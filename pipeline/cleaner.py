"""
pipeline/cleaner.py
--------------------
Arabic text cleaning pipeline for Jordanian legislation.

Responsibilities:
  1. Accept raw text (from parser or raw text file)
  2. Apply cleaning rules in the documented order
  3. Produce original_text (lightly cleaned — no semantic changes) 
     and normalized_text (fully normalized for search/parsing)
  4. Record every rule application with statistics
  5. Save CleanOutput JSON to data/clean/{doc_slug}_clean.json
  6. Append a row to data/clean/cleaning_log.csv

CRITICAL RULES (never break these):
  - original_text is cleaned only of HTML artifacts and encoding issues
  - original_text NEVER loses: article numbers, legal references, paragraph marks
  - normalized_text is a separate copy — the original is untouched
  - Both texts are preserved side-by-side in every downstream record

Cleaning rule order (see ARCHITECTURE.md Section 8):
  1.  Strip HTML tags + decode entities
  2.  Remove zero-width characters
  3.  Remove tatweel (kashida)
  4.  Remove harakat (diacritics)
  5.  Remove page number markers
  6.  Normalize whitespace (no semantic change)
  7.  Convert Arabic-Indic digits → Western digits (normalized only)
  8.  Normalize Alef forms (normalized only)
  9.  Normalize Tamarbouta (normalized only)
  10. Normalize Yeh/Alef-Maqsoura (normalized only)

Usage:
    from pipeline.cleaner import ArabicTextCleaner
    from config.settings import Settings

    cleaner = ArabicTextCleaner(Settings())
    output  = cleaner.clean(raw_text, doc_slug="law-2014-34", source_file="...")
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import Settings
from models.schema import CleanOutput
from utils.arabic_utils import ArabicTextUtils as ATU


class ArabicTextCleaner:
    """
    Cleans Arabic legal text for the LOB legislative pipeline.
    Produces both original_text and normalized_text.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_directories()

        logger.add(
            settings.LOGS_DIR / "cleaner.log",
            rotation="10 MB",
            level="INFO",
            encoding="utf-8",
        )

    # ── Main entry point ──────────────────────────────────────────────────────

    def clean(
        self,
        raw_text: str,
        doc_slug: str,
        source_file: str = "",
    ) -> CleanOutput:
        """
        Run the full cleaning pipeline on raw Arabic legal text.

        Returns a CleanOutput with:
          - original_text:   lightly cleaned (HTML stripped, encoding fixed)
          - normalized_text: fully normalized for search/parsing
          - cleaning_log:    per-rule statistics

        The output is also saved to disk:
          data/clean/{doc_slug}_clean.json
          data/clean/cleaning_log.csv (appended)
        """
        rules_applied: list[str] = []
        cleaning_log: list[dict] = []

        # ── Stage 1: Produce original_text ───────────────────────────────────
        # Apply only non-semantic cleaners. Never alter Arabic letters,
        # numbers, punctuation, or document structure.

        original_text = raw_text

        original_text, log1 = self._apply_rule(
            original_text,
            "strip_html_artifacts",
            ATU.remove_html_artifacts,
        )
        rules_applied.append("strip_html_artifacts")
        cleaning_log.append(log1)

        original_text, log2 = self._apply_rule(
            original_text,
            "remove_zero_width",
            ATU.remove_zero_width,
        )
        rules_applied.append("remove_zero_width")
        cleaning_log.append(log2)

        original_text, log3 = self._apply_rule(
            original_text,
            "remove_tatweel",
            ATU.remove_tatweel,
        )
        rules_applied.append("remove_tatweel")
        cleaning_log.append(log3)

        original_text, log4 = self._apply_rule(
            original_text,
            "remove_harakat",
            ATU.remove_harakat,
        )
        rules_applied.append("remove_harakat")
        cleaning_log.append(log4)

        original_text, log5 = self._apply_rule(
            original_text,
            "remove_page_markers",
            ATU.remove_page_markers,
        )
        rules_applied.append("remove_page_markers")
        cleaning_log.append(log5)

        original_text, log6 = self._apply_rule(
            original_text,
            "normalize_spaces",
            ATU.normalize_spaces,
        )
        rules_applied.append("normalize_spaces")
        cleaning_log.append(log6)

        # ── Stage 2: Produce normalized_text ─────────────────────────────────
        # Start from the already-cleaned original_text, then apply
        # search-optimized normalization (Alef, Tamarbouta, Yeh, digits).

        normalized_text = original_text

        normalized_text, log7 = self._apply_rule(
            normalized_text,
            "convert_arabic_to_western_digits",
            ATU.convert_arabic_to_western_digits,
        )
        rules_applied.append("convert_arabic_to_western_digits")
        cleaning_log.append(log7)

        normalized_text, log8 = self._apply_rule(
            normalized_text,
            "normalize_alef",
            ATU.normalize_alef,
        )
        rules_applied.append("normalize_alef")
        cleaning_log.append(log8)

        normalized_text, log9 = self._apply_rule(
            normalized_text,
            "normalize_tamarbouta",
            ATU.normalize_tamarbouta,
        )
        rules_applied.append("normalize_tamarbouta")
        cleaning_log.append(log9)

        normalized_text, log10 = self._apply_rule(
            normalized_text,
            "normalize_yeh",
            ATU.normalize_yeh,
        )
        rules_applied.append("normalize_yeh")
        cleaning_log.append(log10)

        # ── Stage 3: Sanity checks ────────────────────────────────────────────
        warn_notes = self._sanity_check(original_text, doc_slug)
        for note in warn_notes:
            cleaning_log.append({"rule": "sanity_check", "note": note, "changes": 0})

        # ── Assemble output ───────────────────────────────────────────────────
        output = CleanOutput(
            doc_slug=doc_slug,
            source_file=source_file,
            cleaned_at=datetime.now(timezone.utc).isoformat(),
            original_text=original_text,
            normalized_text=normalized_text,
            cleaning_rules_applied=rules_applied,
            cleaning_log=cleaning_log,
        )

        total_changes = sum(r.get("changes", 0) for r in cleaning_log)
        logger.info(
            f"[Cleaner] {doc_slug}: {total_changes} total cleaning changes, "
            f"{len(warn_notes)} warnings"
        )

        # ── Persist ───────────────────────────────────────────────────────────
        self._save_clean_json(output)
        self._append_cleaning_log(output, warn_notes)

        return output

    def clean_section_text(
        self,
        text: str,
    ) -> tuple[str, str]:
        """
        Clean a single section's text (not a full document).
        Returns (original_text, normalized_text).
        Used by Structurer for per-section cleaning.
        """
        # Light clean for original
        orig = ATU.remove_html_artifacts(text)
        orig = ATU.remove_zero_width(orig)
        orig = ATU.remove_tatweel(orig)
        orig = ATU.remove_harakat(orig)
        orig = ATU.normalize_spaces(orig)

        # Full normalization for search
        norm = ATU.convert_arabic_to_western_digits(orig)
        norm = ATU.normalize_alef(norm)
        norm = ATU.normalize_tamarbouta(norm)
        norm = ATU.normalize_yeh(norm)

        return orig, norm

    # ── Rule application helper ───────────────────────────────────────────────

    @staticmethod
    def _apply_rule(
        text: str,
        rule_name: str,
        fn,
    ) -> tuple[str, dict]:
        """
        Apply a cleaning function. Return (new_text, log_entry).
        Log entry includes character-level change count.
        """
        before_len = len(text)
        new_text = fn(text)
        changes = before_len - len(new_text)
        return new_text, {
            "rule": rule_name,
            "chars_before": before_len,
            "chars_after": len(new_text),
            "changes": changes,
        }

    # ── Sanity checks ─────────────────────────────────────────────────────────

    @staticmethod
    def _sanity_check(text: str, doc_slug: str) -> list[str]:
        """
        Run post-cleaning sanity checks. Return list of warning strings.
        """
        warnings = []

        if not ATU.contains_arabic(text):
            warnings.append(f"CRITICAL: No Arabic characters in cleaned text for {doc_slug}")

        if len(text) < 200:
            warnings.append(
                f"WARN: Very short cleaned text ({len(text)} chars) for {doc_slug}"
            )

        # Check that article pattern still works after cleaning
        article_hits = len(re.findall(r"المادة", text))
        if article_hits == 0:
            warnings.append(
                f"WARN: No المادة patterns found in {doc_slug} — "
                "may be instructions or decision with no articles"
            )

        return warnings

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_clean_json(self, output: CleanOutput) -> None:
        """Save CleanOutput as {doc_slug}_clean.json."""
        path = self.settings.CLEAN_DIR / f"{output.doc_slug}_clean.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output.to_dict(), f, ensure_ascii=False, indent=2)
        logger.debug(f"[Cleaner] Saved: {path}")

    def _append_cleaning_log(
        self,
        output: CleanOutput,
        warn_notes: list[str],
    ) -> None:
        """Append a summary row to the master cleaning_log.csv."""
        log_path = self.settings.CLEANING_LOG_PATH
        write_header = not log_path.exists()
        fields = [
            "doc_slug", "cleaned_at", "source_file",
            "rules_count", "total_char_changes", "warnings",
        ]
        total_changes = sum(r.get("changes", 0) for r in output.cleaning_log)
        row = {
            "doc_slug":           output.doc_slug,
            "cleaned_at":         output.cleaned_at,
            "source_file":        output.source_file,
            "rules_count":        len(output.cleaning_rules_applied),
            "total_char_changes": total_changes,
            "warnings":           "; ".join(warn_notes) if warn_notes else "",
        }
        with open(log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=fields, delimiter=self.settings.CSV_DELIMITER
            )
            if write_header:
                writer.writeheader()
            writer.writerow(row)

    # ── Load helpers ─────────────────────────────────────────────────────────

    def load_clean_output(self, doc_slug: str) -> Optional[CleanOutput]:
        """Load a previously saved CleanOutput from disk."""
        path = self.settings.CLEAN_DIR / f"{doc_slug}_clean.json"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return CleanOutput(**data)
