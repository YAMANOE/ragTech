"""
pipeline/exporter.py
---------------------
Exports structured pipeline data to relational CSVs and graph-ready CSVs.

Relational package (exports/relational/):
  documents.csv, versions.csv, sections.csv, entities.csv,
  topics.csv, document_topics.csv, document_entities.csv,
  document_relationships.csv, section_compliance_flags.csv

Graph package (exports/graph/):
  Node files:
    nodes_documents.csv, nodes_versions.csv, nodes_sections.csv,
    nodes_entities.csv, nodes_topics.csv
  Edge files:
    edges_has_version.csv, edges_has_section.csv, edges_issued_by.csv,
    edges_applies_to.csv, edges_has_topic.csv,
    edges_amends.csv, edges_repeals.csv, edges_based_on.csv,
    edges_refers_to.csv, edges_implements.csv,
    edges_supplements.csv, edges_supersedes.csv

  Future-ready edge headers (empty, Layer 2):
    future/edges_future_has_obligation.csv
    future/edges_future_has_prohibition.csv
    future/edges_future_has_exception.csv
    future/edges_future_requires_approval.csv
    future/edges_future_has_deadline.csv
    future/edges_future_requires_reporting.csv

CSV format:
  - Delimiter: | (pipe) — configured in Settings
  - Encoding:  utf-8-sig (BOM for Excel Arabic compat)
  - NULL:      empty string
  - Booleans:  true/false strings
  - Arrays:    JSON strings

Usage:
    from pipeline.exporter import LegislationExporter
    from config.settings import Settings

    exporter = LegislationExporter(Settings())
    exporter.export_all()   # reads all structured docs and exports
    exporter.export_result(pipeline_result)  # export single result immediately
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import Settings
from models.schema import (
    Document, DocumentRelationship, DocumentVersion, Entity, EntityRole,
    PipelineResult, RelType, Section, Topic, TopicAssignment,
)
from utils.id_generator import IDGenerator as IDG


class LegislationExporter:
    """
    Produces all relational and graph CSV exports from structured data.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_directories()

        logger.add(
            settings.LOGS_DIR / "exporter.log",
            rotation="10 MB",
            level="INFO",
            encoding="utf-8",
        )

    # ── Main entry points ─────────────────────────────────────────────────────

    def export_result(self, result: PipelineResult) -> None:
        """Export a single PipelineResult. Appends to existing CSV files."""
        if not result.success or not result.document:
            logger.warning(f"[Exporter] Skipping failed result: {result.doc_slug}")
            return

        logger.info(f"[Exporter] Exporting: {result.doc_slug}")

        # Relational
        self._append_csv("documents",   [result.document.to_dict()],  _REL_DOCUMENT_FIELDS)
        self._append_csv("versions",    [v.to_dict() for v in result.versions], _REL_VERSION_FIELDS)
        self._append_csv("sections",    [s.to_dict() for s in result.sections], _REL_SECTION_FIELDS)
        self._append_csv("entities",    [e.to_dict() for e in result.entities], _REL_ENTITY_FIELDS)
        self._append_csv("topics",      [_topic_from_assignment(a) for a in result.topic_assignments], _REL_TOPIC_FIELDS)
        self._append_csv("document_topics",    [a.to_dict() for a in result.topic_assignments], _REL_DOC_TOPIC_FIELDS)
        self._append_csv("document_relationships", [r.to_dict() for r in result.relationships], _REL_REL_FIELDS)
        self._append_csv("section_compliance_flags", self._compliance_rows(result.sections), _REL_COMPLIANCE_FIELDS)

        # Graph nodes
        self._append_graph_csv("nodes_documents", [_graph_doc(result.document)], _GRAPH_NODE_DOC_FIELDS)
        for v in result.versions:
            self._append_graph_csv("nodes_versions", [_graph_version(v)], _GRAPH_NODE_VER_FIELDS)
        self._append_graph_csv("nodes_sections", [_graph_section(s) for s in result.sections], _GRAPH_NODE_SEC_FIELDS)
        self._append_graph_csv("nodes_entities", [_graph_entity(e) for e in result.entities], _GRAPH_NODE_ENT_FIELDS)
        self._append_graph_csv("nodes_topics",   [_graph_topic_node(a) for a in result.topic_assignments], _GRAPH_NODE_TOPIC_FIELDS)

        # Graph edges
        self._export_graph_edges(result)

        logger.success(f"[Exporter] Exported: {result.doc_slug}")

    def export_all(self) -> None:
        """
        Re-export all structured documents from data/structured/docs/*.json.
        Clears existing CSV files first (full rebuild).
        """
        structured_docs = list(self.settings.STRUCTURED_DOCS_DIR.glob("*.json"))
        if not structured_docs:
            logger.warning("[Exporter] No structured documents found in structured/docs/")
            return

        logger.info(f"[Exporter] Full export of {len(structured_docs)} documents")

        # Clear existing export files
        self._clear_exports()

        # Write future-ready empty edge headers
        self._write_future_edge_headers()

        for json_path in sorted(structured_docs):
            with open(json_path, encoding="utf-8") as f:
                raw = json.load(f)
            result = _reconstruct_result_from_json(raw)
            self.export_result(result)

        logger.success(f"[Exporter] Full export complete — {len(structured_docs)} documents")

    # ── Relational CSV helpers ────────────────────────────────────────────────

    def _append_csv(
        self,
        name: str,
        rows: list[dict],
        fields: list[str],
    ) -> None:
        if not rows:
            return
        path = self.settings.RELATIONAL_DIR / f"{name}.csv"
        write_header = not path.exists()
        with open(path, "a", newline="", encoding=self.settings.CSV_ENCODING) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fields,
                delimiter=self.settings.CSV_DELIMITER,
                extrasaction="ignore",
            )
            if write_header:
                writer.writeheader()
            for row in rows:
                writer.writerow(self._safe_row(row, fields))

    def _safe_row(self, row: dict, fields: list[str]) -> dict:
        """Ensure all fields exist and serialize complex types to strings."""
        out = {}
        for f in fields:
            v = row.get(f, "")
            if v is None:
                v = ""
            elif isinstance(v, bool):
                v = str(v).lower()
            elif isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)
            else:
                v = str(v)
            out[f] = v
        return out

    @staticmethod
    def _compliance_rows(sections: list[Section]) -> list[dict]:
        """Extract compliance flag rows from sections."""
        return [
            {
                "section_id": s.section_id,
                "doc_slug":   s.doc_slug,
                "version_id": s.version_id,
                "section_type": s.section_type,
                "section_number": s.section_number or "",
                "compliance_relevant": s.compliance_relevant,
                "contains_obligation": s.contains_obligation,
                "contains_prohibition": s.contains_prohibition,
                "contains_approval_requirement": s.contains_approval_requirement,
                "contains_deadline": s.contains_deadline,
                "contains_exception": s.contains_exception,
                "contains_reporting_requirement": s.contains_reporting_requirement,
                "applicability_targets": s.applicability_targets or "",
                "legal_rules_json": s.legal_rules_json or "",
                "evidence_hints_json": s.evidence_hints_json or "",
            }
            for s in sections
        ]

    # ── Graph CSV helpers ─────────────────────────────────────────────────────

    def _append_graph_csv(
        self,
        name: str,
        rows: list[dict],
        fields: list[str],
    ) -> None:
        if not rows:
            return
        path = self.settings.GRAPH_DIR / f"{name}.csv"
        write_header = not path.exists()
        with open(path, "a", newline="", encoding=self.settings.CSV_ENCODING) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fields,
                delimiter=self.settings.CSV_DELIMITER,
                extrasaction="ignore",
            )
            if write_header:
                writer.writeheader()
            for row in rows:
                writer.writerow(self._safe_row(row, fields))

    def _export_graph_edges(self, result: PipelineResult) -> None:
        """Write all graph edge rows for a PipelineResult."""
        doc = result.document
        if not doc:
            return

        # HAS_VERSION  (doc → version)
        for v in result.versions:
            self._append_graph_csv(
                "edges_has_version",
                [{"start_id": doc.doc_id, "end_id": v.version_id,
                  "type": "HAS_VERSION", "is_current": v.is_current}],
                _GRAPH_EDGE_HAS_VERSION_FIELDS,
            )

        # HAS_SECTION  (version → section)
        for s in result.sections:
            self._append_graph_csv(
                "edges_has_section",
                [{"start_id": s.version_id, "end_id": s.section_id,
                  "type": "HAS_SECTION",
                  "display_order": s.display_order,
                  "section_type": s.section_type}],
                _GRAPH_EDGE_HAS_SECTION_FIELDS,
            )

        # ISSUED_BY  (doc → entity)
        if doc.issuing_entity_id:
            self._append_graph_csv(
                "edges_issued_by",
                [{"start_id": doc.doc_id, "end_id": doc.issuing_entity_id,
                  "type": "ISSUED_BY"}],
                _GRAPH_EDGE_SIMPLE_FIELDS,
            )

        # HAS_TOPIC  (doc → topic)
        for ta in result.topic_assignments:
            self._append_graph_csv(
                "edges_has_topic",
                [{"start_id": ta.doc_id, "end_id": ta.topic_id,
                  "type": "HAS_TOPIC",
                  "is_primary": ta.is_primary,
                  "confidence": ta.confidence}],
                _GRAPH_EDGE_HAS_TOPIC_FIELDS,
            )

        # Relationship edges (AMENDS, REPEALS, BASED_ON, REFERS_TO, …)
        _rel_type_to_file = {
            RelType.AMENDS:      "edges_amends",
            RelType.REPEALS:     "edges_repeals",
            RelType.BASED_ON:    "edges_based_on",
            RelType.REFERS_TO:   "edges_refers_to",
            RelType.IMPLEMENTS:  "edges_implements",
            RelType.SUPPLEMENTS: "edges_supplements",
            RelType.SUPERSEDES:  "edges_supersedes",
        }
        for rel in result.relationships:
            fname = _rel_type_to_file.get(rel.rel_type)
            if fname:
                self._append_graph_csv(
                    fname,
                    [{"start_id": rel.source_doc_id, "end_id": rel.target_doc_id,
                      "type": rel.rel_type,
                      "extracted_text": (rel.extracted_text or "")[:200],
                      "confidence": rel.confidence}],
                    _GRAPH_EDGE_REL_FIELDS,
                )

    # ── Lifecycle helpers ─────────────────────────────────────────────────────

    def _clear_exports(self) -> None:
        """Delete all existing export CSV files before a full rebuild."""
        for p in self.settings.RELATIONAL_DIR.glob("*.csv"):
            p.unlink()
        for p in self.settings.GRAPH_DIR.glob("*.csv"):
            p.unlink()
        logger.info("[Exporter] Cleared existing export files")

    def _write_future_edge_headers(self) -> None:
        """Write header-only files for future Layer 2 graph edges."""
        future_edges = {
            "edges_future_has_obligation":     [":START_ID", ":END_ID", ":TYPE", "obligation_text", "target_entity"],
            "edges_future_has_prohibition":    [":START_ID", ":END_ID", ":TYPE", "prohibition_text"],
            "edges_future_has_exception":      [":START_ID", ":END_ID", ":TYPE", "exception_condition"],
            "edges_future_requires_approval":  [":START_ID", ":END_ID", ":TYPE", "approving_entity"],
            "edges_future_has_deadline":       [":START_ID", ":END_ID", ":TYPE", "deadline_text", "days"],
            "edges_future_requires_reporting": [":START_ID", ":END_ID", ":TYPE", "reporting_entity", "frequency"],
        }
        for fname, fields in future_edges.items():
            path = self.settings.GRAPH_FUTURE_DIR / f"{fname}.csv"
            if not path.exists():
                with open(path, "w", newline="", encoding=self.settings.CSV_ENCODING) as f:
                    writer = csv.DictWriter(
                        f, fieldnames=fields, delimiter=self.settings.CSV_DELIMITER
                    )
                    writer.writeheader()
        logger.info("[Exporter] Future edge header files written")


