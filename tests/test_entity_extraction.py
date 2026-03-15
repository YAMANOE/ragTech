"""
tests/test_entity_extraction.py
--------------------------------
Tests for ArabicTextUtils.extract_entities() cleaning logic.

Verifies that greedy regex captures are trimmed correctly:
  - Stop words (من، ما، لم …) truncate the entity name
  - Arabic/ASCII punctuation is stripped
  - Valid multi-word names (up to 4 words) are preserved in full
"""
import pytest
from utils.arabic_utils import ArabicTextUtils as ATU


# ── helper ─────────────────────────────────────────────────────────────────

def extract_names(text: str) -> list[str]:
    """Return sorted list of entity_name_ar values extracted from text."""
    return sorted({e["entity_name_ar"] for e in ATU.extract_entities(text)})


def first_name(text: str) -> str:
    """Return the entity_name_ar of the first match in text."""
    results = ATU.extract_entities(text)
    assert results, f"No entities extracted from: {text!r}"
    return results[0]["entity_name_ar"]


# ── Tests ──────────────────────────────────────────────────────────────────

class TestStopWordsTrimming:
    """Trailing stop words must be cut off at the boundary."""

    def test_majlis_alwuzara_stops_at_min(self):
        # "من" is a stop word → name should be "مجلس الوزراء"
        result = first_name("مجلس الوزراء من رئيس الوزراء يقرر")
        assert result == "مجلس الوزراء", f"Got: {result!r}"

    def test_wizarat_almaliya_stops_at_ma(self):
        # "ما" is a stop word → name should be "وزارة المالية"
        result = first_name("وزارة المالية ما لم يصدر قرار")
        assert result == "وزارة المالية", f"Got: {result!r}"

    def test_majlis_alaayan_stops_at_punctuation(self):
        # semicolon ؛ truncates the match before "سنتان" (which is also a stop word)
        result = first_name("مجلس الاعيان سنتان ؛ بقرار المجلس")
        assert result == "مجلس الاعيان", f"Got: {result!r}"


class TestNoChangeNeeded:
    """Clean names that require no modification."""

    def test_mahkama_shariya_unchanged(self):
        # 2-word court name with no stop words
        result = first_name("محكمة شرعية تختص بالنظر في قضايا الأحوال الشخصية")
        assert result == "محكمة شرعية", f"Got: {result!r}"

    def test_four_word_ministry_kept(self):
        # "وزارة الأشغال العامة والإسكان" — valid 4-word compound ministry
        names = extract_names("وزارة الأشغال العامة والإسكان تتولى الإشراف")
        assert "وزارة الأشغال العامة والإسكان" in names, (
            f"Expected 4-word ministry name in {names}"
        )

    def test_wizarat_alziraa_two_words(self):
        result = first_name("وزارة الزراعة تشرف على البرامج الزراعية")
        assert result == "وزارة الزراعة", f"Got: {result!r}"


class TestEntityTypes:
    """Each matched entity should carry the correct entity_type."""

    def test_wizara_type_is_ministry(self):
        results = ATU.extract_entities("وزارة المالية تصدر قرارات")
        assert any(e["entity_type"] == "ministry" for e in results)

    def test_majlis_type_is_council(self):
        results = ATU.extract_entities("مجلس الوزراء يجتمع أسبوعياً")
        assert any(e["entity_type"] == "council" for e in results)

    def test_mahkama_type_is_court(self):
        results = ATU.extract_entities("محكمة التمييز تنظر في الطعون")
        assert any(e["entity_type"] == "court" for e in results)

    def test_haia_type_is_authority(self):
        results = ATU.extract_entities("هيئة الاستثمار تمنح التراخيص")
        assert any(e["entity_type"] == "authority" for e in results)

    def test_daira_type_is_department(self):
        results = ATU.extract_entities("دائرة الموازنة العامة تُعدّ الميزانية")
        assert any(e["entity_type"] == "department" for e in results)


class TestDeduplication:
    """Same entity mentioned twice should appear only once."""

    def test_duplicate_mention_deduplicated(self):
        text = "مجلس الوزراء يقرر. وافق مجلس الوزراء على القرار."
        results = ATU.extract_entities(text)
        names = [e["entity_name_ar"] for e in results]
        assert names.count("مجلس الوزراء") == 1, (
            f"'مجلس الوزراء' appears {names.count('مجلس الوزراء')} times in {names}"
        )


class TestEmptyAndNoMatch:
    """Edge cases — no entities in text."""

    def test_empty_text_returns_empty(self):
        assert ATU.extract_entities("") == []

    def test_text_without_org_words_returns_empty(self):
        # Plain numerical/date text — no entity trigger words
        assert ATU.extract_entities("01/01/2022 رقم 5 لسنة 2022") == []
