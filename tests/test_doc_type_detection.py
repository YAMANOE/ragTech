"""
tests/test_doc_type_detection.py
---------------------------------
Tests for ArabicTextUtils.detect_doc_type() — the three-pass matcher:
  Pass 1  startswith after kashida strip
  Pass 2  word-boundary regex search (handles ال-prefix)
  Pass 3  LegislationType integer in source_url fallback

Covers all 5 common types + edge cases identified in the corpus.
"""
import pytest
from utils.arabic_utils import ArabicTextUtils as ATU


# ── helper ─────────────────────────────────────────────────────────────────

def detect(title: str, url: str = "", type_map=None) -> str:
    """Thin wrapper; uses the session-scoped type_map fixture when available."""
    # type_map injected via the parameter (or fall back to fixture via caller)
    return ATU.detect_doc_type(title, type_map, source_url=url)


# ── Tests ──────────────────────────────────────────────────────────────────

class TestLawDetection:
    """Titles that should resolve to 'law'."""

    def test_plain_law_title(self, type_map):
        assert detect("قانون الجمارك رقم 20 لسنة 2022", type_map=type_map) == "law"

    def test_al_prefix_law(self, type_map):
        # Pass 2 (regex) must match "قانون" inside "القانون"
        assert detect("القانون الاساسي لشرق الاردن رقم 0 لسنة 1928", type_map=type_map) == "law"

    def test_kashida_law(self, type_map):
        # Tatweel U+0640 inside "قانــون" — Pass 1 after strip
        title = "قان\u0640\u0640\u0640ون صندوق الاستثمار رقم 16 لسنة 2016"
        assert detect(title, type_map=type_map) == "law"

    def test_law_with_number(self, type_map):
        assert detect("قانون الضريبة العامة على المبيعات رقم 29 لسنة 1988", type_map=type_map) == "law"


class TestRegulationDetection:
    """Titles that should resolve to 'regulation'."""

    def test_plain_regulation(self, type_map):
        assert detect("نظام الخدمة المدنية رقم 55 لسنة 2002", type_map=type_map) == "regulation"

    def test_regulation_url_fallback(self, type_map):
        # Title has no recognised type keyword; URL has LegislationType=2
        url = "https://www.lob.gov.jo/?LegislationType=2&LegislationID=2694"
        assert detect("سلطة اقليم العقبة رقم 7 لسنة 1987", url=url, type_map=type_map) == "regulation"


class TestInstructionsDetection:
    """Titles that should resolve to 'instructions'."""

    def test_talim_title(self, type_map):
        assert detect("تعليمات الترخيص المهني رقم 3 لسنة 2010", type_map=type_map) == "instructions"

    def test_instructions_url_fallback(self, type_map):
        url = "https://www.lob.gov.jo/?LegislationType=3&LegislationID=999"
        assert detect("لائحة التدريب المهني", url=url, type_map=type_map) == "instructions"


class TestAgreementDetection:
    """Titles that should resolve to 'agreement'."""

    def test_ittifaqiya(self, type_map):
        assert detect("اتفاقية قرض المشاريع الصغيرة رقم 19 لسنة 1997", type_map=type_map) == "agreement"

    def test_agreement_url_fallback(self, type_map):
        url = "https://www.lob.gov.jo/?LegislationType=4&LegislationID=100"
        assert detect("ترتيب دولي", url=url, type_map=type_map) == "agreement"


class TestConstitutionDetection:
    """Titles that should resolve to 'constitution'."""

    def test_dustur(self, type_map):
        assert detect("الدستور الاردني لسنة 1952", type_map=type_map) == "constitution"


class TestUrlFallback:
    """LegislationType integer in URL drives type when title has no keyword."""

    def test_type_1_law(self, type_map):
        url = "https://lob.gov.jo/?LegislationType=1&LegislationID=1"
        assert detect("وثيقة غير معروفة", url=url, type_map=type_map) == "law"

    def test_type_2_regulation(self, type_map):
        url = "https://lob.gov.jo/?LegislationType=2&LegislationID=2"
        assert detect("وثيقة غير معروفة", url=url, type_map=type_map) == "regulation"

    def test_type_3_instructions(self, type_map):
        url = "https://lob.gov.jo/?LegislationType=3&LegislationID=3"
        assert detect("وثيقة غير معروفة", url=url, type_map=type_map) == "instructions"

    def test_type_4_agreement(self, type_map):
        url = "https://lob.gov.jo/?LegislationType=4&LegislationID=4"
        assert detect("وثيقة غير معروفة", url=url, type_map=type_map) == "agreement"

    def test_unknown_type_id_returns_unknown(self, type_map):
        url = "https://lob.gov.jo/?LegislationType=99&LegislationID=5"
        assert detect("وثيقة غير معروفة", url=url, type_map=type_map) == "unknown"


class TestUnknownFallback:
    """No keyword and no URL → 'unknown'."""

    def test_title_with_no_keyword_no_url(self, type_map):
        assert detect("وثيقة غير معروفة", type_map=type_map) == "unknown"

    def test_empty_title_no_url(self, type_map):
        assert detect("", type_map=type_map) == "unknown"