# ─────────────────────────────────────────────────────────────────────────────
# Field lists — define exact column order for every output file
# ─────────────────────────────────────────────────────────────────────────────

_REL_DOCUMENT_FIELDS = [
    "doc_id", "doc_slug", "title_ar", "title_en", "doc_type",
    "doc_number", "issue_year", "issuing_entity_id", "issuing_entity_name_ar",
    "official_gazette_number", "publication_date", "effective_date", "repeal_date",
    "status", "status_normalized", "source_status_text", "legal_basis_text",
    "applicability_scope", "applicability_sectors", "applicability_entities",
    "source_url", "fetch_date", "raw_html_path", "raw_text_path",
    "has_attachment", "needs_review", "notes",
]

_REL_VERSION_FIELDS = [
    "version_id", "doc_id", "doc_slug", "version_number", "version_type",
    "effective_from", "effective_to", "is_current",
    "amendment_doc_id", "amendment_doc_slug",
    "full_text_original", "full_text_normalized",
    "source_url", "version_notes",
]

_REL_SECTION_FIELDS = [
    "section_id", "version_id", "doc_id", "doc_slug",
    "section_type", "section_number", "section_label",
    "parent_section_id", "display_order", "word_count",
    "original_text", "normalized_text",
]

_REL_COMPLIANCE_FIELDS = [
    "section_id", "doc_slug", "version_id",
    "section_type", "section_number",
    "compliance_relevant", "contains_obligation", "contains_prohibition",
    "contains_approval_requirement", "contains_deadline",
    "contains_exception", "contains_reporting_requirement",
    "applicability_targets", "legal_rules_json", "evidence_hints_json",
]

