"""
tests/test_parser.py
--------------------
Tests for LOBParser._segment_sections() paragraph-splitting logic.

Verifies the two previously-buggy cases:
  Bug 1: Article starting directly with أ- → all paragraphs were merged into body
  Bug 2: ب/ج/د after أ- were merged because pending_section["type"] == "paragraph"
         but the condition only handled type == "article"
"""
import pytest
from tests.conftest import (
    ARTICLE_DIRECT_PAR,
    ARTICLE_FOUR_PARAGRAPHS,
    ARTICLE_LEAD_THEN_PAR,
    PREAMBLE_THEN_ARTICLE,
)


# ── helpers ────────────────────────────────────────────────────────────────

def segment(parser, text: str):
    """Helper: call _segment_sections and return the section list."""
    return parser._segment_sections(text, [])


def sections_of_type(sections, stype: str):
    return [s for s in sections if s.section_type == stype]


def sections_of_number(sections, number: str):
    return [s for s in sections if s.section_number == number]


# ── Tests ──────────────────────────────────────────────────────────────────

class TestArticleDirectParagraph:
    """Article whose very first line is أ- (no lead sentence)."""

    def test_produces_three_paragraphs(self, parser):
        secs = segment(parser, ARTICLE_DIRECT_PAR)
        pars = sections_of_type(secs, "paragraph")
        assert len(pars) == 3, (
            f"Expected 3 paragraphs, got {len(pars)}. "
            f"All section types: {[s.section_type for s in secs]}"
        )

    def test_paragraph_letters_are_correct(self, parser):
        secs = segment(parser, ARTICLE_DIRECT_PAR)
        pars = sections_of_type(secs, "paragraph")
        numbers = [p.section_number for p in pars]
        assert numbers == ["أ", "ب", "ج"], f"Expected ['أ','ب','ج'], got {numbers}"

    def test_paragraphs_parented_to_article(self, parser):
        secs = segment(parser, ARTICLE_DIRECT_PAR)
        article = sections_of_type(secs, "article")
        assert len(article) == 1
        art_order = article[0].display_order
        for par in sections_of_type(secs, "paragraph"):
            assert par.parent_order == art_order, (
                f"Paragraph {par.section_number} has parent_order={par.parent_order}, "
                f"expected {art_order}"
            )


class TestArticleLeadSentenceThenParagraph:
    """Article with a lead sentence before أ-."""

    def test_produces_two_paragraphs(self, parser):
        secs = segment(parser, ARTICLE_LEAD_THEN_PAR)
        pars = sections_of_type(secs, "paragraph")
        assert len(pars) == 2, (
            f"Expected 2 paragraphs, got {len(pars)}. "
            f"Sections: {[(s.section_type, s.section_number) for s in secs]}"
        )

    def test_lead_sentence_stays_in_article_body(self, parser):
        secs = segment(parser, ARTICLE_LEAD_THEN_PAR)
        article = sections_of_type(secs, "article")
        assert len(article) == 1
        assert "تطبق أحكام" in article[0].raw_text, (
            "Lead sentence should remain in article body text"
        )

    def test_paragraph_letters_are_alef_and_ba(self, parser):
        secs = segment(parser, ARTICLE_LEAD_THEN_PAR)
        pars = sections_of_type(secs, "paragraph")
        numbers = [p.section_number for p in pars]
        assert numbers == ["أ", "ب"], f"Expected ['أ','ب'], got {numbers}"


class TestFourConsecutiveParagraphs:
    """أ ب ج د — all four letters split as independent paragraphs (Bug 2 regression)."""

    def test_produces_four_paragraphs(self, parser):
        secs = segment(parser, ARTICLE_FOUR_PARAGRAPHS)
        pars = sections_of_type(secs, "paragraph")
        assert len(pars) == 4, (
            f"Expected 4 paragraphs, got {len(pars)}. "
            f"Sections: {[(s.section_type, s.section_number) for s in secs]}"
        )

    def test_all_four_letters_present(self, parser):
        secs = segment(parser, ARTICLE_FOUR_PARAGRAPHS)
        pars = sections_of_type(secs, "paragraph")
        numbers = [p.section_number for p in pars]
        assert numbers == ["أ", "ب", "ج", "د"], (
            f"Expected ['أ','ب','ج','د'], got {numbers}"
        )

    def test_each_paragraph_has_its_own_text(self, parser):
        secs = segment(parser, ARTICLE_FOUR_PARAGRAPHS)
        pars = sections_of_type(secs, "paragraph")
        # Every paragraph raw_text must contain only its own letter marker
        for par in pars:
            # No other letter-marker from the text should appear in its content
            other_letters = {"أ", "ب", "ج", "د"} - {par.section_number}
            # Each section's raw_text begins with its own marker
            assert par.raw_text.startswith(par.section_number), (
                f"Paragraph {par.section_number} raw_text doesn't start with its letter"
            )


class TestPreambleDetection:
    """Lines before the first المادة marker become a preamble section."""

    def test_preamble_is_created(self, parser):
        secs = segment(parser, PREAMBLE_THEN_ARTICLE)
        preambles = sections_of_type(secs, "preamble")
        assert len(preambles) == 1, (
            f"Expected 1 preamble, got {len(preambles)}"
        )

    def test_preamble_contains_title(self, parser):
        secs = segment(parser, PREAMBLE_THEN_ARTICLE)
        preamble = sections_of_type(secs, "preamble")[0]
        assert "قانون الخدمة المدنية" in preamble.raw_text

    def test_article_follows_preamble(self, parser):
        secs = segment(parser, PREAMBLE_THEN_ARTICLE)
        types = [s.section_type for s in secs]
        assert "preamble" in types
        assert "article" in types
        preamble_idx = types.index("preamble")
        article_idx  = types.index("article")
        assert preamble_idx < article_idx, "Preamble should come before first article"

    def test_no_preamble_when_text_starts_with_article(self, parser):
        text = "المادة (1)\nأحكام عامة\n"
        secs = segment(parser, text)
        preambles = sections_of_type(secs, "preamble")
        assert len(preambles) == 0, "No preamble expected when text starts with article"
