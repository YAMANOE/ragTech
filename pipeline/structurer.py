"""
pipeline/structurer.py
-----------------------
Assembles a complete StructuredDocument from parsed + cleaned data.

Responsibilities:
  1. Build Document record with full metadata
  2. Build DocumentVersion records (version 1 = original by default)
  3. Assign stable IDs to all sections; resolve parent-child hierarchy
  4. Clean section text (original + normalized)
  5. Scan each section for compliance-readiness flags (Layer 2 prep)
  6. Extract and deduplicate Entity records
  7. Classify topics (rule-based keyword matching against topics.yaml)
  8. Detect legal relationships (BASED_ON, AMENDS, REFERS_TO, etc.)
  9. Classify applicability scope
  10. Save structured data to data/structured/docs/{doc_slug}.json

No ML model is used at this stage. All extraction is rule-based.
Topic classification uses keyword matching from config/topics.yaml.

Usage:
    from pipeline.structurer import LegislationStructurer
    from config.settings import Settings
    from pipeline.cleaner import ArabicTextCleaner

    cleaner    = ArabicTextCleaner(settings)
    structurer = LegislationStructurer(settings)

    clean_out  = cleaner.clean(raw_text, doc_slug)
    parsed_doc = parser.parse_html(raw_html, doc_slug)
    result     = structurer.structure(parsed_doc, clean_out)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from config.settings import Settings
from models.schema import (
    ApplicabilityScope, CleanOutput, Document, DocumentRelationship,
    DocumentVersion, Entity, EntityRole, EntityType, ExtractionMethod,
    PipelineResult, RelType, Section, SectionType, Topic, TopicAssignment,
    VersionType, DocStatus,
)
from utils.arabic_utils import ArabicTextUtils as ATU
from utils.id_generator import IDGenerator as IDG
from pipeline.cleaner import ArabicTextCleaner
from pipeline.parser import ParsedSection


class LegislationStructurer:
    """
    Assembles complete structured PipelineResult from parser + cleaner outputs.
    Entry point: structure()
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._topics: list[dict] = self._load_topics()
        self._entity_registry: dict[str, Entity] = {}   # entity_id → Entity
        self._cleaner = ArabicTextCleaner(settings)

        logger.add(
            settings.LOGS_DIR / "structurer.log",
            rotation="10 MB",
            level="INFO",
            encoding="utf-8",
        )

    # ── Main entry point ──────────────────────────────────────────────────────

    def structure(
        self,
        parsed_doc: dict,
        clean_output: CleanOutput,
    ) -> PipelineResult:
        """
        Build a complete PipelineResult from:
          - parsed_doc:   output of LOBParser.parse_html()
          - clean_output: output of ArabicTextCleaner.clean()

        Saves structured JSON to data/structured/docs/{doc_slug}.json.
        """
        doc_slug = parsed_doc["doc_slug"]
        metadata = parsed_doc["metadata"]
        source_url = parsed_doc["source_url"]
        parsed_sections: list[ParsedSection] = parsed_doc["sections"]

        logger.info(f"[Structurer] Building structure for: {doc_slug}")

        # ── 1. Build Document ─────────────────────────────────────────────
        document = self._build_document(
            doc_slug, metadata, source_url, clean_output
        )

        # ── 2. Build Version (default: version 1, original) ───────────────
        version = self._build_version(document, clean_output, source_url)

        # ── 3. Build Sections ─────────────────────────────────────────────
        sections, section_order_map = self._build_sections(
            parsed_sections, version, document
        )

        # ── 4. Extract Entities ───────────────────────────────────────────
        entities, entity_roles = self._extract_entities(
            document, clean_output.original_text
        )
        if entities:
            document.issuing_entity_id   = entities[0].entity_id
            document.issuing_entity_name_ar = entities[0].entity_name_ar

        # ── 5. Classify Topics ────────────────────────────────────────────
        topic_records, topic_assignments = self._classify_topics(
            document, clean_output.normalized_text
        )

        # ── 6. Detect Relationships ───────────────────────────────────────
        relationships = self._detect_relationships(
            document, clean_output.original_text, parsed_doc.get("parse_notes", [])
        )

        # ── 7. Classify Scope ─────────────────────────────────────────────
        document.applicability_scope = ATU.classify_scope(
            clean_output.normalized_text[:1000]
        )

        # ── 8. Assemble result ────────────────────────────────────────────
        result = PipelineResult(
            doc_slug=doc_slug,
            success=True,
            clean_output=clean_output,
            document=document,
            versions=[version],
            sections=sections,
            entities=list(self._entity_registry.values()),
            topic_assignments=topic_assignments,
            relationships=relationships,
        )

        # ── 9. Persist ────────────────────────────────────────────────────
        self._save_structured(result)

        logger.success(
            f"[Structurer] {doc_slug} — {len(sections)} sections, "
            f"{len(entities)} entities, {len(topic_assignments)} topics, "
            f"{len(relationships)} relationships"
        )

        return result

    # ── Document builder ──────────────────────────────────────────────────────

    def _build_document(
        self,
        doc_slug: str,
        metadata: dict,
        source_url: str,
        clean_output: CleanOutput,
    ) -> Document:
        """Build a Document record from extracted metadata."""
        doc_id = IDG.doc_id(doc_slug)

        # Derive clean_json_path
        clean_json_path = str(
            self.settings.CLEAN_DIR / f"{doc_slug}_clean.json"
        )

        return Document(
            doc_id=doc_id,
            doc_slug=doc_slug,
            title_ar=metadata.get("title_ar", ""),
            doc_type=metadata.get("doc_type", "unknown"),
            doc_number=metadata.get("doc_number"),
            issue_year=metadata.get("issue_year"),
            issuing_entity_name_ar=metadata.get("issuing_entity_name_ar"),
            official_gazette_number=metadata.get("official_gazette_number"),
            publication_date=metadata.get("publication_date"),
            effective_date=metadata.get("effective_date"),
            status=metadata.get("status", DocStatus.ACTIVE),
            legal_basis_text=metadata.get("legal_basis_text"),
            source_url=source_url,
            fetch_date=clean_output.cleaned_at,
            raw_html_path="",    # populated upstream
            raw_text_path=clean_output.source_file,
            clean_json_path=clean_json_path,
        )

    # ── Version builder ───────────────────────────────────────────────────────

    def _build_version(
        self,
        document: Document,
        clean_output: CleanOutput,
        source_url: str,
    ) -> DocumentVersion:
        """Build the initial version (version 1 = original) for a document."""
        version_id = IDG.version_id(document.doc_slug, 1)
        return DocumentVersion(
            version_id=version_id,
            doc_id=document.doc_id,
            doc_slug=document.doc_slug,
            version_number=1,
            version_type=VersionType.ORIGINAL,
            effective_from=document.effective_date or document.publication_date,
            effective_to=None,
            is_current=True,
            full_text_original=clean_output.original_text,
            full_text_normalized=clean_output.normalized_text,
            source_url=source_url,
        )

    # ── Section builder ───────────────────────────────────────────────────────

    def _build_sections(
        self,
        parsed_sections: list[ParsedSection],
        version: DocumentVersion,
        document: Document,
    ) -> tuple[list[Section], dict[int, str]]:
        """
        Build Section records from ParsedSection list.
        Resolves parent-child references using display_order → section_id map.

        Returns:
          (sections_list, order_to_section_id_map)
        """
        # First pass: assign section_ids
        order_to_id: dict[int, str] = {}
        for ps in parsed_sections:
            sid = IDG.section_id(version.version_id, ps.section_type, ps.display_order)
            order_to_id[ps.display_order] = sid

        # Second pass: build Section objects
        sections: list[Section] = []
        for ps in parsed_sections:
            sid = order_to_id[ps.display_order]
            parent_sid = order_to_id.get(ps.parent_order) if ps.parent_order else None

            # Clean section text
            orig_text, norm_text = self._cleaner.clean_section_text(ps.raw_text)

            # Scan compliance flags
            flags = ATU.scan_compliance_flags(norm_text)

            sec = Section(
                section_id=sid,
                version_id=version.version_id,
                doc_id=document.doc_id,
                doc_slug=document.doc_slug,
                section_type=ps.section_type,
                section_number=ps.section_number,
                section_label=ps.section_label,
                parent_section_id=parent_sid,
                display_order=ps.display_order,
                original_text=orig_text,
                normalized_text=norm_text,
                word_count=ATU.count_words(orig_text),
                compliance_relevant=flags["compliance_relevant"],
                contains_obligation=flags["contains_obligation"],
                contains_prohibition=flags["contains_prohibition"],
                contains_approval_requirement=flags["contains_approval_requirement"],
                contains_deadline=flags["contains_deadline"],
                contains_exception=flags["contains_exception"],
                contains_reporting_requirement=flags["contains_reporting_requirement"],
            )
            sections.append(sec)

        return sections, order_to_id

    # ── Entity extraction ─────────────────────────────────────────────────────

    def _extract_entities(
        self,
        document: Document,
        full_text: str,
    ) -> tuple[list[Entity], list[EntityRole]]:
        """
        Extract named entities from the full document text.
        Deduplicates entities globally using entity_id.
        Returns (new_entities, entity_roles).
        """
        raw_entities = ATU.extract_entities(full_text)
        new_entities: list[Entity] = []
        roles: list[EntityRole] = []

        for raw in raw_entities:
            e_slug = IDG.entity_slug(raw["entity_name_ar"], raw["entity_type"])
            e_id   = IDG.entity_id(e_slug)

            if e_id not in self._entity_registry:
                entity = Entity(
                    entity_id=e_id,
                    entity_slug=e_slug,
                    entity_name_ar=raw["entity_name_ar"],
                    entity_type=raw["entity_type"],
                )
                self._entity_registry[e_id] = entity
                new_entities.append(entity)

            # Determine role: first entity in document → issuer candidate
            role = "issuer" if not roles else "mentioned"
            role_id = IDG.entity_role_id(document.doc_id, e_id, role)
            roles.append(EntityRole(
                role_id=role_id,
                doc_id=document.doc_id,
                entity_id=e_id,
                entity_name_ar=raw["entity_name_ar"],
                role=role,
                extracted_text=raw["entity_name_ar"],
                extraction_method=ExtractionMethod.RULE_BASED,
            ))

        return new_entities, roles

    # ── Topic classification ──────────────────────────────────────────────────

    def _classify_topics(
        self,
        document: Document,
        normalized_text: str,
    ) -> tuple[list[Topic], list[TopicAssignment]]:
        """
        Match document against topic taxonomy using weighted keyword scoring.

        Scoring zones (per keyword hit):
          Title match           → +0.40  (very high signal)
          Early text (first 800 chars) → +0.20  (definitions, objectives area)
          Full body match       → +0.05  (lower weight)

        A single title keyword match (0.40) already exceeds the threshold (0.4),
        so any law whose domain word appears in its own title gets a topic.
        """
        title_norm = ATU.normalize(document.title_ar or "")
        early_norm = ATU.normalize(normalized_text[:800])
        body_norm  = ATU.normalize(normalized_text)

        scored: list[tuple[float, dict]] = []

        for topic_def in self._topics:
            keywords: list[str] = topic_def.get("keywords_ar", [])
            if not keywords:
                continue

            confidence = 0.0
            for kw in keywords:
                norm_kw = ATU.normalize(kw)
                if norm_kw in title_norm:
                    confidence += 0.40   # title match = highest weight
                elif norm_kw in early_norm:
                    confidence += 0.20   # early text (defs/objectives) = medium
                elif norm_kw in body_norm:
                    confidence += 0.05   # full body = lower weight

            confidence = round(min(1.0, confidence), 3)
            if confidence >= self.settings.TOPIC_CONFIDENCE_THRESHOLD:
                scored.append((confidence, topic_def))

        # Sort by confidence descending
        scored.sort(key=lambda x: x[0], reverse=True)

        topic_records: list[Topic] = []
        assignments: list[TopicAssignment] = []

        for i, (confidence, td) in enumerate(scored):
            t_slug = td["id"]
            t_id   = IDG.topic_id(t_slug)
            t_uuid = IDG.topic_uuid(t_id)

            topic = Topic(
                topic_id=t_id,
                topic_slug=t_slug,
                topic_name_ar=td.get("name_ar", ""),
                topic_name_en=td.get("name_en"),
                parent_topic_id=IDG.topic_id(td["parent"]) if td.get("parent") else None,
                topic_level=td.get("level", 1),
            )
            topic_records.append(topic)

            assign_id = IDG.topic_assignment_id(document.doc_id, t_id)
            assignments.append(TopicAssignment(
                assignment_id=assign_id,
                doc_id=document.doc_id,
                topic_id=t_id,
                topic_name_ar=td.get("name_ar", ""),
                is_primary=(i == 0),
                confidence=confidence,
                extraction_method=ExtractionMethod.KEYWORD,
                matched_keywords=json.dumps(
                    [kw for kw in td.get("keywords_ar", [])
                     if ATU.normalize(kw) in body_norm],
                    ensure_ascii=False,
                ),
            ))

        return topic_records, assignments

    # ── Relationship detection ────────────────────────────────────────────────

    def _detect_relationships(
        self,
        document: Document,
        original_text: str,
        parse_notes: list[str],
    ) -> list[DocumentRelationship]:
        """
        Detect legal relationships based on text patterns.
        Returns list of DocumentRelationship records.

        Currently detects:
          - BASED_ON (استناداً/بناءً patterns + referenced law)
          - AMENDS (amendment language + referenced law)
          - REPEALS (repeal language + referenced law)
          - REFERS_TO (inline law references)
        """
        relationships: list[DocumentRelationship] = []
        seen_rels: set[str] = set()

        # ── BASED_ON from legal basis clauses ────────────────────────────
        basis_hits = ATU.extract_legal_basis(original_text)
        for hit in basis_hits:
            basis_text = hit["basis_text"]

            # Check for a specific law number reference (e.g. القانون رقم 34 لسنة 2019)
            refs = ATU.extract_cross_references(basis_text)
            for ref in refs:
                target_slug = _make_target_slug(ref)
                self._add_relationship(
                    relationships, seen_rels,
                    source=document,
                    target_slug=target_slug,
                    rel_type=RelType.BASED_ON,
                    extracted_text=basis_text[:300],
                    confidence=0.85,
                )

            # Direct Constitution reference (صادر بمقتضى المادة (31) من الدستور)
            if re.search(r"\u0627\u0644\u062f\u0633\u062a\u0648\u0631", basis_text):
                self._add_relationship(
                    relationships, seen_rels,
                    source=document,
                    target_slug="constitution-hashemite-kingdom-1952",
                    rel_type=RelType.BASED_ON,
                    extracted_text=basis_text[:300],
                    confidence=0.90,
                )

        # ── AMENDS / REPEALS from full text ──────────────────────────────
        all_refs = ATU.extract_cross_references(original_text)
        is_amendment = ATU.detect_amendment(original_text)
        is_repeal    = ATU.detect_repeal(original_text)

        for ref in all_refs:
            target_slug = _make_target_slug(ref)
            if target_slug == document.doc_slug:
                continue    # Skip self-reference

            if is_repeal and len(all_refs) <= 3:
                # Small number of refs in a repeal document → likely the target
                self._add_relationship(
                    relationships, seen_rels,
                    source=document,
                    target_slug=target_slug,
                    rel_type=RelType.REPEALS,
                    extracted_text=ref["raw"],
                    confidence=0.8,
                )
            elif is_amendment and len(all_refs) <= 5:
                self._add_relationship(
                    relationships, seen_rels,
                    source=document,
                    target_slug=target_slug,
                    rel_type=RelType.AMENDS,
                    extracted_text=ref["raw"],
                    confidence=0.8,
                )
            else:
                # Generic reference
                self._add_relationship(
                    relationships, seen_rels,
                    source=document,
                    target_slug=target_slug,
                    rel_type=RelType.REFERS_TO,
                    extracted_text=ref["raw"],
                    confidence=0.6,
                )

        return relationships

    def _add_relationship(
        self,
        relationships: list[DocumentRelationship],
        seen: set[str],
        source: Document,
        target_slug: str,
        rel_type: str,
        extracted_text: str,
        confidence: float,
    ) -> None:
        """Add a relationship if not already present (dedup by source+type+target)."""
        if confidence < self.settings.RELATIONSHIP_CONFIDENCE_THRESHOLD:
            return
        rel_id = IDG.relationship_id(source.doc_slug, rel_type, target_slug)
        if rel_id in seen:
            return
        seen.add(rel_id)

        # target_doc_id: use deterministic UUID (may be a stub / pending)
        target_doc_id = IDG.doc_id(target_slug)

        relationships.append(DocumentRelationship(
            rel_id=rel_id,
            source_doc_id=source.doc_id,
            source_doc_slug=source.doc_slug,
            target_doc_id=target_doc_id,
            target_doc_slug=target_slug,
            rel_type=rel_type,
            extracted_text=extracted_text,
            confidence=confidence,
            extraction_method=ExtractionMethod.RULE_BASED,
        ))

    # ── Topic taxonomy loader ─────────────────────────────────────────────────

    def _load_topics(self) -> list[dict]:
        """Load topic taxonomy from config/topics.yaml."""
        path = self.settings.TOPICS_CONFIG_PATH
        if not path.exists():
            logger.warning(f"[Structurer] Topics config not found: {path}")
            return []
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("topics", [])

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_structured(self, result: PipelineResult) -> None:
        """
        Save the full PipelineResult as a JSON file.
        Path: data/structured/docs/{doc_slug}.json
        """
        path = self.settings.STRUCTURED_DOCS_DIR / f"{result.doc_slug}.json"
        payload = {
            "doc_slug": result.doc_slug,
            "structured_at": datetime.now(timezone.utc).isoformat(),
            "document":   result.document.to_dict() if result.document else {},
            "versions":   [v.to_dict() for v in result.versions],
            "sections":   [s.to_dict() for s in result.sections],
            "entities":   [e.to_dict() for e in result.entities],
            "topic_assignments": [t.to_dict() for t in result.topic_assignments],
            "relationships": [r.to_dict() for r in result.relationships],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.debug(f"[Structurer] Saved structured doc: {path}")

    def load_structured(self, doc_slug: str) -> Optional[dict]:
        """Load a previously saved structured document."""
        path = self.settings.STRUCTURED_DOCS_DIR / f"{doc_slug}.json"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_target_slug(ref: dict) -> str:
    """
    Build a target doc_slug from a cross-reference dict.
    Since we don't know the doc_type from a reference, we use 'doc' as placeholder.
    The actual slug can be corrected manually or during a cross-reference resolution step.
    """
    number = ref.get("doc_number", "")
    year   = ref.get("year", "")
    # We don't know the type from context — use 'doc' as placeholder type
    return f"doc-{year}-{number}"