_REL_ENTITY_FIELDS = [
    "entity_id", "entity_slug", "entity_name_ar", "entity_name_en",
    "entity_type", "parent_entity_id", "notes",
]

_REL_TOPIC_FIELDS = [
    "topic_id", "topic_slug", "topic_name_ar", "topic_name_en",
    "parent_topic_id", "topic_level", "description",
]

_REL_DOC_TOPIC_FIELDS = [
    "assignment_id", "doc_id", "topic_id", "topic_name_ar",
    "is_primary", "confidence", "extraction_method", "matched_keywords",
]

_REL_REL_FIELDS = [
    "rel_id", "source_doc_id", "source_doc_slug",
    "target_doc_id", "target_doc_slug",
    "rel_type", "source_article_ref", "target_article_ref",
    "extracted_text", "confidence", "extraction_method", "notes",
]

# Graph node fields (Neo4j import format)
_GRAPH_NODE_DOC_FIELDS    = [":ID(doc)", "doc_slug", "title_ar", "doc_type", "issue_year", "status", "source_url", ":LABEL"]
_GRAPH_NODE_VER_FIELDS    = [":ID(version)", "version_id", "doc_slug", "version_number", "version_type", "is_current", "effective_from", "effective_to", ":LABEL"]
_GRAPH_NODE_SEC_FIELDS    = [":ID(section)", "section_id", "doc_slug", "section_type", "section_number", "section_label", "display_order", "word_count", ":LABEL"]
_GRAPH_NODE_ENT_FIELDS    = [":ID(entity)", "entity_id", "entity_slug", "entity_name_ar", "entity_type", ":LABEL"]
_GRAPH_NODE_TOPIC_FIELDS  = [":ID(topic)", "topic_id", "topic_slug", "topic_name_ar", "topic_level", ":LABEL"]

