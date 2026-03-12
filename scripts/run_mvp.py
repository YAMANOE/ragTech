"""
scripts/run_mvp.py
-------------------
MVP batch runner — processes 5 target Jordanian laws from LOB.

IMPORTANT BEFORE RUNNING:
    1. Verify each URL below on lob.gov.jo — use the search to find the exact page.
    2. Run `playwright install chromium` if not already done.
    3. Adjust LOB_SELECTORS in config/settings.py after inspecting one page with devtools.

Usage:
    python scripts/run_mvp.py                        # process all 5 laws
    python scripts/run_mvp.py --only law-income-tax  # process one by slug
    python scripts/run_mvp.py --skip-fetch           # re-process from saved raw files
    python scripts/run_mvp.py --export-all           # rebuild all CSVs from structured/
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config.settings import Settings
from pipeline.exporter import LegislationExporter

# ── 5 MVP Target Laws ────────────────────────────────────────────────────────
#
# Instructions:
#   - Go to https://lob.gov.jo/AR/  and search for each law by name.
#   - Copy the full URL of the legislation detail page into "url".
#   - The doc_slug should match: {type_en}-{yr}-{number}  (see ARCHITECTURE.md §6).
#   - "url" is left as a placeholder below — REPLACE before running!
#
# Stub format example:
#   https://lob.gov.jo/AR/Details/Law/12345

MVP_TARGETS = [
    {
        "doc_slug": "law-2018-34",
        "name_ar":  "قانون ضريبة الدخل رقم (34) لسنة 2018",
        "url":      "REPLACE_WITH_REAL_LOB_URL",
        "notes":    "Primary income-tax legislation — most compliance-relevant",
    },
    {
        "doc_slug": "law-1997-22",
        "name_ar":  "قانون الشركات رقم (22) لسنة 1997",
        "url":      "REPLACE_WITH_REAL_LOB_URL",
        "notes":    "Companies law — core commercial/investment reference",
    },
    {
        "doc_slug": "regulation-civil-service-2007",
        "name_ar":  "نظام الخدمة المدنية لسنة 2007",
        "url":      "REPLACE_WITH_REAL_LOB_URL",
        "notes":    "Civil service regulation — government-wide applicability",
    },
    {
        "doc_slug": "law-1996-8",
        "name_ar":  "قانون العمل رقم (8) لسنة 1996",
        "url":      "REPLACE_WITH_REAL_LOB_URL",
        "notes":    "Labor law — employment obligations for private sector",
    },
    {
        "doc_slug": "law-2015-15",
        "name_ar":  "قانون الجرائم المعلوماتية رقم (27) لسنة 2015",
        "url":      "REPLACE_WITH_REAL_LOB_URL",
        "notes":    "Cybercrime law — IT/digital sector compliance",
    },
]

FETCH_DELAY_SECONDS = 4   # polite crawler delay between requests


def run_mvp(
    targets: list[dict],
    skip_fetch: bool = False,
    export_all_at_end: bool = True,
) -> list[dict]:
    """Process each MVP target through the full pipeline."""
    # Import here to avoid circular resolution issues at module-level
    from scripts.run_pipeline import run_pipeline

    summaries = []
    settings  = Settings()
    settings.ensure_directories()

    for i, target in enumerate(targets, start=1):
        slug    = target["doc_slug"]
        url     = target["url"]
        name_ar = target["name_ar"]

        logger.info(f"\n{'='*60}")
        logger.info(f"[MVP] ({i}/{len(targets)}) Processing: {name_ar}")
        logger.info(f"[MVP] Slug: {slug}")
        logger.info(f"[MVP] Notes: {target.get('notes', '')}")
        logger.info(f"{'='*60}")

        if url.startswith("REPLACE_"):
            logger.warning(f"[MVP] URL not set for {slug} — skipping fetch, will look for saved file.")
            url = ""

        if skip_fetch:
            url = ""   # force re-use of saved raw files

        try:
            summary = run_pipeline(
                doc_slug=slug,
                url=url,
                skip_export=False,
            )
            summary["name_ar"] = name_ar
            summaries.append(summary)

        except Exception as exc:
            logger.error(f"[MVP] Failed {slug}: {exc}")
            summaries.append({
                "doc_slug": slug,
                "name_ar":  name_ar,
                "success":  False,
                "error":    str(exc),
            })

        # Polite delay between documents
        if i < len(targets):
            logger.info(f"[MVP] Waiting {FETCH_DELAY_SECONDS}s before next document...")
            time.sleep(FETCH_DELAY_SECONDS)

    # ── Rebuild all CSVs together ───────────────────────────────────────────
    if export_all_at_end:
        logger.info("\n[MVP] Rebuilding full export CSVs from structured/ directory…")
        exporter = LegislationExporter(settings)
        exporter.export_all()
        logger.success("[MVP] Full export complete.")

    return summaries


def _print_final_report(summaries: list[dict]) -> None:
    passed  = [s for s in summaries if s.get("success")]
    failed  = [s for s in summaries if not s.get("success")]

    print("\n" + "═" * 64)
    print("  MVP PIPELINE FINAL REPORT")
    print("═" * 64)
    print(f"  Total : {len(summaries)}")
    print(f"  Passed: {len(passed)}")
    print(f"  Failed: {len(failed)}")
    print()

    for s in summaries:
        status  = "✓" if s.get("success") else "✗"
        slug    = s.get("doc_slug", "?")
        name    = s.get("name_ar", "")
        sections = s.get("sections", "?")
        errors  = s.get("validation_errors", 0)
        print(f"  {status}  {slug:<32}  sections={sections}  val_errors={errors}")
        if not s.get("success"):
            print(f"       ERROR: {s.get('error', 'unknown')}")
        elif name:
            print(f"       {name}")

    print("═" * 64)
    print(f"\n  Exports written to:  exports/relational/")
    print(f"  Graph CSVs at:       exports/graph/")
    print(f"  Structured JSON at:  data/structured/docs/")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_mvp.py",
        description="MVP batch runner — 5 Jordanian legislation laws",
    )
    p.add_argument("--only",        help="Run only one target by its doc_slug")
    p.add_argument("--skip-fetch",  dest="skip_fetch", action="store_true",
                   help="Skip fetching; reprocess from saved raw files")
    p.add_argument("--no-export-all", dest="no_export_all", action="store_true",
                   help="Skip final cross-document export rebuild")
    p.add_argument("--log-level",   dest="log_level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()

    logger.remove()
    logger.add(sys.stderr, level=args.log_level, colorize=True)
    logger.add(
        "logs/mvp_run_{time}.log",
        level="DEBUG",
        rotation="50 MB",
        encoding="utf-8",
    )

    targets = MVP_TARGETS
    if args.only:
        targets = [t for t in MVP_TARGETS if t["doc_slug"] == args.only]
        if not targets:
            slugs = ", ".join(t["doc_slug"] for t in MVP_TARGETS)
            logger.error(f"Unknown slug '{args.only}'. Available: {slugs}")
            sys.exit(1)

    summaries = run_mvp(
        targets=targets,
        skip_fetch=args.skip_fetch,
        export_all_at_end=(not args.no_export_all),
    )

    _print_final_report(summaries)

    failed_count = len([s for s in summaries if not s.get("success")])
    sys.exit(0 if failed_count == 0 else 1)
