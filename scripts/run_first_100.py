#!/usr/bin/env python3
"""
scripts/run_first_100.py
-------------------------
Batch pipeline runner — scrapes the LOB search/listing page, extracts up to
N legislation items, then runs each through the existing pipeline:
  fetch → parse → clean → structure → validate → export

Saved outputs:
  data/indexes/search_results_first_100.json   — raw listing metadata
  data/structured/docs/{slug}.json             — one canonical JSON per law
  data/structured/documents_index.json         — cumulative browsable index
  data/reports/first_100_batch_report.json     — final batch summary

Usage:
  python scripts/run_first_100.py                          # first 100 laws
  python scripts/run_first_100.py --limit 5                # only 5
  python scripts/run_first_100.py --limit 20 --active-only # 20 active laws
  python scripts/run_first_100.py --law-type قانون          # filter by type
  python scripts/run_first_100.py --resume --limit 100     # skip processed
  python scripts/run_first_100.py --force-refetch --limit 5

Run instructions:
  5 laws:   python scripts/run_first_100.py --limit 5  --log-level INFO
  20 laws:  python scripts/run_first_100.py --limit 20 --log-level INFO
  100 laws: python scripts/run_first_100.py --limit 100
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from config.settings import Settings
from utils.id_generator import IDGenerator as IDG

# ── LOB URL constants ─────────────────────────────────────────────────────────

_LOB_WWW          = "https://www.lob.gov.jo"
_LOB_SEARCH_URL   = "https://www.lob.gov.jo/?v=2&lang=ar#!/SearchLegislation"
_LOB_DETAIL_TMPL  = (
    "https://www.lob.gov.jo/?v=2&lang=ar"
    "#!/LegislationDetails?LegislationID={leg_id}"
    "&LegislationType={type_id}&isMod=false"
)

# LegislationType numeric IDs (empirically identified from live URLs)
_LEG_TYPE_ID: dict[str, int] = {
    "دستور":       1,
    "قانون":       2,
    "نظام":        3,
    "نظام داخلي":  3,
    "تعليمات":     4,
    "قرار":        5,
    "منشور":       6,
    "أمر ملكي":    7,
    "إرادة ملكية": 8,
    "مرسوم ملكي":  9,
    "اتفاقية":     10,
    "معاهدة":      11,
}
_TYPE_ID_TO_AR: dict[int, str] = {v: k for k, v in _LEG_TYPE_ID.items()}
_DEFAULT_TYPE_ID = 2  # law

_AR_TO_EN_TYPE: dict[str, str] = {
    "دستور":       "constitution",
    "قانون":       "law",
    "نظام":        "regulation",
    "نظام داخلي":  "regulation",
    "تعليمات":     "instruction",
    "قرار":        "decision",
    "منشور":       "circular",
    "أمر ملكي":    "royal_order",
    "إرادة ملكية": "royal_will",
    "مرسوم ملكي":  "royal_decree",
    "اتفاقية":     "agreement",
    "معاهدة":      "treaty",
}

_AR_TO_EN_STATUS: dict[str, str] = {
    "نافذ":   "active",
    "نافذة":  "active",
    "ملغى":   "repealed",
    "ملغية":  "repealed",
    "معدّل":  "amended",
    "معدل":   "amended",
    "مؤقت":   "draft",
}

# Regex helpers
_RE_LEG_ID   = re.compile(r"LegislationID=(\d+)")
_RE_LEG_TYPE = re.compile(r"LegislationType=(\d+)")
_RE_YEAR     = re.compile(r"لسنة\s*(\d{4})")
_RE_NUM      = re.compile(r"رقم\s*\(?(\d+)\)?")


# ── Slug builder ──────────────────────────────────────────────────────────────

def _make_slug(item: dict) -> str:
    """
    Build a canonical doc_slug from listing metadata.
    Priority:
      1. {type}-{year}-{number}   e.g. law-2025-5
      2. legislation-{id}         reliable fallback
    """
    doc_type = item.get("doc_type_en", "")
    year     = item.get("issue_year")
    number   = item.get("doc_number", "")
    leg_id   = item.get("legislation_id", "")

    if doc_type and year and number:
        return IDG.doc_slug(doc_type, int(year), str(number))

    return f"legislation-{leg_id}"


# ── LOB listing scraper ───────────────────────────────────────────────────────

class LOBListingScraper:
    """
    Navigates the LOB Angular SPA search/listing page with Playwright and
    returns a list of legislation item dicts.

    The LOB site is an AngularJS SPA.  Result rows are in a <table> rendered
    by Angular inside the hash-route #!/SearchLegislation.  Each row contains
    detail-page links whose hrefs contain LegislationID= — that's the stable
    extraction anchor regardless of exact table layout.
    """

    # Candidate selectors for result table rows (tried in order)
    _ROW_SELECTORS = [
        "table.table tbody tr",
        "table.legislation-list tbody tr",
        "tbody tr.ng-scope",
        "tbody tr[ng-repeat]",
        "table tbody tr",
    ]

    # Pagination "next page" button selectors
    _NEXT_PAGE_SELECTORS = [
        "a[aria-label='Next']",
        "a[aria-label='التالي']",
        "li.next:not(.disabled) a",
        ".pagination li:last-child:not(.disabled) a",
        "li.pagination-next:not(.disabled) a",
        "a.next",
    ]

    # Search-trigger button selectors
    _SEARCH_BTN_SELECTORS = [
        "button[type='submit']",
        "button.search-btn",
        "button.btn-search",
        "input[type='submit']",
    ]

    # Wait for this selector to confirm results have rendered
    _RESULTS_READY_SELECTOR = (
        "table.table tbody tr, "
        "tbody tr.ng-scope, "
        "div.no-data, "
        "span.no-results"
    )

    def __init__(self, settings: Settings):
        self.settings = settings

    def scrape_sync(
        self,
        limit: int = 100,
        law_type_ar: Optional[str] = None,
        active_only: bool = False,
    ) -> list[dict]:
        """Synchronous entry point."""
        return asyncio.run(self.scrape_listing(limit, law_type_ar, active_only))

    async def scrape_listing(
        self,
        limit: int = 100,
        law_type_ar: Optional[str] = None,
        active_only: bool = False,
    ) -> list[dict]:
        """
        Navigate the LOB search page and extract up to `limit` legislation
        items with metadata.

        Returns list of dicts with keys:
          legislation_id, legislation_type_id, title_ar, doc_type_ar,
          doc_type_en, doc_number, issue_year, source_status_text,
          status_normalized, detail_url, doc_slug
        """
        from playwright.async_api import async_playwright

        results: list[dict] = []
        seen_ids: set[str] = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.settings.PLAYWRIGHT_HEADLESS,
            )
            context = await browser.new_context(
                locale="ar-JO",
                extra_http_headers={
                    "Accept-Language": "ar,en;q=0.8",
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
            )
            page = await context.new_page()

            try:
                logger.info(f"[Scraper] Navigating to: {_LOB_SEARCH_URL}")
                await page.goto(
                    _LOB_SEARCH_URL,
                    wait_until="networkidle",
                    timeout=self.settings.PLAYWRIGHT_TIMEOUT,
                )

                # Apply optional form filters
                await self._apply_filters(page, law_type_ar, active_only)

                # Click "Search" if a search button is present
                await self._trigger_search(page)

                # Wait for Angular to render results
                await self._wait_for_results(page)

                page_num = 0
                while len(results) < limit:
                    page_num += 1
                    logger.info(
                        f"[Scraper] Page {page_num} — "
                        f"{len(results)}/{limit} items so far"
                    )

                    new_items = await self._extract_rows(page, seen_ids)
                    if not new_items:
                        logger.info("[Scraper] No items found on this page — stopping.")
                        break

                    for item in new_items:
                        if len(results) >= limit:
                            break
                        results.append(item)
                        seen_ids.add(item["legislation_id"])

                    if len(results) >= limit:
                        break

                    has_next = await self._goto_next_page(page)
                    if not has_next:
                        logger.info("[Scraper] No next page button — end of listing.")
                        break

                    await asyncio.sleep(2)  # polite inter-page delay

            except Exception as exc:
                logger.error(f"[Scraper] Error during scraping: {exc}")
                try:
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    ss = self.settings.LOGS_DIR / f"scraper_error_{ts}.png"
                    await page.screenshot(path=str(ss), full_page=True)
                    logger.info(f"[Scraper] Error screenshot saved: {ss}")
                except Exception:
                    pass
            finally:
                await browser.close()

        logger.info(f"[Scraper] Collected {len(results)} items")
        return results[:limit]

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _apply_filters(
        self,
        page,
        law_type_ar: Optional[str],
        active_only: bool,
    ) -> None:
        """Optionally fill in the LOB search form filters."""
        if law_type_ar:
            for sel in [
                "select[ng-model*='Type']", "select[ng-model*='type']",
                "#LegislationTypeID", "select[name*='Type']",
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.select_option(label=law_type_ar)
                        logger.debug(f"[Scraper] Type filter set: {law_type_ar}")
                        break
                except Exception:
                    continue

        if active_only:
            for sel in [
                "select[ng-model*='Status']", "select[ng-model*='status']",
                "#LegislationStatusID", "select[name*='Status']",
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        for label in ["نافذ", "نافذة", "Active"]:
                            try:
                                await el.select_option(label=label)
                                logger.debug("[Scraper] Status filter set: active")
                                break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue

    async def _trigger_search(self, page) -> None:
        """Click a search/submit button if one exists on the page."""
        for sel in self._SEARCH_BTN_SELECTORS:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await asyncio.sleep(2)
                    logger.debug(f"[Scraper] Clicked search button ({sel})")
                    return
            except Exception:
                continue

    async def _wait_for_results(self, page) -> None:
        """Wait for result rows to appear after navigation/search."""
        try:
            await page.wait_for_selector(
                self._RESULTS_READY_SELECTOR,
                timeout=self.settings.PLAYWRIGHT_TIMEOUT // 2,
                state="visible",
            )
        except Exception:
            logger.warning("[Scraper] Results selector not found — scraping as-is")

    async def _extract_rows(
        self, page, seen_ids: set[str]
    ) -> list[dict]:
        """Extract all new legislation items from the current page view."""
        items: list[dict] = []

        # Strategy 1: structured table rows
        for row_sel in self._ROW_SELECTORS:
            rows = await page.query_selector_all(row_sel)
            if not rows:
                continue
            for row in rows:
                item = await self._parse_table_row(row)
                if item and item["legislation_id"] not in seen_ids:
                    items.append(item)
            if items:
                logger.debug(
                    f"[Scraper] Selector '{row_sel}' → {len(items)} new items"
                )
                return items

        # Strategy 2: scan all legislation detail links on the page
        logger.debug("[Scraper] Falling back to full-page href scan")
        links = await page.query_selector_all("a[href*='LegislationID']")
        for link in links:
            href  = await link.get_attribute("href") or ""
            title = (await link.inner_text()).strip()
            item = _item_from_href(href, title)
            if item and item["legislation_id"] not in seen_ids:
                items.append(item)

        return items

    async def _parse_table_row(self, row) -> Optional[dict]:
        """
        Parse one <tr> to extract metadata.
        The LOB table columns are typically: Type | Number | Title | Year | Status
        (exact column order can vary — we use heuristics).
        """
        try:
            link = await row.query_selector("a[href*='LegislationID']")
            if not link:
                link = await row.query_selector("a")
            if not link:
                return None

            href       = await link.get_attribute("href") or ""
            title_text = (await link.inner_text()).strip()

            if "LegislationID" not in href:
                return None

            item = _item_from_href(href, title_text)
            if not item:
                return None

            # Enrich from sibling <td> cells
            cells = await row.query_selector_all("td")
            for cell in cells:
                text = (await cell.inner_text()).strip()
                if not text:
                    continue
                # 4-digit year
                if re.fullmatch(r"1[89]\d\d|20[0-2]\d", text):
                    item["issue_year"] = int(text)
                # Known status word
                if text in _AR_TO_EN_STATUS:
                    item["source_status_text"] = text
                    item["status_normalized"]  = _AR_TO_EN_STATUS[text]
                # Known doc type
                if text in _AR_TO_EN_TYPE and not item.get("doc_type_ar"):
                    item["doc_type_ar"] = text
                    item["doc_type_en"] = _AR_TO_EN_TYPE[text]
                # Short pure-digit string = number
                if re.fullmatch(r"\d{1,4}", text) and not item.get("doc_number"):
                    item["doc_number"] = text

            item["doc_slug"] = _make_slug(item)
            return item

        except Exception as exc:
            logger.debug(f"[Scraper] Row parse error: {exc}")
            return None

    async def _goto_next_page(self, page) -> bool:
        """
        Attempt to navigate to the next result page.
        Returns True if a next-page click was performed.
        """
        for sel in self._NEXT_PAGE_SELECTORS:
            try:
                btn = await page.query_selector(sel)
                if not btn:
                    continue
                disabled = await btn.evaluate(
                    "el => !!(el.closest('li')?.classList.contains('disabled') "
                    "|| el.hasAttribute('disabled') "
                    "|| el.closest('li')?.classList.contains('disable'))"
                )
                if disabled:
                    return False
                await btn.click()
                await asyncio.sleep(2)
                await self._wait_for_results(page)
                return True
            except Exception:
                continue
        return False


# ── Item constructor (module-level, used by scraper) ─────────────────────────

def _item_from_href(href: str, title: str = "") -> Optional[dict]:
    """Build a listing item dict from a legislation detail href + title text."""
    m_id   = _RE_LEG_ID.search(href)
    m_type = _RE_LEG_TYPE.search(href)

    if not m_id:
        return None

    leg_id  = m_id.group(1)
    type_id = int(m_type.group(1)) if m_type else _DEFAULT_TYPE_ID

    doc_type_ar = _TYPE_ID_TO_AR.get(type_id, "")
    doc_type_en = _AR_TO_EN_TYPE.get(doc_type_ar, "law")

    issue_year = None
    doc_number = None
    m_year = _RE_YEAR.search(title)
    m_num  = _RE_NUM.search(title)
    if m_year:
        issue_year = int(m_year.group(1))
    if m_num:
        doc_number = m_num.group(1)

    detail_url = _LOB_DETAIL_TMPL.format(leg_id=leg_id, type_id=type_id)

    item: dict = {
        "legislation_id":      leg_id,
        "legislation_type_id": type_id,
        "title_ar":            title,
        "doc_type_ar":         doc_type_ar,
        "doc_type_en":         doc_type_en,
        "doc_number":          doc_number,
        "issue_year":          issue_year,
        "source_status_text":  "",
        "status_normalized":   "active",   # default until scraper finds real status
        "detail_url":          detail_url,
        "doc_slug":            "",         # set by caller via _make_slug()
    }
    item["doc_slug"] = _make_slug(item)
    return item


# ── Directory helpers ─────────────────────────────────────────────────────────

def _ensure_output_dirs(settings: Settings) -> tuple[Path, Path]:
    """Create data/indexes/ and data/reports/ dirs; return their paths."""
    indexes_dir = settings.DATA_DIR / "indexes"
    reports_dir = settings.DATA_DIR / "reports"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    return indexes_dir, reports_dir


# ── Single-item pipeline runner with retry ───────────────────────────────────

def _run_one(
    item: dict,
    force_refetch: bool = False,
    retries: int = 3,
    retry_delay: float = 8.0,
) -> dict:
    """
    Run the full pipeline for one legislation item.
    Wraps run_pipeline() with retry logic.
    Returns an enriched result dict with pipeline outcome.
    """
    # Import here to reuse the existing run_pipeline function
    from scripts.run_pipeline import run_pipeline

    slug = item["doc_slug"]
    url  = item["detail_url"]

    for attempt in range(1, retries + 1):
        try:
            summary = run_pipeline(
                doc_slug=slug,
                url=url,
                force_refetch=force_refetch,
            )
            return {
                **item,
                "pipeline_success":    summary.get("success", False),
                "pipeline_sections":   summary.get("sections", 0),
                "pipeline_entities":   summary.get("entities", 0),
                "pipeline_topics":     summary.get("topics", 0),
                "pipeline_relationships": summary.get("relationships", 0),
                "validation_passed":   summary.get("validation_passed", False),
                "pipeline_errors":     summary.get("errors", []),
                "attempt":             attempt,
            }
        except Exception as exc:
            logger.warning(
                f"[Batch] {slug} attempt {attempt}/{retries} failed: {exc}"
            )
            if attempt < retries:
                logger.info(f"[Batch] Retrying in {retry_delay}s …")
                time.sleep(retry_delay)
            else:
                return {
                    **item,
                    "pipeline_success":    False,
                    "pipeline_sections":   0,
                    "pipeline_entities":   0,
                    "pipeline_topics":     0,
                    "pipeline_relationships": 0,
                    "validation_passed":   False,
                    "pipeline_errors":     [str(exc)],
                    "attempt":             attempt,
                }


# ── Batch runner ──────────────────────────────────────────────────────────────

def run_batch(
    limit: int = 100,
    law_type_ar: Optional[str] = None,
    active_only: bool = False,
    force_refetch: bool = False,
    resume: bool = False,
    retries: int = 3,
) -> dict:
    """
    Full batch pipeline:
      1. Scrape LOB listing to get up to `limit` items
      2. Save search index JSON
      3. For each item, run the pipeline (skip if --resume and already done)
      4. Save batch report JSON
      5. Return aggregate summary dict
    """
    started_at = datetime.now(timezone.utc).isoformat()
    settings   = Settings()
    settings.ensure_directories()
    indexes_dir, reports_dir = _ensure_output_dirs(settings)

    # ── Step 1: Scrape listing ─────────────────────────────────────────────
    logger.info(
        f"[Batch] Scraping LOB listing: limit={limit}, "
        f"type={law_type_ar or 'all'}, active_only={active_only}"
    )
    scraper = LOBListingScraper(settings)
    listing = scraper.scrape_sync(
        limit=limit,
        law_type_ar=law_type_ar,
        active_only=active_only,
    )

    if not listing:
        logger.error("[Batch] Scraper returned 0 items. Check LOB page selectors.")
        return {
            "success":        False,
            "items_scraped":  0,
            "items_processed": 0,
            "error":          "Scraper returned 0 items",
        }

    # ── Step 2: Save search index ──────────────────────────────────────────
    index_path = indexes_dir / "search_results_first_100.json"
    index_payload = {
        "scraped_at":    started_at,
        "limit":         limit,
        "law_type_ar":   law_type_ar,
        "active_only":   active_only,
        "total_scraped": len(listing),
        "items":         listing,
    }
    index_path.write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"[Batch] Search index saved: {index_path} ({len(listing)} items)")

    # ── Step 3: Process each item ──────────────────────────────────────────
    results: list[dict] = []
    skipped  = 0
    success  = 0
    failed   = 0

    for i, item in enumerate(listing, start=1):
        slug = item["doc_slug"]
        logger.info(
            f"\n{'─'*60}"
            f"\n[Batch] ({i}/{len(listing)}) {slug}"
            f"\n  title: {item.get('title_ar', '')[:80]}"
            f"\n  url:   {item['detail_url']}"
        )

        # --resume: skip if already fully structured
        if resume and not force_refetch:
            structured_path = settings.STRUCTURED_DOCS_DIR / f"{slug}.json"
            if structured_path.exists():
                logger.info(f"[Batch] Skipping (already processed): {slug}")
                results.append({
                    **item,
                    "pipeline_success":    True,
                    "pipeline_sections":   0,
                    "pipeline_entities":   0,
                    "pipeline_topics":     0,
                    "pipeline_relationships": 0,
                    "validation_passed":   True,
                    "pipeline_errors":     [],
                    "attempt":             0,
                    "skipped":             True,
                })
                skipped += 1
                continue

        result = _run_one(item, force_refetch=force_refetch, retries=retries)
        result["skipped"] = False

        if result["pipeline_success"]:
            success += 1
        else:
            failed += 1

        results.append(result)

        # Polite inter-request delay (except for the last item)
        if i < len(listing):
            time.sleep(settings.FETCH_DELAY_SECONDS)

    # ── Step 4: Compute quality metrics ───────────────────────────────────
    processed = [r for r in results if not r.get("skipped")]
    successful = [r for r in processed if r["pipeline_success"]]

    quality = {
        "has_legal_basis_pct": _pct(
            sum(1 for r in successful
                if _has_field(settings, r["doc_slug"], "legal_basis_text")),
            len(successful),
        ),
        "has_publication_date_pct": _pct(
            sum(1 for r in successful
                if _has_field(settings, r["doc_slug"], "publication_date")),
            len(successful),
        ),
        "has_topic_assignments_pct": _pct(
            sum(1 for r in successful if r["pipeline_topics"] > 0),
            len(successful),
        ),
        "has_entities_pct": _pct(
            sum(1 for r in successful if r["pipeline_entities"] > 0),
            len(successful),
        ),
        "has_relationships_pct": _pct(
            sum(1 for r in successful if r["pipeline_relationships"] > 0),
            len(successful),
        ),
        "validation_passed_pct": _pct(
            sum(1 for r in successful if r["validation_passed"]),
            len(successful),
        ),
        "avg_sections": (
            round(
                sum(r["pipeline_sections"] for r in successful) / len(successful), 1
            ) if successful else 0
        ),
    }

    # ── Step 5: Save batch report ──────────────────────────────────────────
    finished_at = datetime.now(timezone.utc).isoformat()
    report = {
        "batch_started_at":  started_at,
        "batch_finished_at": finished_at,
        "config": {
            "limit":         limit,
            "law_type_ar":   law_type_ar,
            "active_only":   active_only,
            "force_refetch": force_refetch,
            "resume":        resume,
            "retries":       retries,
        },
        "summary": {
            "items_scraped":   len(listing),
            "items_processed": len(processed),
            "items_skipped":   skipped,
            "success":         success,
            "failed":          failed,
            "success_rate_pct": _pct(success, len(processed)),
        },
        "data_quality": quality,
        "failed_slugs": [
            r["doc_slug"] for r in results
            if not r.get("skipped") and not r["pipeline_success"]
        ],
        "results": results,
    }

    report_path = reports_dir / "first_100_batch_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.success(f"[Batch] Report saved: {report_path}")

    # ── Console summary ────────────────────────────────────────────────────
    logger.success(
        f"\n{'='*60}"
        f"\n[Batch] COMPLETE"
        f"\n  scraped:   {len(listing)}"
        f"\n  processed: {len(processed)}"
        f"\n  skipped:   {skipped}"
        f"\n  ✓ success: {success}"
        f"\n  ✗ failed:  {failed}"
        f"\n  quality:   topic_assignments={quality['has_topic_assignments_pct']}% "
        f"legal_basis={quality['has_legal_basis_pct']}%"
        f"\n  report:    {report_path}"
        f"\n{'='*60}"
    )

    return report["summary"]


# ── Quality helpers ───────────────────────────────────────────────────────────

def _pct(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


def _has_field(settings: Settings, doc_slug: str, field: str) -> bool:
    """
    Check if a field has a non-null, non-empty value in the structured JSON.
    Used for data-quality metrics in the batch report.
    """
    path = settings.STRUCTURED_DOCS_DIR / f"{doc_slug}.json"
    if not path.exists():
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        val = data.get("document", {}).get(field)
        return bool(val)
    except Exception:
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_first_100.py",
        description=(
            "Jordanian RegTech — batch pipeline for the first N LOB legislation items"
        ),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of legislation items to process (default: 100)",
    )
    p.add_argument(
        "--law-type",
        dest="law_type",
        default=None,
        metavar="TYPE_AR",
        help=(
            "Filter by Arabic document type, e.g.: قانون, نظام, تعليمات"
        ),
    )
    p.add_argument(
        "--active-only",
        dest="active_only",
        action="store_true",
        help="Only process legislation with status نافذ (active)",
    )
    p.add_argument(
        "--force-refetch",
        dest="force_refetch",
        action="store_true",
        help="Re-fetch and re-process already fetched documents",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip legislation whose structured JSON already exists "
            "(data/structured/docs/{slug}.json)"
        ),
    )
    p.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retry attempts per item on failure (default: 3)",
    )
    p.add_argument(
        "--log-level",
        dest="log_level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()

    # Configure loguru
    logger.remove()
    logger.add(sys.stderr, level=args.log_level, colorize=True, enqueue=True)

    # Also log to a file
    settings = Settings()
    settings.ensure_directories()
    logger.add(
        settings.LOGS_DIR / "run_first_100.log",
        rotation="50 MB",
        level="DEBUG",
        encoding="utf-8",
    )

    summary = run_batch(
        limit=args.limit,
        law_type_ar=args.law_type,
        active_only=args.active_only,
        force_refetch=args.force_refetch,
        resume=args.resume,
        retries=args.retries,
    )

    print("\n── Batch Summary ─────────────────────────────────────")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    sys.exit(0 if summary.get("success", 0) > 0 or summary.get("items_scraped", 0) == 0 else 1)
