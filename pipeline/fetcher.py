"""
pipeline/fetcher.py
--------------------
Playwright-based fetcher for LOB (Bureau of Legislation and Opinion) pages.

Responsibilities:
  1. Render a LOB legislation page (JavaScript-rendered HTML)
  2. Save raw HTML to data/raw/html/
  3. Extract visible text and save to data/raw/text/
  4. Append a row to data/raw/source_registry.csv
  5. Return a FetchRecord for downstream pipeline stages

Important design notes:
  - Uses Playwright's async API; sync wrapper `fetch_sync()` is provided for scripts
  - Respects FETCH_DELAY_SECONDS between requests
  - LOB_SELECTORS in config/settings.py must be tuned after first real run
  - Saves screenshot on failure to help debug selector issues

Usage:
    from pipeline.fetcher import LOBFetcher
    from config.settings import Settings

    settings = Settings()
    fetcher  = LOBFetcher(settings)
    record   = fetcher.fetch_sync("https://lob.gov.jo/AR/...", "law-2014-34")
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import Settings
from models.schema import FetchRecord
from utils.id_generator import IDGenerator


class LOBFetcher:
    """
    Fetches legislation pages from the Jordanian Bureau of Legislation (LOB).
    Stores raw HTML, extracted text, and updates the source registry.
    """

    # Registry CSV columns (must match FetchRecord fields)
    _REGISTRY_FIELDS = [
        "fetch_id", "doc_slug", "source_url", "fetch_timestamp",
        "http_status", "page_title", "html_file_path", "text_file_path",
        "fetch_notes",
    ]

    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_directories()

        # Set up file-based logging for fetcher
        logger.add(
            settings.LOGS_DIR / "fetcher.log",
            rotation="10 MB",
            level="INFO",
            encoding="utf-8",
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch_sync(
        self,
        url: str,
        doc_slug: str,
        wait_selector: Optional[str] = None,
    ) -> FetchRecord:
        """
        Synchronous wrapper around the async fetcher.
        Use this from scripts and non-async contexts.
        """
        return asyncio.run(self.fetch_page(url, doc_slug, wait_selector=wait_selector))

    async def fetch_page(
        self,
        url: str,
        doc_slug: str,
        wait_selector: Optional[str] = None,
    ) -> FetchRecord:
        """
        Fetch a single LOB page with Playwright.
        Returns a FetchRecord (also written to source_registry.csv).
        """
        from playwright.async_api import async_playwright

        timestamp = datetime.now(timezone.utc)
        timestamp_str = timestamp.isoformat()
        file_ts = timestamp.strftime("%Y%m%d_%H%M%S")
        fetch_id = IDGenerator.fetch_id(url, timestamp_str)

        logger.info(f"[Fetcher] Starting fetch: {url} → {doc_slug}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.settings.PLAYWRIGHT_HEADLESS
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
                response = await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=self.settings.PLAYWRIGHT_TIMEOUT,
                )
                http_status = response.status if response else None

                # Wait for content selector (use config or argument override)
                effective_wait = wait_selector or self.settings.LOB_SELECTORS["wait_for"]
                try:
                    await page.wait_for_selector(
                        effective_wait,
                        timeout=self.settings.PLAYWRIGHT_TIMEOUT // 2,
                    )
                    logger.debug(f"[Fetcher] Content selector found: {effective_wait}")
                except Exception:
                    # Selector not found — still proceed, but log warning
                    logger.warning(
                        f"[Fetcher] Content selector not found for {url}. "
                        "Captured whatever is loaded. Update LOB_SELECTORS in settings."
                    )
                    # Save screenshot to help debug selector issues
                    screenshot_path = self.settings.LOGS_DIR / f"{doc_slug}_{file_ts}_debug.png"
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                    logger.info(f"[Fetcher] Screenshot saved: {screenshot_path}")

                page_title = await page.title()
                raw_html = await page.content()

                # Extract visible text (remove nav/header/footer/script junk)
                raw_text = await page.evaluate("""
                    () => {
                        const remove = document.querySelectorAll(
                            'script, style, nav, header, footer, .navigation, .menu, .breadcrumb'
                        );
                        remove.forEach(el => el.remove());
                        return document.body ? document.body.innerText : '';
                    }
                """)

                # Save HTML
                html_filename = f"{doc_slug}_{file_ts}.html"
                html_path = self.settings.RAW_HTML_DIR / html_filename
                html_path.write_text(raw_html, encoding="utf-8")

                # Save text
                txt_filename = f"{doc_slug}_{file_ts}.txt"
                txt_path = self.settings.RAW_TEXT_DIR / txt_filename
                txt_path.write_text(raw_text, encoding="utf-8")

                logger.success(
                    f"[Fetcher] Saved: {html_filename} ({len(raw_html)} bytes HTML, "
                    f"{len(raw_text)} chars text)"
                )

                record = FetchRecord(
                    fetch_id=fetch_id,
                    doc_slug=doc_slug,
                    source_url=url,
                    fetch_timestamp=timestamp_str,
                    http_status=http_status,
                    page_title=page_title,
                    html_file_path=str(html_path),
                    text_file_path=str(txt_path),
                    fetch_notes="",
                )

            except Exception as exc:
                logger.error(f"[Fetcher] Failed to fetch {url}: {exc}")

                # Try to save a debug screenshot if page partially loaded
                try:
                    screenshot_path = self.settings.LOGS_DIR / f"{doc_slug}_{file_ts}_error.png"
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                except Exception:
                    pass

                record = FetchRecord(
                    fetch_id=fetch_id,
                    doc_slug=doc_slug,
                    source_url=url,
                    fetch_timestamp=timestamp_str,
                    http_status=None,
                    page_title="",
                    html_file_path="",
                    text_file_path="",
                    fetch_notes=f"ERROR: {exc}",
                )

            finally:
                await browser.close()

        self._append_registry(record)

        # Polite delay
        await asyncio.sleep(self.settings.FETCH_DELAY_SECONDS)

        return record

    async def fetch_batch(
        self,
        items: list[dict],
    ) -> list[FetchRecord]:
        """
        Sequentially fetch a batch of pages (sequential to respect rate limits).

        Args:
            items: list of dicts, each with keys: 'url' and 'doc_slug'
                   Optional: 'wait_selector'

        Returns:
            List of FetchRecord objects (one per item, even if failed)
        """
        results = []
        for i, item in enumerate(items, 1):
            logger.info(f"[Fetcher] Batch {i}/{len(items)}: {item['doc_slug']}")
            try:
                record = await self.fetch_page(
                    url=item["url"],
                    doc_slug=item["doc_slug"],
                    wait_selector=item.get("wait_selector"),
                )
                results.append(record)
            except Exception as exc:
                logger.error(f"[Fetcher] Batch item failed: {item}: {exc}")
                results.append(
                    FetchRecord(
                        fetch_id=IDGenerator.fetch_id(item["url"], "error"),
                        doc_slug=item.get("doc_slug", "unknown"),
                        source_url=item.get("url", ""),
                        fetch_timestamp=datetime.now(timezone.utc).isoformat(),
                        fetch_notes=f"BATCH ERROR: {exc}",
                    )
                )
        return results

    def fetch_batch_sync(self, items: list[dict]) -> list[FetchRecord]:
        """Synchronous wrapper for fetch_batch."""
        return asyncio.run(self.fetch_batch(items))

    # ── Page content helpers ──────────────────────────────────────────────────

    async def _try_selectors(self, page, selectors: list[str]) -> Optional[str]:
        """
        Try a list of CSS selectors in order; return inner text of first match.
        Returns None if no selector matches.
        """
        for selector in selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    return await element.inner_text()
            except Exception:
                continue
        return None

    # ── Registry helpers ──────────────────────────────────────────────────────

    def _append_registry(self, record: FetchRecord) -> None:
        """Append a FetchRecord row to source_registry.csv."""
        registry_path = self.settings.SOURCE_REGISTRY_PATH
        write_header = not registry_path.exists()

        with open(registry_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=self._REGISTRY_FIELDS,
                delimiter=self.settings.CSV_DELIMITER,
            )
            if write_header:
                writer.writeheader()
            row = record.to_dict()
            writer.writerow({k: row.get(k, "") for k in self._REGISTRY_FIELDS})

        logger.debug(f"[Fetcher] Registry updated: {registry_path}")

    def load_registry(self) -> list[dict]:
        """Load all fetch records from source_registry.csv."""
        if not self.settings.SOURCE_REGISTRY_PATH.exists():
            return []
        with open(self.settings.SOURCE_REGISTRY_PATH, encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=self.settings.CSV_DELIMITER)
            return list(reader)

    def already_fetched(self, doc_slug: str) -> bool:
        """Return True if doc_slug has at least one successful fetch in registry."""
        for row in self.load_registry():
            if row.get("doc_slug") == doc_slug and row.get("html_file_path"):
                return True
        return False

    def latest_fetch(self, doc_slug: str) -> Optional[dict]:
        """Return the most recent successful registry row for a doc_slug."""
        rows = [
            r for r in self.load_registry()
            if r.get("doc_slug") == doc_slug and r.get("html_file_path")
        ]
        return sorted(rows, key=lambda r: r.get("fetch_timestamp", ""))[-1] if rows else None

    # ── LOB search page scraping ──────────────────────────────────────────────

    async def search_lob(
        self,
        query: str,
        max_results: int = 20,
    ) -> list[dict]:
        """
        Search for legislation on LOB and return a list of
        {'title': ..., 'url': ..., 'doc_slug_hint': ...} dicts.

        ASSUMPTION: LOB search URL pattern — verify with browser dev tools.
        The search interface may vary; update SEARCH_URL and selectors
        in config/settings.py after first inspection.

        This method is a STARTING POINT and will need selector adjustment.
        """
        from playwright.async_api import async_playwright

        # ASSUMPTION: LOB uses a query parameter for search
        SEARCH_URL = f"{self.settings.LOB_BASE_URL}/AR/SearchLegislation?q={query}"
        RESULT_SELECTOR = "ul.search-results li a, div.result-item a, table.results tr td a"

        logger.info(f"[Fetcher] Searching LOB: '{query}'")
        results = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(SEARCH_URL, wait_until="networkidle", timeout=30_000)
                await asyncio.sleep(2)

                links = await page.query_selector_all(RESULT_SELECTOR)
                for link in links[:max_results]:
                    title = (await link.inner_text()).strip()
                    href  = await link.get_attribute("href") or ""
                    if href and not href.startswith("http"):
                        href = self.settings.LOB_BASE_URL + href
                    if title and href:
                        results.append({"title": title, "url": href})
            except Exception as exc:
                logger.error(f"[Fetcher] Search failed: {exc}")
            finally:
                await browser.close()

        logger.info(f"[Fetcher] Search returned {len(results)} results")
        return results
