"""
utils/id_generator.py
----------------------
Deterministic, stable identifier generation for the pipeline.

Design goals:
  - Same input → same output on every run (no random UUIDs)
  - Human-readable slugs for doc_slug, version_id, section_id
  - Full UUID (UUID5) for database FK columns
  - Namespace isolation per record type

UUID5 uses SHA-1 over (namespace + name), giving deterministic output.
We define separate namespaces per record type to avoid collisions.
"""
from __future__ import annotations

import uuid
import re
from typing import Optional

try:
    from slugify import slugify as _slugify

    def _safe_slug(text: str) -> str:
        """Convert Arabic or mixed text to a URL-safe ASCII slug."""
        return _slugify(text, separator="-", allow_unicode=False, max_length=60)

except ImportError:
    # Fallback: ASCII-only slug without python-slugify
    def _safe_slug(text: str) -> str:  # type: ignore[misc]
        text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
        text = re.sub(r"\s+", "-", text.strip())
        return text[:60].lower()


# ── Namespace UUIDs per record type ─────────────────────────────────────────
_NS_DOCUMENT     = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # DNS namespace
_NS_VERSION      = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
_NS_SECTION      = uuid.UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c8")
_NS_ENTITY       = uuid.UUID("6ba7b813-9dad-11d1-80b4-00c04fd430c8")
_NS_TOPIC        = uuid.UUID("6ba7b814-9dad-11d1-80b4-00c04fd430c8")
_NS_RELATIONSHIP = uuid.UUID("6ba7b815-9dad-11d1-80b4-00c04fd430c8")
_NS_FETCH        = uuid.UUID("6ba7b816-9dad-11d1-80b4-00c04fd430c8")


class IDGenerator:
    """
    Generates all stable identifiers used in the pipeline.

    All methods are static. No instance needed.

    Usage:
        from utils.id_generator import IDGenerator as IDG

        doc_slug  = IDG.doc_slug("law", 2014, "34")
        doc_id    = IDG.doc_id(doc_slug)
        ver_id    = IDG.version_id(doc_slug, 1)
        sec_id    = IDG.section_id(ver_id, "article", 3)
    """

    # ── Document ─────────────────────────────────────────────────────────────

    @staticmethod
    def doc_slug(
        doc_type_en: str,
        year: int,
        number: str,
        title_slug: Optional[str] = None,
    ) -> str:
        """
        Generate a stable human-readable document slug.

        Pattern: {type}-{year}-{number}
                 {type}-{year}-{number}-{title_fragment}  (when number is ambiguous)

        Examples:
            law-2014-34
            regulation-2021-3
            instruction-2019-civil-service  (no number)
        """
        doc_type_en = doc_type_en.lower().replace("_", "-")

        if number and number.strip().isdigit():
            slug = f"{doc_type_en}-{year}-{number.strip()}"
        elif number:
            num_slug = _safe_slug(number)[:20]
            slug = f"{doc_type_en}-{year}-{num_slug}"
        elif title_slug:
            slug = f"{doc_type_en}-{year}-{title_slug[:40]}"
        else:
            slug = f"{doc_type_en}-{year}-unknown"

        return slug

    @staticmethod
    def doc_id(doc_slug: str) -> str:
        """Deterministic UUID for a document, derived from doc_slug."""
        return str(uuid.uuid5(_NS_DOCUMENT, doc_slug))

    # ── Version ───────────────────────────────────────────────────────────────

    @staticmethod
    def version_id(doc_slug: str, version_number: int) -> str:
        """
        Human-readable version identifier.
        Example: law-2014-34-v1
        """
        return f"{doc_slug}-v{version_number}"

    @staticmethod
    def version_uuid(version_id: str) -> str:
        """Deterministic UUID for a version record."""
        return str(uuid.uuid5(_NS_VERSION, version_id))

    # ── Section ───────────────────────────────────────────────────────────────

    @staticmethod
    def section_id(
        version_id: str,
        section_type: str,
        display_order: int,
    ) -> str:
        """
        Human-readable section identifier.
        Example: law-2014-34-v1-art-0003
                 law-2014-34-v1-cha-0001
        """
        # Three-char abbreviations per section type
        type_abbr = {
            "preamble":  "pre",
            "part":      "prt",
            "chapter":   "cha",
            "article":   "art",
            "paragraph": "par",
            "clause":    "cla",
            "annex":     "anx",
            "title":     "ttl",
        }.get(section_type, section_type[:3])

        return f"{version_id}-{type_abbr}-{display_order:04d}"

    @staticmethod
    def section_uuid(section_id: str) -> str:
        """Deterministic UUID for a section record."""
        return str(uuid.uuid5(_NS_SECTION, section_id))

    # ── Entity ────────────────────────────────────────────────────────────────

    @staticmethod
    def entity_slug(entity_name_ar: str, entity_type: str) -> str:
        """
        Generate slug for an entity.
        Example: ministry-finance   (from وزارة المالية)
        """
        name_slug = _safe_slug(entity_name_ar)[:40]
        if not name_slug:
            # Fallback for non-transliterable Arabic
            name_slug = f"{hash(entity_name_ar) & 0xFFFF:04x}"
        return f"{entity_type}-{name_slug}"

    @staticmethod
    def entity_id(entity_slug: str) -> str:
        """Deterministic entity ID string (prefixed, not UUID for readability)."""
        return f"entity-{entity_slug}"

    @staticmethod
    def entity_uuid(entity_id: str) -> str:
        """Deterministic UUID for DB FK use."""
        return str(uuid.uuid5(_NS_ENTITY, entity_id))

    # ── Topic ─────────────────────────────────────────────────────────────────

    @staticmethod
    def topic_id(topic_slug: str) -> str:
        """Deterministic topic ID string."""
        return f"topic-{topic_slug}"

    @staticmethod
    def topic_uuid(topic_id: str) -> str:
        """Deterministic UUID for DB FK use."""
        return str(uuid.uuid5(_NS_TOPIC, topic_id))

    # ── Relationship ──────────────────────────────────────────────────────────

    @staticmethod
    def relationship_id(
        source_doc_slug: str,
        rel_type: str,
        target_doc_slug: str,
    ) -> str:
        """
        Stable relationship identifier.
        Example: law-2014-34-AMENDS-law-2009-28
        """
        key = f"{source_doc_slug}-{rel_type}-{target_doc_slug}"
        return str(uuid.uuid5(_NS_RELATIONSHIP, key))

    # ── Fetch record ──────────────────────────────────────────────────────────

    @staticmethod
    def fetch_id(url: str, timestamp: str) -> str:
        """Short readable fetch ID (not a full UUID)."""
        key = f"{url}_{timestamp}"
        short = str(uuid.uuid5(_NS_FETCH, key)).replace("-", "")[:12]
        return f"fetch-{short}"

    # ── Title slug helper ─────────────────────────────────────────────────────

    @staticmethod
    def title_slug(title_ar: str, max_len: int = 40) -> str:
        """
        Convert Arabic title to a URL-safe slug fragment.
        Used as a tiebreaker when doc_number is absent.
        """
        s = _safe_slug(title_ar)
        return s[:max_len] if s else "untitled"

    # ── Assignment / junction IDs ─────────────────────────────────────────────

    @staticmethod
    def topic_assignment_id(doc_id: str, topic_id: str) -> str:
        return str(uuid.uuid5(_NS_TOPIC, f"{doc_id}::{topic_id}"))

    @staticmethod
    def entity_role_id(doc_id: str, entity_id: str, role: str) -> str:
        return str(uuid.uuid5(_NS_ENTITY, f"{doc_id}::{entity_id}::{role}"))
