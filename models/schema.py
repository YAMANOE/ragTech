"""
models/schema.py
----------------
All dataclasses for the Jordanian legislative intelligence pipeline.

Design principles:
  - Every record type is a plain Python dataclass (serializable to dict/JSON)
  - Stable string IDs (doc_slug, version_id, section_id) used throughout
  - UUID fields are deterministic (UUID5) so re-runs don't create duplicates
  - Compliance-ready fields on Section are NULL now; Layer 2 will populate them
  - Both original_text and normalized_text are preserved on every text-bearing record
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, List
import json


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations (plain string constants — no Enum overhead for JSON serialization)
# ─────────────────────────────────────────────────────────────────────────────

class DocType:
    CONSTITUTION = "constitution"
    LAW          = "law"
    REGULATION   = "regulation"
    INSTRUCTION  = "instruction"
    DECISION     = "decision"
    CIRCULAR     = "circular"
    AGREEMENT    = "agreement"
    TREATY       = "treaty"
    ROYAL_DECREE = "royal_decree"
    ROYAL_ORDER  = "royal_order"
    ROYAL_WILL   = "royal_will"
    DECLARATION  = "declaration"
    UNKNOWN      = "unknown"


class DocStatus:
    ACTIVE   = "active"
    AMENDED  = "amended"
    REPEALED = "repealed"
    DRAFT    = "draft"
    PENDING  = "pending"   # Referenced but not yet fetched


class VersionType:
    ORIGINAL     = "original"
    AMENDMENT    = "amendment"
    CONSOLIDATED = "consolidated"


class SectionType:
    PREAMBLE = "preamble"
    PART     = "part"        # جزء / باب
    CHAPTER  = "chapter"     # فصل / قسم
    ARTICLE  = "article"     # مادة
    PARAGRAPH = "paragraph"  # فقرة (أ، ب، ج)
    CLAUSE   = "clause"      # بند فرعي
    ANNEX    = "annex"       # ملحق / جدول
    TITLE    = "title"       # عنوان قسم داخل النص


class RelType:
    AMENDS      = "AMENDS"
    REPEALS     = "REPEALS"
    BASED_ON    = "BASED_ON"
    IMPLEMENTS  = "IMPLEMENTS"
    SUPPLEMENTS = "SUPPLEMENTS"
    SUPERSEDES  = "SUPERSEDES"
    REFERS_TO   = "REFERS_TO"
    APPLIES_TO  = "APPLIES_TO"


class EntityType:
    MINISTRY   = "ministry"
    AUTHORITY  = "authority"
    COUNCIL    = "council"
    COURT      = "court"
    DEPARTMENT = "department"
    COMPANY    = "company"
    PERSON     = "person"
    OTHER      = "other"


class ApplicabilityScope:
    GENERAL          = "general"
    GOVERNMENT_WIDE  = "government_wide"
    SECTOR_SPECIFIC  = "sector_specific"
    ENTITY_SPECIFIC  = "entity_specific"
    INTERNAL         = "internal"
    UNKNOWN          = "unknown"


class ExtractionMethod:
    RULE_BASED = "rule_based"
    KEYWORD    = "keyword"
    MANUAL     = "manual"
    MODEL      = "model"    # future: NLP model


# ─────────────────────────────────────────────────────────────────────────────
# Core Records
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Document:
    """
    Represents the identity of a legislation document, independent of versions.
    One row in exports/relational/documents.csv and nodes_documents.csv.
    """
    # ── Required identifiers ──────────────────────────────────────────────
    doc_id: str                          # Deterministic UUID5 from doc_slug
    doc_slug: str                        # e.g. "law-2014-34" — stable key

    # ── Core metadata ────────────────────────────────────────────────────
    title_ar: str                        # Full Arabic title, exactly as on LOB
    title_en: Optional[str] = None       # English title (manual or translated)
    doc_type: str = DocType.UNKNOWN      # See DocType constants
    doc_number: Optional[str] = None     # "34" or "رقم (34)"
    issue_year: Optional[int] = None     # Gregorian year

    # ── Issuing entity ───────────────────────────────────────────────────
    issuing_entity_id: Optional[str] = None        # FK to Entity.entity_id
    issuing_entity_name_ar: Optional[str] = None   # Denormalized copy

    # ── Publication details ──────────────────────────────────────────────
    official_gazette_number: Optional[str] = None  # عدد الجريدة الرسمية
    publication_date: Optional[str] = None          # ISO 8601 date
    effective_date: Optional[str] = None            # ISO 8601 date
    repeal_date: Optional[str] = None               # ISO 8601 date, null if active

    # ── Status ───────────────────────────────────────────────────────────
    status: str = DocStatus.ACTIVE   # See DocStatus constants

    # ── Legal basis ──────────────────────────────────────────────────────
    legal_basis_text: Optional[str] = None   # Full extracted استناداً clause

    # ── Applicability ────────────────────────────────────────────────────
    applicability_scope: str = ApplicabilityScope.UNKNOWN
    applicability_sectors: List[str] = field(default_factory=list)   # e.g. ["health", "education"]
    applicability_entities: List[str] = field(default_factory=list)  # named entities in scope

    # ── Source provenance ────────────────────────────────────────────────
    source_url: str = ""
    fetch_date: Optional[str] = None        # ISO 8601 datetime UTC
    raw_html_path: str = ""
    raw_text_path: str = ""
    clean_json_path: str = ""

    # ── Flags ────────────────────────────────────────────────────────────
    needs_review: bool = False   # Flagged for manual check
    has_attachment: bool = False # Has linked PDF or annex
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["applicability_sectors"] = json.dumps(d["applicability_sectors"], ensure_ascii=False)
        d["applicability_entities"] = json.dumps(d["applicability_entities"], ensure_ascii=False)
        return d


@dataclass
class DocumentVersion:
    """
    A specific version of a Document at a point in time.
    Original law = version 1. Each amendment creates a new version.
    One row in exports/relational/versions.csv and nodes_versions.csv.
    """
    version_id: str          # e.g. "law-2014-34-v1"
    doc_id: str              # FK to Document.doc_id
    doc_slug: str            # Denormalized

    version_number: int = 1
    version_type: str = VersionType.ORIGINAL    # See VersionType constants

    effective_from: Optional[str] = None   # ISO 8601 date
    effective_to: Optional[str] = None     # ISO 8601 date (null = current)
    is_current: bool = True

    # ── The amending document (if this is an amendment version) ──────────
    amendment_doc_id: Optional[str] = None      # Document that caused this version
    amendment_doc_slug: Optional[str] = None    # Denormalized

    # ── Full text ────────────────────────────────────────────────────────
    full_text_original: str = ""      # Exact text as extracted
    full_text_normalized: str = ""    # Cleaned / normalized for search

    source_url: str = ""
    version_notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Section:
    """
    A single unit within a DocumentVersion: preamble, part, chapter, article,
    paragraph, clause, or annex.

    Compliance-ready fields (contains_*, applicability_targets, legal_rules_json,
    evidence_hints_json) are stored as NULL now and will be populated by Layer 2.

    One row in exports/relational/sections.csv and nodes_sections.csv.
    """
    section_id: str          # e.g. "law-2014-34-v1-art-0003"
    version_id: str          # FK to DocumentVersion.version_id
    doc_id: str              # FK to Document.doc_id
    doc_slug: str            # Denormalized

    # ── Section structure ────────────────────────────────────────────────
    section_type: str = SectionType.ARTICLE            # See SectionType constants
    section_number: Optional[str] = None               # "1", "أ", "ثانياً"
    section_label: Optional[str] = None                # "المادة (1)", "الفصل الأول"
    parent_section_id: Optional[str] = None            # FK to parent Section
    display_order: int = 0                             # Global position in version

    # ── Text content ────────────────────────────────────────────────────
    original_text: str = ""       # NEVER modified after extraction
    normalized_text: str = ""     # Cleaned for search/parsing
    word_count: int = 0

    # ── Compliance-readiness (Layer 2 fields — NULL until populated) ────
    compliance_relevant: Optional[bool] = None
    contains_obligation: Optional[bool] = None
    contains_prohibition: Optional[bool] = None
    contains_approval_requirement: Optional[bool] = None
    contains_deadline: Optional[bool] = None
    contains_exception: Optional[bool] = None
    contains_reporting_requirement: Optional[bool] = None

    # ── Applicability at section level (JSON strings) ───────────────────
    applicability_targets: Optional[str] = None   # JSON array string
    legal_rules_json: Optional[str] = None        # JSON: future rule extraction
    evidence_hints_json: Optional[str] = None     # JSON: future evidence hints

    def to_dict(self) -> dict:
        return asdict(self)

    def word_count_from_text(self) -> int:
        """Compute word count from original_text."""
        return len(self.original_text.split())


@dataclass
class Entity:
    """
    A named institutional entity extracted from legislation.
    Ministries, authorities, councils, courts, departments, etc.
    One row in exports/relational/entities.csv and nodes_entities.csv.
    """
    entity_id: str          # e.g. "entity-ministry-finance"
    entity_slug: str
    entity_name_ar: str              # Arabic name as found in text
    entity_name_en: Optional[str] = None
    entity_type: str = EntityType.OTHER  # See EntityType constants
    parent_entity_id: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EntityRole:
    """
    Junction table: role of an entity in relation to a document.
    Maps to exports/relational/document_entities.csv.
    Also used to generate ISSUED_BY and APPLIES_TO graph edges.
    """
    role_id: str
    doc_id: str
    entity_id: str
    entity_name_ar: str    # Denormalized
    role: str              # "issuer", "target", "mentioned"
    source_section_id: Optional[str] = None    # Which section named this entity
    extracted_text: Optional[str] = None
    extraction_method: str = ExtractionMethod.RULE_BASED

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Topic:
    """
    A legal topic category from the taxonomy in config/topics.yaml.
    One row in exports/relational/topics.csv and nodes_topics.csv.
    """
    topic_id: str           # e.g. "topic-tax-financial-law"
    topic_slug: str
    topic_name_ar: str
    topic_name_en: Optional[str] = None
    parent_topic_id: Optional[str] = None
    topic_level: int = 1    # 1 = primary, 2 = sub-category
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TopicAssignment:
    """
    Junction: a topic assigned to a document.
    One row in exports/relational/document_topics.csv.
    Also generates HAS_TOPIC graph edge.
    """
    assignment_id: str
    doc_id: str
    topic_id: str
    topic_name_ar: str       # Denormalized
    is_primary: bool = False
    confidence: float = 0.0
    extraction_method: str = ExtractionMethod.KEYWORD
    matched_keywords: Optional[str] = None   # JSON array of matched terms

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocumentRelationship:
    """
    A directed relationship between two legislation documents.
    AMENDS, REPEALS, BASED_ON, IMPLEMENTS, SUPPLEMENTS, SUPERSEDES, REFERS_TO.
    One row in exports/relational/document_relationships.csv.
    Also generates type-specific graph edge files.
    """
    rel_id: str
    source_doc_id: str
    source_doc_slug: str    # Denormalized
    target_doc_id: str      # May be a stub (status=pending) if not yet fetched
    target_doc_slug: str    # Denormalized

    rel_type: str           # See RelType constants

    source_article_ref: Optional[str] = None    # "Article 5 of source"
    target_article_ref: Optional[str] = None    # "Article 93 of target"
    extracted_text: Optional[str] = None        # The raw clause text
    confidence: float = 1.0
    extraction_method: str = ExtractionMethod.RULE_BASED
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SectionRelationship:
    """
    Cross-reference between sections (e.g., "see Article 7" within a law).
    For future use — stored in structured data but not exported yet.
    """
    rel_id: str
    source_section_id: str
    target_section_id: Optional[str] = None     # Null if unresolved
    target_ref_text: Optional[str] = None        # Raw reference string
    rel_type: str = RelType.REFERS_TO
    confidence: float = 0.8

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline support records
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FetchRecord:
    """
    One row in data/raw/source_registry.csv.
    Written by the Fetcher after each successful or failed fetch.
    """
    fetch_id: str
    doc_slug: str
    source_url: str
    fetch_timestamp: str      # ISO 8601 UTC
    http_status: Optional[int] = None
    page_title: str = ""
    html_file_path: str = ""
    text_file_path: str = ""
    fetch_notes: str = ""     # Errors or warnings

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CleanOutput:
    """
    Stored as data/clean/{doc_slug}_clean.json.
    Carries both original and normalized text + full cleaning log.
    """
    doc_slug: str
    source_file: str          # Path to raw text file used as input
    cleaned_at: str           # ISO 8601 UTC
    original_text: str        # Exactly as extracted from raw HTML
    normalized_text: str      # After all cleaning rules
    cleaning_rules_applied: List[str] = field(default_factory=list)
    cleaning_log: List[dict] = field(default_factory=list)   # Per-rule stats

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationResult:
    """Result of a single validation check."""
    check_name: str
    passed: bool
    doc_slug: Optional[str] = None
    record_id: Optional[str] = None
    detail: str = ""
    severity: str = "error"    # "error" | "warning" | "info"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineResult:
    """
    Summary object returned by scripts/run_pipeline.py for one document.
    """
    doc_slug: str
    success: bool
    fetch_record: Optional[FetchRecord] = None
    clean_output: Optional[CleanOutput] = None
    document: Optional[Document] = None
    versions: List[DocumentVersion] = field(default_factory=list)
    sections: List[Section] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)
    topic_assignments: List[TopicAssignment] = field(default_factory=list)
    relationships: List[DocumentRelationship] = field(default_factory=list)
    validation_results: List[ValidationResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def validation_passed(self) -> bool:
        return all(r.passed or r.severity != "error" for r in self.validation_results)

    def summary(self) -> dict:
        return {
            "doc_slug": self.doc_slug,
            "success": self.success,
            "sections": len(self.sections),
            "entities": len(self.entities),
            "topics": len(self.topic_assignments),
            "relationships": len(self.relationships),
            "validation_passed": self.validation_passed,
            "errors": self.errors,
        }
