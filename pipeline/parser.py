"""
pipeline/parser.py
-------------------
Parses raw HTML from LOB into structured metadata + section list.

Responsibilities:
  1. Extract document title from HTML
  2. Extract metadata (number, year, issuing entity, gazette, dates)
  3. Extract full text body
  4. Segment text into a hierarchy: preamble → parts → chapters → articles → paragraphs
  5. Detect annex/attachment sections
  6. Produce a ParsedDocument dict ready for the Cleaner and Structurer

The parser uses BeautifulSoup for HTML navigation, then regex on the extracted
plain text for section boundary detection.

IMPORTANT: LOB CSS selectors are in config/settings.py. They are best guesses
and must be verified + tuned after inspecting the actual LOB DOM.

Usage:
    from pipeline.parser import LOBParser
    parsed = LOBParser(settings).parse_html(raw_html, doc_slug, source_url)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from loguru import logger

from config.settings import Settings
from utils.arabic_utils import ArabicTextUtils as ATU


# ─────────────────────────────────────────────────────────────────────────────
# ParsedSection — lightweight container (not a dataclass to keep it flexible)
# ─────────────────────────────────────────────────────────────────────────────

class ParsedSection:
    """Raw section detected during parsing — before ID assignment."""

    def __init__(
        self,
        section_type: str,
        section_number: Optional[str],
        section_label: Optional[str],
        raw_text: str,
        display_order: int,
        parent_order: Optional[int] = None,  # display_order of parent
    ):
        self.section_type    = section_type
        self.section_number  = section_number
        self.section_label   = section_label
        self.raw_text        = raw_text
        self.display_order   = display_order
        self.parent_order    = parent_order  # will be resolved to section_id later

    def __repr__(self) -> str:
        snippet = self.raw_text[:60].replace("\n", " ")
        return (
            f"ParsedSection({self.section_type} #{self.section_number} "
            f"[order={self.display_order}] '{snippet}...')"
        )


# ─────────────────────────────────────────────────────────────────────────────
# LOBParser
# ─────────────────────────────────────────────────────────────────────────────

class LOBParser:
    """
    Parses a raw HTML string fetched from a LOB legislation page.

    Output is a plain dict (ParsedDocument) with:
      - metadata: dict of extracted metadata fields
      - raw_text:  full extracted plain text (unmodified)
      - sections:  list of ParsedSection objects
      - attachments: list of {'url': ..., 'label': ...} dicts
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    # ── Main entry point ──────────────────────────────────────────────────────

    def parse_html(
        self,
        raw_html: str,
        doc_slug: str,
        source_url: str = "",
    ) -> dict:
        """
        Parse a raw HTML page from LOB.

        Returns a ParsedDocument dict:
        {
          'doc_slug':    str,
          'source_url':  str,
          'metadata':    dict,
          'raw_text':    str,
          'sections':    list[ParsedSection],
          'attachments': list[dict],
          'parse_notes': list[str],
        }
        """
        parse_notes: list[str] = []
        soup = BeautifulSoup(raw_html, "lxml")

        # 1. Remove boilerplate (nav, header, footer, scripts)
        for tag in soup(["script", "style", "nav", "header", "footer",
                          "noscript", "iframe", "button"]):
            tag.decompose()

        # 2. Extract title
        title_ar = self._extract_title(soup, parse_notes)

        # 3. Extract body text, then strip known LOB Angular UI artifact lines
        raw_body_text = self._extract_body_text(soup, parse_notes)
        body_text = ATU.remove_lob_artifacts(raw_body_text)  # removes e.g. "ارتباطات المادة"

        if not body_text:
            parse_notes.append("WARN: Empty body text extracted — check LOB_SELECTORS")
            logger.warning(f"[Parser] Empty body text for {doc_slug}")

        # 4. Extract metadata from title + first ~1500 chars of cleaned body
        metadata = self._extract_metadata(title_ar, body_text[:1500], doc_slug, parse_notes)

        # 5. Extract attachment URLs
        attachments = self._extract_attachments(soup, source_url)

        # 6. Segment cleaned body text into sections
        sections = self._segment_sections(body_text, parse_notes)

        logger.info(
            f"[Parser] {doc_slug}: title='{title_ar[:60]}', "
            f"sections={len(sections)}, attachments={len(attachments)}"
        )

        return {
            "doc_slug":    doc_slug,
            "source_url":  source_url,
            "metadata":    metadata,
            "raw_text":    body_text,
            "sections":    sections,
            "attachments": attachments,
            "parse_notes": parse_notes,
        }

    def parse_text_file(
        self,
        text_path: str,
        doc_slug: str,
        source_url: str = "",
    ) -> dict:
        """
        Alternative: parse from a saved .txt file (no HTML, text only).
        Useful when raw text was already extracted by the fetcher.
        """
        text = Path(text_path).read_text(encoding="utf-8")
        parse_notes: list[str] = []
        # Build minimal metadata from text alone
        metadata = self._extract_metadata(
            title_ar=text.split("\n")[0][:200],
            context_text=ATU.remove_lob_artifacts(text)[:1500],
            doc_slug=doc_slug,
            parse_notes=parse_notes,
        )
        clean_text = ATU.remove_lob_artifacts(text)
        sections = self._segment_sections(clean_text, parse_notes)

        return {
            "doc_slug":    doc_slug,
            "source_url":  source_url,
            "metadata":    metadata,
            "raw_text":    text,
            "sections":    sections,
            "attachments": [],
            "parse_notes": parse_notes,
        }

    # ── Title extraction ──────────────────────────────────────────────────────

    def _extract_title(self, soup: BeautifulSoup, notes: list[str]) -> str:
        """Try configured title selectors in order, fall back to first <h1> or <h2>."""
        for selector in self.settings.LOB_SELECTORS["title"]:
            el = soup.select_one(selector)
            if el and el.get_text(strip=True):
                return el.get_text(separator=" ", strip=True)

        # Fallbacks
        for tag in ["h1", "h2"]:
            el = soup.find(tag)
            if el:
                return el.get_text(separator=" ", strip=True)

        notes.append("WARN: Title not found — using first line of body text")
        # Last resort: first non-empty line of page text
        body_text = soup.get_text(separator="\n")
        for line in body_text.splitlines():
            line = line.strip()
            if line and ATU.contains_arabic(line):
                return line[:200]

        return ""

    # ── Body text extraction ──────────────────────────────────────────────────

    def _extract_body_text(self, soup: BeautifulSoup, notes: list[str]) -> str:
        """
        Try configured content_body selectors; fall back to full page body.
        Returns plain text with newlines preserving paragraph structure.
        """
        for selector in self.settings.LOB_SELECTORS["content_body"]:
            el = soup.select_one(selector)
            if el:
                # Preserve paragraph breaks: replace <p>, <br>, <div> with newlines
                for br in el.find_all(["br", "p", "div", "li", "tr"]):
                    br.insert_before("\n")
                text = el.get_text(separator="\n")
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if ATU.contains_arabic(text) and len(text) > 100:
                    return text

        # Fallback: full page body text
        notes.append("WARN: Content selector not matched — using full page text")
        body = soup.find("body")
        if body:
            for br in body.find_all(["br", "p", "div", "li"]):
                br.insert_before("\n")
            text = body.get_text(separator="\n")
            return re.sub(r"\n{3,}", "\n\n", text).strip()

        return soup.get_text(separator="\n").strip()

    # ── Metadata extraction ───────────────────────────────────────────────────

    def _extract_metadata(
        self,
        title_ar: str,
        context_text: str,
        doc_slug: str,
        notes: list[str],
    ) -> dict:
        """
        Extract structured metadata from title + beginning of body text.
        Returns a flat dict matching Document fields.
        """
        combined = f"{title_ar}\n{context_text}"

        # ── Document type ──────────────────────────────────────────────
        doc_type = ATU.detect_doc_type(title_ar, self.settings.DOC_TYPE_MAP)
        if doc_type == "unknown":
            notes.append(f"WARN: Could not detect doc_type from title: '{title_ar[:60]}'")

        # ── Number + Year ──────────────────────────────────────────────
        num_year = ATU.extract_doc_number_year(combined)
        doc_number = num_year[0] if num_year else None
        issue_year = int(num_year[1]) if num_year else None
        if not num_year:
            notes.append("WARN: Document number/year not detected")

        # ── Gazette number ─────────────────────────────────────────────
        gazette_number = ATU.extract_gazette_number(combined)

        # ── Dates ──────────────────────────────────────────────────────
        dates = ATU.extract_dates(combined)
        publication_date = dates[0] if dates else None
        effective_date   = dates[1] if len(dates) > 1 else publication_date

        # ── Legal basis ────────────────────────────────────────────────
        basis_hits = ATU.extract_legal_basis(combined)
        legal_basis_text = basis_hits[0]["basis_text"] if basis_hits else None

        # ── Issuing entity ─────────────────────────────────────────────
        entities = ATU.extract_entities(title_ar + "\n" + context_text[:400])
        issuing_entity_name_ar = entities[0]["entity_name_ar"] if entities else None

        # ── Status heuristic ───────────────────────────────────────────
        # Default to 'active'; Structurer will revise after amendment detection
        status = "active"
        if ATU.detect_repeal(combined):
            status = "repealed"
        elif ATU.detect_amendment(combined):
            status = "amended"

        return {
            "title_ar":              title_ar.strip(),
            "doc_type":              doc_type,
            "doc_number":            doc_number,
            "issue_year":            issue_year,
            "official_gazette_number": gazette_number,
            "publication_date":      publication_date,
            "effective_date":        effective_date,
            "status":                status,
            "legal_basis_text":      legal_basis_text,
            "issuing_entity_name_ar": issuing_entity_name_ar,
        }

    # ── Attachment extraction ─────────────────────────────────────────────────

    def _extract_attachments(
        self,
        soup: BeautifulSoup,
        base_url: str,
    ) -> list[dict]:
        """
        Find linked PDF attachments and annex files on the page.
        Returns list of {'url': str, 'label': str} dicts.
        """
        attachments = []
        lob_base = self.settings.LOB_BASE_URL

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            label = a_tag.get_text(strip=True)

            # Detect PDF links or explicitly named attachment links
            is_pdf = href.lower().endswith(".pdf")
            is_attachment = any(kw in label for kw in ["ملحق", "جدول", "نموذج", "مرفق"])

            if is_pdf or is_attachment:
                if not href.startswith("http"):
                    href = lob_base + href if href.startswith("/") else f"{base_url}/{href}"
                attachments.append({"url": href, "label": label or "attachment"})

        return attachments

    # ── Section segmentation ─────────────────────────────────────────────────

    def _segment_sections(
        self,
        text: str,
        notes: list[str],
    ) -> list[ParsedSection]:
        """
        Segment full text into a list of ParsedSection objects.

        Strategy:
          1. Split text into lines
          2. Classify each line as a section boundary marker
          3. Group trailing text under each section until next marker
          4. Build parent-child order references

        Returns ordered list of ParsedSection objects.
        """
        lines = text.splitlines()
        sections: list[ParsedSection] = []
        order = 0

        # Tracking current context for hierarchy
        current_part_order:    Optional[int] = None
        current_chapter_order: Optional[int] = None
        current_article_order: Optional[int] = None
        preamble_lines: list[str] = []
        preamble_done = False

        # Buffer for accumulating lines into current section
        pending_section: Optional[dict] = None

        def flush_pending():
            """Flush pending section buffer into section list."""
            nonlocal order
            if pending_section is None:
                return
            body = "\n".join(pending_section["body_lines"]).strip()
            # Include the header line in the section text
            full_text = pending_section["label"] + ("\n" + body if body else "")
            sec = ParsedSection(
                section_type=pending_section["type"],
                section_number=pending_section["number"],
                section_label=pending_section["label"],
                raw_text=full_text.strip(),
                display_order=pending_section["order"],
                parent_order=pending_section["parent_order"],
            )
            sections.append(sec)

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if pending_section:
                    pending_section["body_lines"].append("")
                continue

            # ── Check for Part (باب / جزء) ──────────────────────────────
            m_part = ATU.detect_part(stripped)
            if m_part:
                flush_pending()
                preamble_done = True
                if preamble_lines and not sections:
                    _add_preamble(preamble_lines, sections, order)
                    order += 1
                order += 1
                current_part_order    = order
                current_chapter_order = None
                current_article_order = None
                pending_section = _new_pending(
                    stype="part",
                    number=m_part.group(2),
                    label=stripped,
                    order=order,
                    parent_order=None,
                )
                continue

            # ── Check for Chapter (فصل / قسم) ────────────────────────────
            m_chapter = ATU.detect_chapter(stripped)
            if m_chapter:
                flush_pending()
                preamble_done = True
                if preamble_lines and not sections:
                    _add_preamble(preamble_lines, sections, order)
                    order += 1
                order += 1
                current_chapter_order = order
                current_article_order = None
                pending_section = _new_pending(
                    stype="chapter",
                    number=m_chapter.group(2),
                    label=stripped,
                    order=order,
                    parent_order=current_part_order,
                )
                continue

            # ── Check for Article (مادة) ─────────────────────────────────
            m_article = ATU.detect_article(stripped)
            if m_article:
                flush_pending()
                preamble_done = True
                if preamble_lines and not sections:
                    _add_preamble(preamble_lines, sections, order)
                    order += 1
                order += 1
                current_article_order = order
                pending_section = _new_pending(
                    stype="article",
                    number=ATU.convert_arabic_to_western_digits(m_article.group(1)),
                    label=stripped,
                    order=order,
                    parent_order=current_chapter_order or current_part_order,
                )
                continue

            # ── Check for Annex ──────────────────────────────────────────
            m_annex = ATU.detect_annex(stripped)
            if m_annex:
                flush_pending()
                order += 1
                pending_section = _new_pending(
                    stype="annex",
                    number=None,
                    label=stripped,
                    order=order,
                    parent_order=None,
                )
                continue

            # ── Regular text line ────────────────────────────────────────
            if not preamble_done:
                preamble_lines.append(line)
            elif pending_section is not None:
                # Check for sub-paragraph markers within articles
                m_par_letter  = ATU.detect_paragraph_letter(stripped)
                m_par_ordinal = ATU.detect_paragraph_ordinal(stripped)

                if (m_par_letter or m_par_ordinal) and pending_section["type"] == "article":
                    # Only split into a paragraph sub-node when the article
                    # already has substantive lead text.  If the article body
                    # is still empty (e.g. article 2 starts immediately with
                    # "أ-"), accumulate everything inside the article body so
                    # it stays non-empty and useful for chatbot retrieval.
                    article_body = "\n".join(pending_section["body_lines"]).strip()
                    in_no_split  = pending_section.get("no_split", False)

                    if article_body and not in_no_split:
                        # Article has lead text → create paragraph sub-node
                        flush_pending()
                        order += 1
                        par_num = (
                            m_par_letter.group(1) if m_par_letter
                            else m_par_ordinal.group(1)
                        )
                        pending_section = _new_pending(
                            stype="paragraph",
                            number=par_num,
                            label=stripped,
                            order=order,
                            parent_order=current_article_order,
                        )
                    else:
                        # Article empty or already in no-split mode →
                        # keep ALL paragraph content inside the article body
                        pending_section["no_split"] = True
                        pending_section["body_lines"].append(line)
                else:
                    pending_section["body_lines"].append(line)
            else:
                # Text before first section marker → accumulate for preamble
                preamble_lines.append(line)

        # Flush final section
        flush_pending()

        # If we have preamble lines but no sections were created yet
        if preamble_lines and not any(s.section_type == "preamble" for s in sections):
            order += 1
            _add_preamble(preamble_lines, sections, order)

        if not sections:
            notes.append("WARN: No sections detected — law may need manual review")
        else:
            article_count = sum(1 for s in sections if s.section_type == "article")
            notes.append(f"INFO: Detected {len(sections)} sections ({article_count} articles)")

        return sections


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _new_pending(
    stype: str,
    number: Optional[str],
    label: str,
    order: int,
    parent_order: Optional[int],
) -> dict:
    return {
        "type": stype,
        "number": number,
        "label": label,
        "order": order,
        "parent_order": parent_order,
        "body_lines": [],
    }


def _add_preamble(
    preamble_lines: list[str],
    sections: list[ParsedSection],
    order: int,
) -> None:
    text = "\n".join(preamble_lines).strip()
    if text:
        sections.insert(
            0,
            ParsedSection(
                section_type="preamble",
                section_number=None,
                section_label="ديباجة",
                raw_text=text,
                display_order=order,
                parent_order=None,
            ),
        )
