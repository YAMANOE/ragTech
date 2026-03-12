"""
utils/arabic_utils.py
---------------------
Arabic text utilities for the legislative intelligence pipeline.

Key responsibilities:
  1. Text normalization (for search/parsing — original text stays untouched)
  2. Arabic legal pattern matching (articles, chapters, legal basis, dates)
  3. Entity pattern detection (ministries, authorities, councils)
  4. Document type detection from title
  5. Legal relationship trigger detection
  6. Scope/applicability classification
  7. Arabic numeral conversion

IMPORTANT: All normalization functions return a NEW string.
           They never modify the caller's data in place.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# pyarabic is used for battle-tested Arabic character utilities
try:
    import pyarabic.araby as araby
    _PYARABIC_AVAILABLE = True
except ImportError:
    _PYARABIC_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Arabic Unicode constants
# ─────────────────────────────────────────────────────────────────────────────

# Harakat (diacritics)
HARAKAT = "\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0653\u0654\u0655\u0656\u0657\u0658\u0659\u065A\u065B\u065C\u065D\u065E\u065F"

# Tatweel (kashida stretcher)
TATWEEL = "\u0640"

# Zero-width characters
ZERO_WIDTH = "\u200B\u200C\u200D\uFEFF\u00AD"

# Arabic-Indic numerals (U+0660..U+0669) → ASCII digits
ARABIC_INDIC_DIGITS = {
    "\u0660": "0", "\u0661": "1", "\u0662": "2", "\u0663": "3", "\u0664": "4",
    "\u0665": "5", "\u0666": "6", "\u0667": "7", "\u0668": "8", "\u0669": "9",
}

# Alef variants → bare Alef
ALEF_VARIANTS = {
    "\u0622": "\u0627",  # آ → ا
    "\u0623": "\u0627",  # أ → ا
    "\u0625": "\u0627",  # إ → ا
    "\u0671": "\u0627",  # ٱ → ا
}

# ─────────────────────────────────────────────────────────────────────────────
# Ordinal word to integer map (Arabic)
# ─────────────────────────────────────────────────────────────────────────────
ARABIC_ORDINAL_TO_INT: dict[str, int] = {
    "الأول": 1, "الأولى": 1,
    "الثاني": 2, "الثانية": 2,
    "الثالث": 3, "الثالثة": 3,
    "الرابع": 4, "الرابعة": 4,
    "الخامس": 5, "الخامسة": 5,
    "السادس": 6, "السادسة": 6,
    "السابع": 7, "السابعة": 7,
    "الثامن": 8, "الثامنة": 8,
    "التاسع": 9, "التاسعة": 9,
    "العاشر": 10, "العاشرة": 10,
    "الحادي عشر": 11,
    "الثاني عشر": 12,
    "الثالث عشر": 13,
    "الرابع عشر": 14,
    "الخامس عشر": 15,
    "السادس عشر": 16,
    "السابع عشر": 17,
    "الثامن عشر": 18,
    "التاسع عشر": 19,
    "العشرون": 20,
}

# ─────────────────────────────────────────────────────────────────────────────
# Compiled regex patterns for legal structure detection
# ─────────────────────────────────────────────────────────────────────────────

# Arabic ordinal text pattern for reuse
_ORDINAL_PAT = (
    r"(?:الأول[ىا]?|الثانية?|الثالثة?|الرابعة?|الخامسة?|السادسة?|"
    r"السابعة?|الثامنة?|التاسعة?|العاشرة?|الحادي عشر|الثاني عشر|"
    r"الثالث عشر|الرابع عشر|الخامس عشر|السادس عشر|السابع عشر|"
    r"الثامن عشر|التاسع عشر|العشرون)"
)

_NUM_PAT = r"(?:\d+|[\u0660-\u0669]+)"  # Western or Arabic-Indic digits

# Article: المادة (3)  |  المادة 3  |  المادة الثالثة  |  المادة (الثالثة)
RE_ARTICLE = re.compile(
    rf"^[\s\u200b]*المادة\s*[\(\（]?\s*({_NUM_PAT}|{_ORDINAL_PAT})\s*[\)\）]?",
    re.MULTILINE | re.UNICODE,
)

# Part (باب / جزء)
RE_PART = re.compile(
    rf"^[\s\u200b]*(الباب|الجزء)\s+({_ORDINAL_PAT}|{_NUM_PAT})",
    re.MULTILINE | re.UNICODE,
)

# Chapter (فصل / قسم)
RE_CHAPTER = re.compile(
    rf"^[\s\u200b]*(الفصل|القسم)\s+({_ORDINAL_PAT}|{_NUM_PAT})",
    re.MULTILINE | re.UNICODE,
)

# Paragraph markers within an article body (أ.  ب.  ج.)
RE_PARAGRAPH_LETTER = re.compile(
    r"^[\s\u200b]*([أبجدهوزحطيكلمنسعفصقرشت])[\.\-\)\u0029]\s+",
    re.MULTILINE | re.UNICODE,
)

# Ordinal paragraph markers (أولاً: ثانياً: ثالثاً:)
RE_PARAGRAPH_ORDINAL = re.compile(
    r"^[\s\u200b]*(أولاً|ثانياً|ثالثاً|رابعاً|خامساً|سادساً|سابعاً|ثامناً|تاسعاً|عاشراً)\s*[:]\s*",
    re.MULTILINE | re.UNICODE,
)

# Annex / attachment markers
RE_ANNEX = re.compile(
    r"^[\s\u200b]*(ملحق|جدول|نموذج|مرفق|الملحق|الجدول|النموذج)\b",
    re.MULTILINE | re.UNICODE,
)

# Legal basis patterns (استناداً / بناءً / عملاً / تطبيقاً / بمقتضى)
RE_LEGAL_BASIS = re.compile(
    r"(استناداً?\s+(?:لأحكام|إلى)|بناءً?\s+على(?:\s+أحكام)?|"
    r"عملاً?\s+بأحكام|تطبيقاً?\s+لأحكام|وفقاً?\s+لأحكام|"
    r"بمقتضى\s+أحكام|استناداً?\s+إلى\s+أحكام)"
    r"(.{15,300}?)(?=\n|،\s|\.\s|$)",
    re.DOTALL | re.UNICODE,
)

# Cross-reference to another law in text
RE_DOC_REFERENCE = re.compile(
    rf"(?:القانون|النظام|قرار|الاتفاقية)\s+رقم\s*[\(\（]?\s*({_NUM_PAT})\s*[\)\）]?\s*لسنة\s*({_NUM_PAT}{{4}})",
    re.UNICODE,
)

# Article reference within same law: "بموجب المادة (X)"
RE_ARTICLE_REF = re.compile(
    rf"(?:المادة|الفقرة)\s*[\(\（]?\s*({_NUM_PAT}|{_ORDINAL_PAT})\s*[\)\）]?",
    re.UNICODE,
)

# Document title number + year: رقم (34) لسنة 2014
RE_DOC_NUMBER_YEAR = re.compile(
    rf"رقم\s*[\(\（]?\s*({_NUM_PAT})\s*[\)\）]?\s*لسنة\s*({_NUM_PAT}{{4}})",
    re.UNICODE,
)

# Official Gazette reference
RE_GAZETTE = re.compile(
    r"الجريدة الرسمية\s*(?:رقم|عدد|,)?\s*[\(\（]?\s*(\d+)\s*[\)\）]?",
    re.UNICODE,
)

# Date extraction: DD/MM/YYYY or DD-MM-YYYY
RE_DATE_DMY = re.compile(
    r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})",
    re.UNICODE,
)

# Amendment trigger patterns
RE_AMEND = re.compile(
    r"(يُعدَّل|تُعدَّل|يُعدَّلُ|تُعدَّلُ|جرى تعديل|يُعدّل|تُعدّل|التعديل|بتعديل)",
    re.UNICODE,
)

# Repeal trigger patterns
RE_REPEAL = re.compile(
    r"(يُلغى|تُلغى|يُلغى|تُلغى|ملغى|ملغاة|يُلغى\s+القانون|إلغاء)",
    re.UNICODE,
)

# ── Entity patterns ──────────────────────────────────────────────────────────
RE_MINISTRY = re.compile(r"وزارة\s+[\u0600-\u06FF\u0020]{3,45}", re.UNICODE)
RE_AUTHORITY = re.compile(r"(?:هيئة|سلطة)\s+[\u0600-\u06FF\u0020]{3,45}", re.UNICODE)
RE_COUNCIL = re.compile(r"مجلس\s+[\u0600-\u06FF\u0020]{3,45}", re.UNICODE)
RE_COURT = re.compile(r"محكمة\s+[\u0600-\u06FF\u0020]{3,45}", re.UNICODE)
RE_DEPARTMENT = re.compile(r"(?:دائرة|مديرية)\s+[\u0600-\u06FF\u0020]{3,45}", re.UNICODE)

# ── Scope / applicability patterns ──────────────────────────────────────────
RE_SCOPE_ALL = re.compile(
    r"(يسري على|يطبق على|ينطبق على|تسري أحكامه على)",
    re.UNICODE,
)
RE_SCOPE_ALL_GOV = re.compile(
    r"(جميع الجهات الحكومية|الوزارات والدوائر|الجهات الرسمية|المؤسسات الحكومية)",
    re.UNICODE,
)

# ── Compliance-readiness trigger phrases ────────────────────────────────────
RE_OBLIGATION = re.compile(
    r"\b(يجب|يلزم|يتعين|يتوجب|يكون ملزماً|على كل|على الجهات|على الوزارة)\b",
    re.UNICODE,
)
RE_PROHIBITION = re.compile(
    r"\b(يُحظر|يُمنع|لا يجوز|لا يُسمح|لا يحق|محظور|ممنوع)\b",
    re.UNICODE,
)
RE_DEADLINE = re.compile(
    r"(خلال مدة لا تزيد|خلال|في غضون|موعد لا يتجاوز|يجب أن يتم خلال|مدة أقصاها)",
    re.UNICODE,
)
RE_EXCEPTION = re.compile(
    r"(استثناءً من|مع مراعاة|باستثناء|على الرغم من|فيما عدا|باستثناء ما)",
    re.UNICODE,
)
RE_APPROVAL = re.compile(
    r"(بعد الحصول على موافقة|يستلزم الحصول على|بموافقة|بإذن من|بعد استئذان)",
    re.UNICODE,
)
RE_REPORTING = re.compile(
    r"(يرفع تقريراً|تقديم تقرير|الإفصاح عن|الإبلاغ عن|رفع بيان|تقرير دوري)",
    re.UNICODE,
)


# ─────────────────────────────────────────────────────────────────────────────
# ArabicTextUtils
# ─────────────────────────────────────────────────────────────────────────────

class ArabicTextUtils:
    """
    Stateless utility class for Arabic text processing in legal contexts.
    All methods are static — no instantiation required.

    Usage:
        from utils.arabic_utils import ArabicTextUtils as ATU
        normalized = ATU.normalize(raw_text)
    """

    # ── Normalization ────────────────────────────────────────────────────────

    @staticmethod
    def remove_html_artifacts(text: str) -> str:
        """Remove leftover HTML tags and decode common HTML entities."""
        # Remove tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Decode common entities
        replacements = {
            "&amp;": "&", "&nbsp;": " ", "&lt;": "<", "&gt;": ">",
            "&quot;": '"', "&#8206;": "", "&#8207;": "",
        }
        for ent, char in replacements.items():
            text = text.replace(ent, char)
        return text

    @staticmethod
    def remove_zero_width(text: str) -> str:
        """Remove zero-width and invisible Unicode characters."""
        for ch in ZERO_WIDTH:
            text = text.replace(ch, "")
        return text

    @staticmethod
    def remove_tatweel(text: str) -> str:
        """Remove Arabic tatweel (kashida) stretcher characters."""
        return text.replace(TATWEEL, "")

    @staticmethod
    def remove_harakat(text: str) -> str:
        """Remove Arabic diacritical marks (harakat)."""
        if _PYARABIC_AVAILABLE:
            return araby.strip_tashkeel(text)
        # Fallback: manual strip
        return re.sub(f"[{re.escape(HARAKAT)}]", "", text)

    @staticmethod
    def normalize_alef(text: str) -> str:
        """
        Normalize Alef variants (أ إ آ ٱ) → bare Alef (ا).
        SEARCH COPY ONLY — never apply to original_text.
        """
        for variant, bare in ALEF_VARIANTS.items():
            text = text.replace(variant, bare)
        return text

    @staticmethod
    def normalize_tamarbouta(text: str) -> str:
        """
        Normalize Tamarbouta (ة) → Heh (ه) at end of words.
        SEARCH COPY ONLY — never apply to original_text.
        Controversial in legal text; used only for search normalization.
        """
        return re.sub(r"ة\b", "ه", text)

    @staticmethod
    def normalize_yeh(text: str) -> str:
        """Normalize Alef Maqsoura (ى) → Yeh (ي) — search copy only."""
        return text.replace("\u0649", "\u064A")

    @staticmethod
    def normalize_spaces(text: str) -> str:
        """
        Collapse multiple whitespace (spaces, tabs) to single space.
        Collapse 3+ consecutive newlines to 2 newlines.
        """
        text = re.sub(r"[ \t\u00A0\u3000]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def convert_arabic_to_western_digits(text: str) -> str:
        """
        Convert Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩) to Western digits (0-9).
        Applied to normalized_text only to allow numeric comparison.
        """
        for arabic, western in ARABIC_INDIC_DIGITS.items():
            text = text.replace(arabic, western)
        return text

    @staticmethod
    def remove_page_markers(text: str) -> str:
        """Remove page number markers common in LOB page extracts."""
        # Patterns: "صفحة 1", "Page 1 of 10", "- 1 -"
        text = re.sub(r"\bصفحة\s+\d+\b", "", text)
        text = re.sub(r"\bPage\s+\d+\s+of\s+\d+\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*[-–]\s*\d+\s*[-–]\s*$", "", text, flags=re.MULTILINE)
        return text

    @classmethod
    def normalize(cls, text: str, *, for_search: bool = True) -> str:
        """
        Full normalization pipeline.

        Args:
            text:       Input Arabic text.
            for_search: If True, apply Alef/Tamarbouta/Yeh normalization
                        (search-optimized). If False, stop before those steps
                        (display-safe — preserves إ أ ة).

        Returns:
            Normalized text. Original text is NOT modified.
        """
        text = cls.remove_html_artifacts(text)
        text = cls.remove_zero_width(text)
        text = cls.remove_tatweel(text)
        text = cls.remove_harakat(text)
        text = cls.remove_page_markers(text)
        text = cls.normalize_spaces(text)
        text = cls.convert_arabic_to_western_digits(text)

        if for_search:
            text = cls.normalize_alef(text)
            text = cls.normalize_tamarbouta(text)
            text = cls.normalize_yeh(text)

        return text

    # ── Legal structure detection ───────────────────────────────────────────

    @staticmethod
    def detect_article(line: str) -> Optional[re.Match]:
        """Return regex match if line starts with an article marker."""
        return RE_ARTICLE.match(line.strip())

    @staticmethod
    def detect_part(line: str) -> Optional[re.Match]:
        """Return regex match if line is a Part (باب / جزء) header."""
        return RE_PART.match(line.strip())

    @staticmethod
    def detect_chapter(line: str) -> Optional[re.Match]:
        """Return regex match if line is a Chapter (فصل / قسم) header."""
        return RE_CHAPTER.match(line.strip())

    @staticmethod
    def detect_paragraph_letter(line: str) -> Optional[re.Match]:
        """Return regex match if line is an Arabic-letter paragraph marker."""
        return RE_PARAGRAPH_LETTER.match(line.strip())

    @staticmethod
    def detect_paragraph_ordinal(line: str) -> Optional[re.Match]:
        """Return regex match if line is an ordinal paragraph marker (أولاً:)."""
        return RE_PARAGRAPH_ORDINAL.match(line.strip())

    @staticmethod
    def detect_annex(line: str) -> Optional[re.Match]:
        """Return regex match if line is an annex/table/form header."""
        return RE_ANNEX.match(line.strip())

    @staticmethod
    def extract_legal_basis(text: str) -> list[dict]:
        """
        Find all legal basis clauses (استناداً / بناءً / عملاً / بمقتضى).
        Returns list of {'trigger': str, 'basis_text': str, 'start': int}.
        """
        results = []
        for m in RE_LEGAL_BASIS.finditer(text):
            results.append({
                "trigger": m.group(1).strip(),
                "basis_text": (m.group(1) + m.group(2)).strip(),
                "start": m.start(),
            })
        return results

    @staticmethod
    def extract_cross_references(text: str) -> list[dict]:
        """
        Find references to other legislation: رقم (X) لسنة YYYY.
        Returns list of {'doc_number': str, 'year': str, 'start': int}.
        """
        results = []
        for m in RE_DOC_REFERENCE.finditer(text):
            results.append({
                "doc_number": ArabicTextUtils.convert_arabic_to_western_digits(m.group(1)),
                "year": ArabicTextUtils.convert_arabic_to_western_digits(m.group(2)),
                "start": m.start(),
                "raw": m.group(0),
            })
        return results

    @staticmethod
    def extract_doc_number_year(text: str) -> Optional[tuple[str, str]]:
        """
        Extract (doc_number, year) from a document title or heading.
        Returns (number_str, year_str) or None.
        """
        m = RE_DOC_NUMBER_YEAR.search(text)
        if m:
            num = ArabicTextUtils.convert_arabic_to_western_digits(m.group(1))
            year = ArabicTextUtils.convert_arabic_to_western_digits(m.group(2))
            return num, year
        return None

    @staticmethod
    def extract_gazette_number(text: str) -> Optional[str]:
        """Extract Official Gazette number from text."""
        m = RE_GAZETTE.search(text)
        return m.group(1) if m else None

    @staticmethod
    def extract_dates(text: str) -> list[str]:
        """
        Find all DD/MM/YYYY or DD-MM-YYYY dates in text.
        Returns list of ISO 8601 date strings (YYYY-MM-DD).
        """
        dates = []
        for m in RE_DATE_DMY.finditer(text):
            day, month, year = m.group(1), m.group(2), m.group(3)
            try:
                # basic validation
                d, mo, y = int(day), int(month), int(year)
                if 1 <= d <= 31 and 1 <= mo <= 12 and 1900 <= y <= 2099:
                    dates.append(f"{y:04d}-{mo:02d}-{d:02d}")
            except ValueError:
                pass
        return dates

    @staticmethod
    def detect_doc_type(title: str, type_map: dict) -> str:
        """
        Detect document type from title by matching first word.
        Returns the English doc type string or 'unknown'.
        """
        title_stripped = title.strip()
        # Try longest match first (e.g., "مرسوم ملكي" before "مرسوم")
        sorted_keys = sorted(type_map.keys(), key=len, reverse=True)
        for arabic_type in sorted_keys:
            if title_stripped.startswith(arabic_type):
                return type_map[arabic_type]
        return "unknown"

    # ── Entity detection ───────────────────────────────────────────────────

    @staticmethod
    def extract_entities(text: str) -> list[dict]:
        """
        Extract named organizational entities from text.
        Returns list of {'entity_name_ar': str, 'entity_type': str, 'start': int}.
        """
        results = []

        patterns = [
            (RE_MINISTRY,   "ministry"),
            (RE_AUTHORITY,  "authority"),
            (RE_COUNCIL,    "council"),
            (RE_COURT,      "court"),
            (RE_DEPARTMENT, "department"),
        ]

        seen: set[str] = set()
        for pattern, etype in patterns:
            for m in pattern.finditer(text):
                name = m.group(0).strip()
                # Trim trailing noise (commas, periods, parentheses)
                name = re.sub(r"[\،,\.\)\(]+$", "", name).strip()
                if name not in seen and len(name) >= 5:
                    seen.add(name)
                    results.append({
                        "entity_name_ar": name,
                        "entity_type": etype,
                        "start": m.start(),
                    })

        return results

    # ── Relationship detection ───────────────────────────────────────────────

    @staticmethod
    def detect_amendment(text: str) -> bool:
        """Return True if text contains amendment trigger language."""
        return bool(RE_AMEND.search(text))

    @staticmethod
    def detect_repeal(text: str) -> bool:
        """Return True if text contains repeal trigger language."""
        return bool(RE_REPEAL.search(text))

    # ── Scope detection ─────────────────────────────────────────────────────

    @staticmethod
    def classify_scope(text: str) -> str:
        """
        Classify applicability scope of the legislation.
        Returns one of: general, government_wide, sector_specific,
                        entity_specific, internal, unknown.
        """
        from models.schema import ApplicabilityScope
        if RE_SCOPE_ALL_GOV.search(text):
            return ApplicabilityScope.GOVERNMENT_WIDE
        if RE_SCOPE_ALL.search(text):
            return ApplicabilityScope.GENERAL
        # If the title contains a single ministry → entity_specific
        entity_hits = ArabicTextUtils.extract_entities(text[:500])
        if entity_hits:
            return ApplicabilityScope.ENTITY_SPECIFIC
        return ApplicabilityScope.UNKNOWN

    # ── Compliance flag scanning ─────────────────────────────────────────────

    @staticmethod
    def scan_compliance_flags(text: str) -> dict:
        """
        Scan section text for Layer 2 compliance trigger words.
        Returns dict of boolean flags; all are False if not found.
        These flags are stored on Section records for future Layer 2 use.
        """
        return {
            "compliance_relevant": any([
                bool(RE_OBLIGATION.search(text)),
                bool(RE_PROHIBITION.search(text)),
                bool(RE_DEADLINE.search(text)),
                bool(RE_APPROVAL.search(text)),
                bool(RE_REPORTING.search(text)),
            ]),
            "contains_obligation":  bool(RE_OBLIGATION.search(text)),
            "contains_prohibition": bool(RE_PROHIBITION.search(text)),
            "contains_approval_requirement": bool(RE_APPROVAL.search(text)),
            "contains_deadline":    bool(RE_DEADLINE.search(text)),
            "contains_exception":   bool(RE_EXCEPTION.search(text)),
            "contains_reporting_requirement": bool(RE_REPORTING.search(text)),
        }

    # ── Utility ─────────────────────────────────────────────────────────────

    @staticmethod
    def contains_arabic(text: str) -> bool:
        """Return True if text contains at least one Arabic character."""
        return bool(re.search(r"[\u0600-\u06FF]", text))

    @staticmethod
    def count_words(text: str) -> int:
        """Count words in Arabic text (split on whitespace)."""
        return len(text.split())

    @staticmethod
    def ordinal_to_int(text: str) -> Optional[int]:
        """Convert Arabic ordinal word to integer (الأول → 1)."""
        cleaned = text.strip()
        return ARABIC_ORDINAL_TO_INT.get(cleaned)