# Graph edge fields
_GRAPH_EDGE_HAS_VERSION_FIELDS  = [":START_ID(doc)", ":END_ID(version)", ":TYPE", "is_current"]
_GRAPH_EDGE_HAS_SECTION_FIELDS  = [":START_ID(version)", ":END_ID(section)", ":TYPE", "display_order", "section_type"]
_GRAPH_EDGE_SIMPLE_FIELDS       = [":START_ID(doc)", ":END_ID(entity)", ":TYPE"]
_GRAPH_EDGE_HAS_TOPIC_FIELDS    = [":START_ID(doc)", ":END_ID(topic)", ":TYPE", "is_primary", "confidence"]
_GRAPH_EDGE_REL_FIELDS          = [":START_ID(doc)", ":END_ID(doc)", ":TYPE", "extracted_text", "confidence"]


# ─────────────────────────────────────────────────────────────────────────────
# Record → graph row converters
# ─────────────────────────────────────────────────────────────────────────────

def _graph_doc(doc: Document) -> dict:
    return {
        ":ID(doc)": doc.doc_id, "doc_slug": doc.doc_slug,
        "title_ar": doc.title_ar, "doc_type": doc.doc_type,
        "issue_year": doc.issue_year or "", "status": doc.status,
        "source_url": doc.source_url, ":LABEL": "Document",
    }

