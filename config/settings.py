"""
config/settings.py
------------------
Central configuration for the Jordanian legislative intelligence pipeline.

All paths, LOB selectors, and pipeline parameters live here.
Update LOB_CONTENT_SELECTORS after inspecting the actual DOM
with Playwright screenshot / browser dev tools.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Project root ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(
    os.getenv("PROJECT_ROOT", Path(__file__).resolve().parent.parent)
)


class Settings:
    """All pipeline settings in one place."""

    # ── LOB website ─────────────────────────────────────────────────────────
    LOB_BASE_URL: str = os.getenv("LOB_BASE_URL", "https://lob.gov.jo")

    # ── Playwright ───────────────────────────────────────────────────────────
    PLAYWRIGHT_TIMEOUT: int = int(os.getenv("PLAYWRIGHT_TIMEOUT", 30_000))   # ms
    FETCH_DELAY_SECONDS: float = float(os.getenv("FETCH_DELAY_SECONDS", 2.0))
    PLAYWRIGHT_HEADLESS: bool = True

    # ── LOB page DOM selectors ────────────────────────────────────────────────
    # ASSUMPTION: These are best-guess selectors based on typical Jordanian
    # government portal patterns. Verify by inspecting actual LOB pages and
    # update as needed.
    LOB_SELECTORS = {
        # Primary container for the full legislation text
        # Verified 2026-03-11 against live LOB Angular pages (lob.gov.jo)
        "content_body": [
            "div.clicked-legislation-content",      # ✓ confirmed — main legislation text
            "div.main-content",                     # outer wrapper fallback
            "div[dir='rtl']",                       # last-resort: any RTL div
        ],
        # Title of the legislation
        "title": [
            "div.clicked-legislation-header h3",    # ✓ confirmed — e.g. "قانون صندوق التكافل…"
            "div.clicked-legislation-header h2",
            "div.clicked-legislation-header h1",
            "h3.animated",                          # alternate class pattern
        ],
        # Metadata block (number, year, gazette, dates)
        "metadata_block": [
            "div.clicked-legislation-body",         # ✓ confirmed — contains tabs: metadata, gazette, amendments
            "div.clicked-legislation-content",
        ],
        # Selector to wait for before extracting (ensures Angular render is done)
        "wait_for": "div.clicked-legislation-header",
    }

    # ── Paths ────────────────────────────────────────────────────────────────
    DATA_DIR: Path = PROJECT_ROOT / "data"
    RAW_DIR: Path = DATA_DIR / "raw"
    RAW_HTML_DIR: Path = RAW_DIR / "html"
    RAW_TEXT_DIR: Path = RAW_DIR / "text"
    SOURCE_REGISTRY_PATH: Path = RAW_DIR / "source_registry.csv"

    CLEAN_DIR: Path = DATA_DIR / "clean"
    CLEANING_LOG_PATH: Path = CLEAN_DIR / "cleaning_log.csv"

    STRUCTURED_DIR: Path = DATA_DIR / "structured"
    STRUCTURED_DOCS_DIR: Path = STRUCTURED_DIR / "docs"
    STRUCTURED_DOCUMENTS_PATH: Path = STRUCTURED_DIR / "documents.json"
    STRUCTURED_VERSIONS_PATH: Path = STRUCTURED_DIR / "versions.json"
    STRUCTURED_SECTIONS_PATH: Path = STRUCTURED_DIR / "sections.json"
    STRUCTURED_ENTITIES_PATH: Path = STRUCTURED_DIR / "entities.json"
    STRUCTURED_TOPICS_PATH: Path = STRUCTURED_DIR / "topics.json"
    STRUCTURED_RELATIONSHIPS_PATH: Path = STRUCTURED_DIR / "relationships.json"

    EXPORTS_DIR: Path = PROJECT_ROOT / "exports"
    RELATIONAL_DIR: Path = EXPORTS_DIR / "relational"
    GRAPH_DIR: Path = EXPORTS_DIR / "graph"
    GRAPH_FUTURE_DIR: Path = GRAPH_DIR / "future"

    # ── Logs ────────────────────────────────────────────────────────────────
    LOGS_DIR: Path = PROJECT_ROOT / "logs"

    # ── CSV export settings ──────────────────────────────────────────────────
    CSV_DELIMITER: str = "|"          # Pipe avoids conflicts with Arabic text
    CSV_ENCODING: str = "utf-8-sig"   # BOM for Excel Arabic compatibility
    CSV_NULL: str = ""                # Empty string for NULL values

    # ── Topic taxonomy ───────────────────────────────────────────────────────
    TOPICS_CONFIG_PATH: Path = PROJECT_ROOT / "config" / "topics.yaml"

    # ── Parsing thresholds ───────────────────────────────────────────────────
    TOPIC_CONFIDENCE_THRESHOLD: float = 0.4
    RELATIONSHIP_CONFIDENCE_THRESHOLD: float = 0.5

    # ── Document type mapping (Arabic → English slug) ─────────────────────────
    DOC_TYPE_MAP: dict[str, str] = {
        "دستور": "constitution",
        "قانون": "law",
        "نظام": "regulation",
        "تعليمات": "instruction",
        "قرار": "decision",
        "منشور": "circular",
        "تعميم": "circular",
        "اتفاقية": "agreement",
        "معاهدة": "treaty",
        "مرسوم ملكي": "royal_decree",
        "مرسوم": "royal_decree",
        "أمر ملكي": "royal_order",
        "إرادة ملكية": "royal_will",
        "إعلان": "declaration",
    }

    # ── Section type display names ─────────────────────────────────────────
    SECTION_TYPE_LABELS: dict[str, list[str]] = {
        "part":      ["الجزء", "الباب"],
        "chapter":   ["الفصل", "القسم"],
        "article":   ["المادة"],
        "preamble":  [],     # detected contextually (text before first article)
        "annex":     ["ملحق", "جدول", "نموذج"],
    }

    # ── Ordinal number map (Arabic text → integer) ────────────────────────
    ARABIC_ORDINALS: dict[str, int] = {
        "الأول": 1, "الأولى": 1, "الأولى": 1,
        "الثاني": 2, "الثانية": 2,
        "الثالث": 3, "الثالثة": 3,
        "الرابع": 4, "الرابعة": 4,
        "الخامس": 5, "الخامسة": 5,
        "السادس": 6, "السادسة": 6,
        "السابع": 7, "السابعة": 7,
        "الثامن": 8, "الثامنة": 8,
        "التاسع": 9, "التاسعة": 9,
        "العاشر": 10, "العاشرة": 10,
        "الحادي عشر": 11, "الثاني عشر": 12,
    }

    def ensure_directories(self) -> None:
        """Create all required directories if they do not exist."""
        dirs = [
            self.RAW_HTML_DIR, self.RAW_TEXT_DIR,
            self.CLEAN_DIR, self.STRUCTURED_DOCS_DIR,
            self.RELATIONAL_DIR, self.GRAPH_DIR, self.GRAPH_FUTURE_DIR,
            self.LOGS_DIR,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
