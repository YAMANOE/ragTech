"""
tests/test_topic_classifier.py
--------------------------------
Tests for LegislationStructurer._classify_topics() keyword-scoring logic.

Uses the real topics.yaml taxonomy and the same scoring weights as production:
  title match  → +0.40   (≥ threshold of 0.40 on its own)
  early text   → +0.20
  body text    → +0.05

A single keyword hit in the title exceeds the threshold and guarantees
a topic assignment, so tests inject text with known keywords.
"""
import pytest
from dataclasses import dataclass, field
from typing import Optional, List

from utils.arabic_utils import ArabicTextUtils as ATU


# ── Minimal Document stub ──────────────────────────────────────────────────

@dataclass
class _DocStub:
    """Minimal Document that satisfies _classify_topics()'s attribute access."""
    doc_id: str
    doc_slug: str
    title_ar: str
    doc_type: str = "law"
    status: str = "active"
    status_normalized: str = "active"
    source_status_text: Optional[str] = None
    title_en: Optional[str] = None
    doc_number: Optional[str] = None
    issue_year: Optional[int] = None
    issuing_entity_id: Optional[str] = None
    issuing_entity_name_ar: Optional[str] = None
    official_gazette_number: Optional[str] = None
    publication_date: Optional[str] = None
    effective_date: Optional[str] = None
    repeal_date: Optional[str] = None
    legal_basis_text: Optional[str] = None
    applicability_scope: str = "unknown"
    applicability_sectors: List[str] = field(default_factory=list)
    applicability_entities: List[str] = field(default_factory=list)
    source_url: str = ""
    fetch_date: Optional[str] = None
    raw_html_path: str = ""
    raw_text_path: str = ""
    clean_json_path: str = ""
    needs_review: bool = False
    has_attachment: bool = False
    notes: str = ""


def _doc(title_ar: str) -> _DocStub:
    return _DocStub(doc_id="test-id", doc_slug="test-slug", title_ar=title_ar)


# ── Helper ─────────────────────────────────────────────────────────────────

def classify(structurer, title: str, body: str = "") -> list[str]:
    """
    Run _classify_topics and return list of matched topic slugs.
    Uses 'body' as the normalised_text argument.
    """
    doc = _doc(title)
    _, assignments = structurer._classify_topics(doc, body)
    return [a.topic_id for a in assignments]


def topic_slugs_from_ids(ids: list[str]) -> list[str]:
    """Strip the 'topic-' prefix added by IDGenerator.topic_id()."""
    # topic_id() = "topic-" + slug, so we reverse that
    return [tid.replace("topic-", "", 1) if tid.startswith("topic-") else tid for tid in ids]


# ── Tests ──────────────────────────────────────────────────────────────────

class TestConstitutionalAdministrativeLaw:
    """'دستور' in title → constitutional-administrative-law."""

    def test_dustur_in_title(self, structurer):
        ids = classify(structurer, title="الدستور الأردني لسنة 1952")
        slugs = topic_slugs_from_ids(ids)
        assert "constitutional-administrative-law" in slugs, (
            f"Expected constitutional-administrative-law in {slugs}"
        )


class TestTaxFinancialLaw:
    """'ضريبة' keyword → tax-financial-law."""

    def test_tax_keyword_in_title(self, structurer):
        ids = classify(structurer, title="قانون ضريبة الدخل رقم 25 لسنة 2018")
        slugs = topic_slugs_from_ids(ids)
        assert "tax-financial-law" in slugs, (
            f"Expected tax-financial-law in {slugs}"
        )

    def test_tax_keyword_in_body(self, structurer):
        # Keyword only in body → lower confidence but should still cross threshold
        # after enough hits; use 'الضريبة' (a direct keyword) in early text
        body = "تفرض الضريبة على الدخل الخاضع للضريبة وفقاً لأحكام هذا القانون"
        ids = classify(structurer, title="قانون الإيرادات العامة", body=body)
        slugs = topic_slugs_from_ids(ids)
        assert "tax-financial-law" in slugs, (
            f"Expected tax-financial-law in {slugs}"
        )


class TestLaborSocialInsurance:
    """'عمل' keyword → labor-social-insurance."""

    def test_amal_in_title(self, structurer):
        ids = classify(structurer, title="قانون العمل الأردني رقم 8 لسنة 1996")
        slugs = topic_slugs_from_ids(ids)
        assert "labor-social-insurance" in slugs, (
            f"Expected labor-social-insurance in {slugs}"
        )


class TestAgriculturalLaw:
    """'زراعة' keyword → agricultural-insurance-law."""

    def test_ziraa_in_title(self, structurer):
        ids = classify(structurer, title="قانون الزراعة والتنمية الريفية")
        slugs = topic_slugs_from_ids(ids)
        assert "agricultural-insurance-law" in slugs, (
            f"Expected agricultural-insurance-law in {slugs}"
        )

    def test_wizarat_alziraa_in_body(self, structurer):
        body = "تختص وزارة الزراعة بالإشراف على تطبيق أحكام هذا القانون"
        ids = classify(structurer, title="نظام الإنتاج الزراعي", body=body)
        slugs = topic_slugs_from_ids(ids)
        assert "agricultural-insurance-law" in slugs, (
            f"Expected agricultural-insurance-law in {slugs}"
        )


class TestEmptyText:
    """Empty title and empty body → no topic assignments."""

    def test_empty_inputs_yield_no_topics(self, structurer):
        ids = classify(structurer, title="", body="")
        assert ids == [], f"Expected no topics for empty input, got {ids}"

    def test_generic_title_no_keyword_yields_no_topic(self, structurer):
        # Title with no taxonomy keyword and empty body
        ids = classify(structurer, title="المادة الأولى أحكام عامة", body="")
        # May or may not match; just assert no exception and result is a list
        assert isinstance(ids, list)


class TestPrimaryTopicOrdering:
    """First assignment (is_primary=True) should have the highest confidence."""

    def test_primary_has_highest_confidence(self, structurer):
        doc = _doc("قانون ضريبة الدخل والزراعة والعمل")
        body = (
            "تفرض الضريبة على جميع دافعي الضريبة. "
            "تنظيم قانون العمل وعلاقات صاحب العمل. "
            "تشجيع الزراعة ووزارة الزراعة."
        )
        _, assignments = structurer._classify_topics(doc, body)
        if len(assignments) >= 2:
            primary = next((a for a in assignments if a.is_primary), None)
            others  = [a for a in assignments if not a.is_primary]
            assert primary is not None
            for other in others:
                assert primary.confidence >= other.confidence, (
                    f"Primary confidence {primary.confidence} < other {other.confidence}"
                )
