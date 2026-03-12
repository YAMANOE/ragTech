"""
scripts/run_pipeline.py
------------------------
Full pipeline runner for a single legislation document.

Usage:
    python scripts/run_pipeline.py --url "https://lob.gov.jo/AR/..." --slug "law-2014-34"
    python scripts/run_pipeline.py --html-file data/raw/html/law-2014-34.html --slug "law-2014-34"
    python scripts/run_pipeline.py --text-file data/raw/text/law-2014-34.txt --slug "law-2014-34"

Pipeline stages run:
    fetch → parse → clean → structure → validate → export

Options:
    --url           LOB page URL to fetch
    --slug          Document slug (required, e.g., law-2014-34)
    --html-file     Path to existing raw HTML file (skip fetching)
    --text-file     Path to existing raw text file (skip fetch + parse)
    --skip-export   Run pipeline but do not write CSV exports
    --force-refetch Re-fetch even if already in source registry
    --log-level     Logging level: DEBUG, INFO, WARNING (default: INFO)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config.settings import Settings
from pipeline.cleaner import ArabicTextCleaner
from pipeline.exporter import LegislationExporter
from pipeline.fetcher import LOBFetcher
from pipeline.parser import LOBParser
from pipeline.structurer import LegislationStructurer
from pipeline.validator import PipelineValidator


def run_pipeline(
    doc_slug: str,
    url: str = "",
    html_file: str = "",
    text_file: str = "",
    skip_export: bool = False,
    force_refetch: bool = False,
) -> dict:
    """
    Run the complete pipeline for one document.

    Returns a summary dict with counts and validation status.
    """
    settings = Settings()
    settings.ensure_directories()

    # ── Component setup ────────────────────────────────────────────────────
    fetcher    = LOBFetcher(settings)
    parser     = LOBParser(settings)
    cleaner    = ArabicTextCleaner(settings)
    structurer = LegislationStructurer(settings)
    validator  = PipelineValidator(settings)
    exporter   = LegislationExporter(settings)

    raw_html = ""
    raw_text = ""
    source_url = url

    # ── Stage 1: Fetch ─────────────────────────────────────────────────────
    if url:
        if not force_refetch and fetcher.already_fetched(doc_slug):
            logger.info(f"[Pipeline] {doc_slug} already in registry. Use --force-refetch to re-fetch.")
            latest = fetcher.latest_fetch(doc_slug)
            if latest:
                html_file = latest.get("html_file_path", "")
                text_file = latest.get("text_file_path", "")
        else:
            logger.info(f"[Pipeline] STAGE 1: Fetching {url}")
            fetch_record = fetcher.fetch_sync(url, doc_slug)
            if not fetch_record.html_file_path:
                logger.error(f"[Pipeline] Fetch failed for {doc_slug}. Aborting.")
                return {"success": False, "error": "Fetch failed", "doc_slug": doc_slug}
            html_file  = fetch_record.html_file_path
            text_file  = fetch_record.text_file_path
            source_url = url

    # ── Stage 2: Parse ─────────────────────────────────────────────────────
    if html_file:
        logger.info(f"[Pipeline] STAGE 2: Parsing HTML: {html_file}")
        raw_html = Path(html_file).read_text(encoding="utf-8")
        parsed_doc = parser.parse_html(raw_html, doc_slug, source_url=source_url)
        raw_text = parsed_doc["raw_text"]

    elif text_file:
        logger.info(f"[Pipeline] STAGE 2: Parsing text file: {text_file}")
        parsed_doc = parser.parse_text_file(text_file, doc_slug, source_url=source_url)
        raw_text = parsed_doc["raw_text"]

    else:
        logger.error("[Pipeline] No input provided. Use --url, --html-file, or --text-file.")
        return {"success": False, "error": "No input", "doc_slug": doc_slug}

    if not raw_text.strip():
        logger.error(f"[Pipeline] Empty text extracted for {doc_slug}. Aborting.")
        return {"success": False, "error": "Empty text", "doc_slug": doc_slug}

    # ── Stage 3: Clean ─────────────────────────────────────────────────────
    logger.info(f"[Pipeline] STAGE 3: Cleaning text ({len(raw_text)} chars)")
    clean_output = cleaner.clean(
        raw_text,
        doc_slug=doc_slug,
        source_file=text_file or html_file,
    )

    # ── Stage 4: Structure ──────────────────────────────────────────────────
    logger.info(f"[Pipeline] STAGE 4: Structuring document")
    result = structurer.structure(parsed_doc, clean_output)

    # Attach raw file paths to document
    if result.document:
        result.document.raw_html_path = html_file or ""
        result.document.raw_text_path = text_file or ""

    # ── Stage 5: Validate ───────────────────────────────────────────────────
    logger.info(f"[Pipeline] STAGE 5: Validating")
    val_results = validator.validate(result)
    result.validation_results = val_results

    if validator.has_errors(val_results):
        logger.error(f"[Pipeline] Validation FAILED for {doc_slug}")
        # Export anyway (with warning) — don't block on warnings
        logger.warning("[Pipeline] Continuing to export despite validation errors.")

    # ── Stage 6: Export ─────────────────────────────────────────────────────
    if not skip_export:
        logger.info(f"[Pipeline] STAGE 6: Exporting CSV files")
        exporter.export_result(result)
    else:
        logger.info(f"[Pipeline] STAGE 6: Export skipped (--skip-export)")

    summary = result.summary()
    logger.success(f"[Pipeline] DONE: {doc_slug} — {summary}")
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description="Jordanian RegTech — run full pipeline for one legislation document",
    )
    p.add_argument("--url",          help="LOB page URL to fetch")
    p.add_argument("--slug",         required=True, help="Document slug (e.g. law-2014-34)")
    p.add_argument("--html-file",    dest="html_file", help="Path to existing raw HTML file")
    p.add_argument("--text-file",    dest="text_file", help="Path to existing raw text file")
    p.add_argument("--skip-export",  dest="skip_export", action="store_true")
    p.add_argument("--force-refetch", dest="force_refetch", action="store_true")
    p.add_argument("--log-level",    dest="log_level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()

    # Configure loguru
    logger.remove()
    logger.add(sys.stderr, level=args.log_level, colorize=True)

    if not args.url and not args.html_file and not args.text_file:
        logger.error("Provide --url, --html-file, or --text-file")
        sys.exit(1)

    summary = run_pipeline(
        doc_slug=args.slug,
        url=args.url or "",
        html_file=args.html_file or "",
        text_file=args.text_file or "",
        skip_export=args.skip_export,
        force_refetch=args.force_refetch,
    )

    print("\n── Pipeline Summary ──────────────────────────────")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    sys.exit(0 if summary.get("success") else 1)
