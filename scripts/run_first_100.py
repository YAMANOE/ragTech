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
    "نافذ":      "active",
    "نافذة":     "active",
    "ساري":      "active",
    "سارية":     "active",
    "ملغى":      "repealed",
    "ملغي":      "repealed",
    "ملغية":     "repealed",
    "غير ساري":  "repealed",
    "غير سارية": "repealed",
    "منتهي":     "repealed",
    "منتهية":    "repealed",
    "معدّل":     "amended",
    "معدل":      "amended",
    "معدّلة":    "amended",
    "معدلة":     "amended",
    "موقوف":     "suspended",
    "موقوفة":    "suspended",
    "مؤقت":      "draft",
    "مؤقتة":     "draft",
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

# URL candidates for the legislation listing / search page.
# Tried in order until one yields results.
_LISTING_URL_CANDIDATES = [
    # The AngularJS hash-route search page
    "https://www.lob.gov.jo/?v=2&lang=ar#!/SearchLegislation",
    # Home page — sometimes the default view shows a full paginated table
    "https://www.lob.gov.jo/?v=2&lang=ar",
]


class LOBListingScraper:
    """
    LOB listing scraper — correctly extracts from the AngularJS SPA.

    Root issue: the search results table uses
        ng-click="LegislationLaw.LinkToDetails(item)"
    with NO href attributes.  There are no <a href="...LegislationID..."> links
    in the DOM at all.  Every previous attempt that looked for href links or
    tried to read window.angular (which is undefined in Playwright's eval
    context) returned zero items.

    Fix: monkey-patch LegislationLaw.LinkToDetails on the controller $scope
    via jQuery's .scope() extension, click every result row programmatically,
    and capture the raw AngularJS item objects that are passed to the function.
    Those objects contain LegislationID, LegislationName, Year, etc.
    We use a for...in loop (not Object.keys) so we traverse prototype-chain
    properties, then filter out Angular internals ($$-prefixed keys) and
    keep only primitive values — the result is a plain serialisable dict.
    """

    # Go directly to the legislation search/listing page.
    # The #!/SearchLegislation hash-route renders an EMPTY #Sections by default
    # (we confirmed this from saved HTML); #!Jordanian-Legislation shows the
    # search form immediately.
    _URL = "https://www.lob.gov.jo/?v=0&lang=ar#!Jordanian-Legislation"

    def __init__(self, settings: Settings, headless: bool = False):
        self.settings = settings
        self.headless = headless   # False = visible browser window (default for debugging)

    # ── Public entry points ───────────────────────────────────────────────────

    def scrape_sync(
        self,
        limit: int = 100,
        law_type_ar: Optional[str] = None,
        active_only: bool = False,
    ) -> list[dict]:
        return asyncio.run(self.scrape_listing(limit, law_type_ar, active_only))

    async def scrape_listing(
        self,
        limit: int = 100,
        law_type_ar: Optional[str] = None,
        active_only: bool = False,
    ) -> list[dict]:
        from playwright.async_api import async_playwright

        results:  list[dict] = []
        seen_ids: set[str]   = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                locale="ar-JO",
                extra_http_headers={"Accept-Language": "ar,en;q=0.8"},
            )
            page = await context.new_page()

            # ── 1. Navigate ───────────────────────────────────────────────
            logger.info(f"[Scraper] Navigating to: {self._URL}")
            try:
                await page.goto(
                    self._URL,
                    wait_until="domcontentloaded",
                    timeout=self.settings.PLAYWRIGHT_TIMEOUT,
                )
            except Exception as exc:
                logger.error(f"[Scraper] Navigation failed: {exc}")
                await browser.close()
                return []

            # ── 2. Wait for the search form button to be visible ──────────
            logger.info("[Scraper] Waiting for search form…")
            try:
                await page.wait_for_selector(
                    "button[type='submit']",
                    state="visible",
                    timeout=30_000,
                )
                logger.info("[Scraper] Search form ready")
            except Exception as exc:
                logger.warning(f"[Scraper] Search form timeout: {exc}")

            # ── 3. Diagnostic: before search ──────────────────────────────
            await self._save_diagnostic(page, "01_before_search")

            # ── 4. Apply optional filters ─────────────────────────────────
            await self._apply_filters(page, law_type_ar, active_only)

            # ── 5. Submit the search form ─────────────────────────────────
            await self._submit_search(page)

            # ── 6. Wait for result rows to appear ─────────────────────────
            logger.info("[Scraper] Waiting for result rows…")
            try:
                await page.wait_for_selector(
                    "tr[ng-click*='LegislationLaw'], tr[ng-repeat*='SearchResult']",
                    state="visible",
                    timeout=20_000,
                )
                await asyncio.sleep(1)  # let Angular finish rendering
                logger.info("[Scraper] Result rows visible")
            except Exception as exc:
                logger.warning(f"[Scraper] Result rows timeout: {exc}")

            # ── 7. Diagnostic: after search ───────────────────────────────
            await self._save_diagnostic(page, "02_after_search")

            # ── 8. Extract + paginate ─────────────────────────────────────
            page_num = 0
            while len(results) < limit:
                page_num += 1
                logger.info(
                    f"[Scraper] Page {page_num} — "
                    f"{len(results)}/{limit} collected so far"
                )

                new_items = await self._extract_current_page(
                    page, seen_ids, active_only
                )

                if not new_items:
                    logger.warning(
                        f"[Scraper] Page {page_num}: 0 items extracted. "
                        "Stopping pagination."
                    )
                    await self._log_page_debug(page)
                    break

                # Preview first item on first page
                if page_num == 1:
                    p0 = new_items[0]
                    logger.info(
                        f"[Scraper] First item preview:\n"
                        f"  id={p0['legislation_id']!r}\n"
                        f"  title={p0['title_ar'][:70]!r}\n"
                        f"  year={p0['issue_year']}  type={p0['doc_type_en']}"
                        f"  status={p0['status_normalized']}\n"
                        f"  url={p0['detail_url']!r}"
                    )

                for item in new_items:
                    if len(results) >= limit:
                        break
                    results.append(item)
                    seen_ids.add(item["legislation_id"])

                if len(results) >= limit:
                    break

                has_next = await self._goto_next_page(page)
                if not has_next:
                    logger.info("[Scraper] No more pages.")
                    break

                # Wait for next page results to render
                try:
                    await page.wait_for_selector(
                        "tr[ng-click*='LegislationLaw']",
                        state="visible",
                        timeout=12_000,
                    )
                    await asyncio.sleep(1)
                except Exception:
                    pass

            await browser.close()

        logger.info(f"[Scraper] Collected {len(results)} total items")
        return results[:limit]

    # ── Core extraction: monkey-patch + click ─────────────────────────────────

    async def _extract_current_page(
        self,
        page,
        seen_ids: set,
        active_only: bool,
    ) -> list[dict]:
        """
        Extract all visible result rows by temporarily monkey-patching
        LegislationLaw.LinkToDetails on the AngularJS controller $scope.

        When we click each <tr ng-click="LegislationLaw.LinkToDetails(item)">,
        Angular evaluates the expression and calls our patched function with the
        real scope item object.  We capture those objects, restore the original
        function, then serialise the captured items with for...in (which
        traverses the prototype chain, unlike Object.keys which only returns
        own enumerable properties and silently misses AngularJS scope fields).
        """
        js = """
        () => {
            try {
                // ── find result rows ────────────────────────────────────
                var rows = document.querySelectorAll('tr[ng-click*="LegislationLaw"]');
                if (!rows.length) {
                    rows = document.querySelectorAll('tr[ng-repeat*="SearchResult"]');
                }
                if (!rows.length) {
                    return {ok: false, reason: 'no result rows in DOM', rowsFound: 0};
                }

                // ── walk up to the controller scope ────────────────────
                var getScope = function(el) {
                    if (window.jQuery) {
                        try {
                            var fn = window.jQuery(el).scope;
                            if (fn) return fn.call(window.jQuery(el));
                        } catch(e) {}
                    }
                    return null;
                };

                var ctrlScope = getScope(rows[0]);
                if (!ctrlScope) {
                    return {ok: false, reason: 'jQuery .scope() returned null — is jQuery loaded?'};
                }
                // Walk ancestors until we find LegislationLaw
                var s = ctrlScope;
                for (var d = 0; d < 15; d++) {
                    if (s && s.LegislationLaw) break;
                    if (s && s.$parent) { s = s.$parent; } else { s = null; break; }
                }
                if (!s || !s.LegislationLaw) {
                    return {ok: false, reason: 'LegislationLaw not found on any ancestor scope'};
                }

                var llaw = s.LegislationLaw;
                if (typeof llaw.LinkToDetails !== 'function') {
                    return {ok: false, reason: 'LinkToDetails is not a function'};
                }

                // ── patch: capture items instead of navigating ─────────
                var captured = [];
                var origFn = llaw.LinkToDetails;
                llaw.LinkToDetails = function(item) { captured.push(item); };

                for (var i = 0; i < rows.length; i++) {
                    rows[i].click();
                }

                llaw.LinkToDetails = origFn;   // restore immediately

                if (captured.length === 0) {
                    return {
                        ok: false,
                        reason: 'row clicks did not fire LinkToDetails — check ng-click binding',
                        rowsFound: rows.length
                    };
                }

                // ── serialise: for...in traverses prototype chain ──────
                var items = [];
                for (var ci = 0; ci < captured.length; ci++) {
                    var raw = captured[ci];
                    var clean = {};
                    for (var k in raw) {
                        if (k.slice(0, 2) === '$$') continue;   // skip Angular internals
                        var v = raw[k];
                        if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
                            clean[k] = v;
                        }
                    }
                    items.push(clean);
                }

                // Return first item's keys to help debugging
                var firstKeys = items.length > 0 ? Object.keys(items[0]) : [];

                return {
                    ok: true,
                    rowsFound: rows.length,
                    captured: captured.length,
                    firstKeys: firstKeys,
                    items: items
                };

            } catch(e) {
                return {ok: false, reason: 'exception: ' + e.toString()};
            }
        }
        """
        try:
            result = await page.evaluate(js)
        except Exception as exc:
            logger.warning(f"[Scraper] extract eval error: {exc}")
            return []

        if not result.get("ok"):
            logger.warning(
                f"[Scraper] extract failed: {result.get('reason')}  "
                f"rows={result.get('rowsFound', 0)}"
            )
            return []

        logger.info(
            f"[Scraper] rows={result['rowsFound']}  "
            f"captured={result['captured']}  "
            f"firstKeys={result.get('firstKeys', [])}"
        )

        items: list[dict] = []
        for raw_entry in result.get("items", []):
            item = self._build_item(raw_entry)
            if not item:
                continue
            if item["legislation_id"] in seen_ids:
                continue
            if active_only and item.get("status_normalized") != "active":
                continue
            items.append(item)

        return items

    def _build_item(self, entry: dict) -> Optional[dict]:
        """Convert a raw Angular scope field-dict into our standard item format.

        Confirmed field names from the LOB API (captured via monkey-patch):
          pmk_ID, Name, Number, Year, Status_AR, Type (int TypeID), TypeArName
        """
        # ── Find LegislationID ────────────────────────────────────────────
        leg_id: Optional[str] = None
        for field in [
            "pmk_ID",                                        # actual LOB field name
            "LegislationID", "LegislationId", "legislationId",
            "legislation_id", "Id", "ID", "id",
        ]:
            val = entry.get(field)
            if val is not None and str(val).strip() not in ("", "0", "None"):
                leg_id = str(int(float(str(val))))
                break

        if not leg_id:
            # Broadest fallback: any key with "id" in name that has a positive integer
            for k, v in entry.items():
                if "id" in k.lower() and isinstance(v, (int, float)) and v > 0:
                    leg_id = str(int(v))
                    logger.debug(f"[Scraper] Used fallback ID field: {k}={v}")
                    break

        if not leg_id:
            logger.debug(
                f"[Scraper] _build_item: no LegislationID found. "
                f"Keys present: {list(entry.keys())[:20]}"
            )
            return None

        # ── Type ──────────────────────────────────────────────────────────
        # "Type" in the LOB API is the integer TypeID; "TypeArName" is the Arabic label.
        raw_type_int = entry.get("Type")
        type_id = int(
            entry.get("LegislationTypeID") or entry.get("LegislationTypeId") or
            entry.get("TypeID") or entry.get("TypeId") or entry.get("typeId") or
            (raw_type_int if isinstance(raw_type_int, (int, float)) else None) or
            _DEFAULT_TYPE_ID
        )
        # Arabic type label — prefer TypeArName, fall back to string fields
        type_ar = ""
        for tf in ("TypeArName", "LegislationType", "TypeName"):
            val = entry.get(tf)
            if val and isinstance(val, str):
                type_ar = val.strip()
                break
        if not type_ar and not isinstance(raw_type_int, (int, float)):
            # "Type" is a string in this entry
            type_ar = str(raw_type_int or "").strip()
        if not type_ar:
            type_ar = _TYPE_ID_TO_AR.get(type_id, "")
        type_en = _AR_TO_EN_TYPE.get(type_ar, "law")

        # ── Title ─────────────────────────────────────────────────────────
        # "Name" is the confirmed LOB field name; try LegislationName as fallback
        title = ""
        for tf in ("Name", "LegislationName", "LegislationTitle", "title"):
            val = entry.get(tf)
            if val and isinstance(val, str):
                title = val.strip()
                break

        # ── Year ──────────────────────────────────────────────────────────
        year_raw = (
            entry.get("Year") or entry.get("year") or
            entry.get("IssueYear") or entry.get("issueYear")
        )
        issue_year: Optional[int] = None
        if year_raw:
            try:
                issue_year = int(year_raw)
            except (ValueError, TypeError):
                pass

        # ── Doc number ───────────────────────────────────────────────────
        num_raw = (
            entry.get("Number") or entry.get("LegislationNumber") or
            entry.get("LegislationNo") or entry.get("number") or ""
        )
        doc_number: Optional[str] = str(num_raw).strip() or None
        if doc_number in ("0", "None", ""):
            doc_number = None

        # ── Status ───────────────────────────────────────────────────────
        # "Status_AR" is the confirmed LOB field name
        status_ar = ""
        for sf in ("Status_AR", "StatusName", "Status", "status"):
            val = entry.get(sf)
            if val and isinstance(val, str):
                status_ar = val.strip()
                break
        if status_ar.isdigit():
            status_ar = ""
        status_en = _AR_TO_EN_STATUS.get(status_ar, "active")

        detail_url = _LOB_DETAIL_TMPL.format(leg_id=leg_id, type_id=type_id)

        item: dict = {
            "legislation_id":      leg_id,
            "legislation_type_id": type_id,
            "title_ar":            title,
            "doc_type_ar":         type_ar,
            "doc_type_en":         type_en,
            "doc_number":          doc_number,
            "issue_year":          issue_year,
            "source_status_text":  status_ar,
            "status_normalized":   status_en,
            "detail_url":          detail_url,
            "doc_slug":            "",
        }
        item["doc_slug"] = _make_slug(item)
        return item

    # ── Pagination ────────────────────────────────────────────────────────────

    async def _goto_next_page(self, page) -> bool:
        """
        Call LegislationLaw.GetLegislationSearch(nextStart, null) on the scope
        to load the next results page.  Falls back to clicking the Next button.
        """
        js = """
        () => {
            try {
                var getScope = function(el) {
                    if (window.jQuery) {
                        try { var fn = window.jQuery(el).scope; if (fn) return fn.call(window.jQuery(el)); } catch(e) {}
                    }
                    return null;
                };
                var el = document.querySelector('tr[ng-click*="LegislationLaw"]') ||
                         document.getElementById('Sections');
                if (!el) return {ok: false, reason: 'no element'};

                var scope = getScope(el);
                while (scope && !scope.LegislationLaw) scope = scope.$parent;
                if (!scope || !scope.LegislationLaw) return {ok: false, reason: 'no controller scope'};

                var llaw = scope.LegislationLaw;
                var total   = llaw.TotalCount || 0;
                var current = llaw.PageIndex  || 1;
                var next    = current + 10;   // 10 results per page

                if (total > 0 && current + 10 > total) {
                    return {ok: false, reason: 'last page', total: total, current: current};
                }
                if (typeof llaw.GetLegislationSearch !== 'function') {
                    return {ok: false, reason: 'GetLegislationSearch not a function'};
                }
                llaw.GetLegislationSearch(next, null);
                scope.$apply();
                return {ok: true, next: next, total: total};
            } catch(e) {
                return {ok: false, reason: e.toString()};
            }
        }
        """
        try:
            res = await page.evaluate(js)
        except Exception as exc:
            logger.debug(f"[Scraper] next_page eval failed: {exc}")
            res = {"ok": False}

        if res.get("ok"):
            logger.info(
                f"[Scraper] Requesting page start={res.get('next')} "
                f"of {res.get('total')}"
            )
            return True

        reason = res.get("reason", "")
        logger.debug(f"[Scraper] next_page: {reason}")
        if "last page" in reason:
            return False

        # Fallback: click Next button
        for sel in [
            "a[aria-label='Next']", "a[aria-label='التالي']",
            "li.next:not(.disabled) a", "[ng-click*='nextPage']",
        ]:
            try:
                btn = await page.query_selector(sel)
                if not btn:
                    continue
                disabled = await btn.evaluate(
                    "el => el.closest('li') && "
                    "(el.closest('li').classList.contains('disabled') || "
                    "el.hasAttribute('disabled'))"
                )
                if not disabled:
                    await btn.click()
                    return True
            except Exception:
                continue
        return False

    # ── Form helpers ──────────────────────────────────────────────────────────

    async def _apply_filters(
        self, page, law_type_ar: Optional[str], active_only: bool
    ) -> None:
        if law_type_ar:
            for sel in [
                "select[ng-model*='Type']", "#LegislationTypeID",
                "select[ng-model*='LegislationType']",
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.select_option(label=law_type_ar)
                        logger.debug(f"[Scraper] Type filter: {law_type_ar}")
                        break
                except Exception:
                    continue

        if active_only:
            for sel in ["select[ng-model*='Status']", "#LegislationStatusID"]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        for label in ["نافذ", "ساري", "Active"]:
                            try:
                                await el.select_option(label=label)
                                logger.debug(f"[Scraper] Status filter: {label}")
                                break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue

    async def _submit_search(self, page) -> bool:
        for sel in [
            "button[type='submit']",
            "button.btn-primary",
            "[ng-click*='search']", "[ng-click*='Search']",
        ]:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    logger.info(f"[Scraper] Search submitted via: {sel}")
                    return True
            except Exception:
                continue

        # JS fallback
        submitted = await page.evaluate("""
        () => {
            var btns = Array.from(document.querySelectorAll('button,input[type=submit]'));
            for (var i = 0; i < btns.length; i++) {
                var t = (btns[i].textContent || btns[i].value || '').trim();
                if (t.includes('بحث') || t.includes('عرض') || t.toLowerCase().includes('search')) {
                    btns[i].click(); return 'clicked:' + t;
                }
            }
            return null;
        }
        """)
        if submitted:
            logger.info(f"[Scraper] Search submitted via JS: {submitted}")
            return True
        logger.debug("[Scraper] Search submit: no button found")
        return False

    # ── Diagnostic helpers ────────────────────────────────────────────────────

    async def _save_diagnostic(self, page, stage: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        logs_dir = self.settings.LOGS_DIR
        try:
            ss = logs_dir / f"scraper_{stage}_{ts}.png"
            await page.screenshot(path=str(ss), full_page=True)
            logger.info(f"[Scraper] Screenshot: {ss}")
        except Exception as exc:
            logger.debug(f"[Scraper] Screenshot failed: {exc}")
        try:
            html = await page.content()
            html_path = self.settings.DATA_DIR / "indexes" / f"lob_{stage}.html"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html, encoding="utf-8")
            logger.info(f"[Scraper] HTML saved: {html_path} ({len(html):,} chars)")
        except Exception as exc:
            logger.debug(f"[Scraper] HTML save failed: {exc}")

    async def _log_page_debug(self, page) -> None:
        try:
            info = await page.evaluate("""
            () => {
                var trs = document.querySelectorAll('tr[ng-click*="LegislationLaw"]');
                var allLinks = document.querySelectorAll('a');
                return {
                    resultRows: trs.length,
                    totalLinks: allLinks.length,
                    jqueryLoaded: typeof window.jQuery !== 'undefined',
                    angularLoaded: typeof window.angular !== 'undefined',
                    bodySnippet: document.body ? document.body.innerText.slice(0, 300) : ''
                };
            }
            """)
            logger.info(f"[Scraper] Page debug: {json.dumps(info, ensure_ascii=False)}")
        except Exception as exc:
            logger.debug(f"[Scraper] Page debug failed: {exc}")



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


def _enrich_from_text(item: dict, text: str) -> dict:
    """
    Try to fill in missing issue_year, source_status_text, and
    status_normalized from free text (e.g. a surrounding table row).
    Returns the same dict with any newly found values filled in.
    """
    if not item.get("issue_year"):
        m = re.search(r"\b(19[89]\d|20[012]\d)\b", text)
        if m:
            item["issue_year"] = int(m.group(1))

    if not item.get("source_status_text"):
        for ar, en in _AR_TO_EN_STATUS.items():
            if ar in text:
                item["source_status_text"] = ar
                item["status_normalized"]  = en
                break

    if not item.get("doc_type_ar"):
        for ar, en in _AR_TO_EN_TYPE.items():
            if ar in text:
                item["doc_type_ar"] = ar
                item["doc_type_en"] = en
                break

    if not item.get("doc_number"):
        m = _RE_NUM.search(text)
        if m:
            item["doc_number"] = m.group(1)

    # Rebuild slug now that we may have more data
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