def _graph_version(v: DocumentVersion) -> dict:
    return {
        ":ID(version)": v.version_id, "version_id": v.version_id,
        "doc_slug": v.doc_slug, "version_number": v.version_number,
        "version_type": v.version_type, "is_current": str(v.is_current).lower(),
        "effective_from": v.effective_from or "",
        "effective_to": v.effective_to or "", ":LABEL": "Version",
    }

def _graph_section(s: Section) -> dict:
    return {
        ":ID(section)": s.section_id, "section_id": s.section_id,
        "doc_slug": s.doc_slug, "section_type": s.section_type,
        "section_number": s.section_number or "",
        "section_label": s.section_label or "",
        "display_order": s.display_order,
        "word_count": s.word_count, ":LABEL": "Section",
    }

def _graph_entity(e: Entity) -> dict:
    return {
        ":ID(entity)": e.entity_id, "entity_id": e.entity_id,
        "entity_slug": e.entity_slug, "entity_name_ar": e.entity_name_ar,
        "entity_type": e.entity_type, ":LABEL": "Entity",
    }

def _graph_topic_node(ta: TopicAssignment) -> dict:
    return {
        ":ID(topic)": ta.topic_id, "topic_id": ta.topic_id,
        "topic_slug": ta.topic_id.replace("topic-", ""),
        "topic_name_ar": ta.topic_name_ar, "topic_level": 1, ":LABEL": "Topic",
    }

def _topic_from_assignment(ta: TopicAssignment) -> dict:
    return {
        "topic_id": ta.topic_id,
        "topic_slug": ta.topic_id.replace("topic-", ""),
        "topic_name_ar": ta.topic_name_ar,
        "topic_name_en": "",
        "parent_topic_id": "",
        "topic_level": 1,
        "description": "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# JSON → PipelineResult reconstruction (for export_all)
# ─────────────────────────────────────────────────────────────────────────────

def _reconstruct_result_from_json(raw: dict) -> PipelineResult:
    """
    Reconstruct a PipelineResult from a saved structured JSON file.
    Used by export_all() to re-export from disk.
    """
    from models.schema import (
        Document, DocumentVersion, Section, Entity, TopicAssignment,
        DocumentRelationship, PipelineResult,
    )

    def _to_doc(d: dict) -> Document:
        d2 = {k: v for k, v in d.items() if k in Document.__dataclass_fields__}
        return Document(**d2)

    def _to_ver(d: dict) -> DocumentVersion:
        d2 = {k: v for k, v in d.items() if k in DocumentVersion.__dataclass_fields__}
        return DocumentVersion(**d2)

    def _to_sec(d: dict) -> Section:
        d2 = {k: v for k, v in d.items() if k in Section.__dataclass_fields__}
        return Section(**d2)

    def _to_ent(d: dict) -> Entity:
        d2 = {k: v for k, v in d.items() if k in Entity.__dataclass_fields__}
        return Entity(**d2)

    def _to_ta(d: dict) -> TopicAssignment:
        d2 = {k: v for k, v in d.items() if k in TopicAssignment.__dataclass_fields__}
        return TopicAssignment(**d2)

    def _to_rel(d: dict) -> DocumentRelationship:
        d2 = {k: v for k, v in d.items() if k in DocumentRelationship.__dataclass_fields__}
        return DocumentRelationship(**d2)

    return PipelineResult(
        doc_slug=raw["doc_slug"],
        success=True,
        document=_to_doc(raw.get("document", {})),
        versions=[_to_ver(v) for v in raw.get("versions", [])],
        sections=[_to_sec(s) for s in raw.get("sections", [])],
        entities=[_to_ent(e) for e in raw.get("entities", [])],
        topic_assignments=[_to_ta(t) for t in raw.get("topic_assignments", [])],
        relationships=[_to_rel(r) for r in raw.get("relationships", [])],
    )
