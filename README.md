# Jordanian RegTech — Legislative Intelligence Pipeline

A production-ready Python pipeline for scraping, cleaning, structuring, and exporting
Jordanian legislation from the Bureau of Legislation and Opinion (LOB) website.

**Current state:** 99 structured documents. 100% topic coverage. 11/11 QA checks passing. 91/91 tests passing.
Last batch completed: 2026-03-14. Post-processing improvements applied: 2026-03-15.

---

## Quick Start

### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env if you need to override defaults (base URL, timeouts)
```

### 3. Run

```bash
# Scrape and process 5 laws (test run)
python scripts/run_first_100.py --limit 5

# Full batch: 100 laws (takes ~23 minutes)
python scripts/run_first_100.py --limit 100

# Resume an interrupted batch
python scripts/run_first_100.py --resume --limit 100

# Process a single known law
python scripts/run_pipeline.py \
  --url "https://www.lob.gov.jo/?v=2&lang=ar#!/LegislationDetails?LegislationID=36&LegislationType=1&isMod=false" \
  --slug "legislation-36"
```

---

## What the Pipeline Does

It runs 6 stages on each document:

1. **Fetch** — Playwright scrapes the AngularJS LOB page, saves raw HTML and text to `data/raw/`
2. **Parse** — BeautifulSoup extracts the title, body text, metadata, and structural sections
3. **Clean** — 10-rule Arabic text cleaning: removes HTML artifacts, diacritics, UI labels, normalizes spacing
4. **Structure** — Assembles a complete typed document object: Document + DocumentVersion + Sections + Entities + Topics + Relationships
5. **Validate** — Runs QA checks: required fields, valid status, Arabic text present, section ordering, no LOB UI artifacts leaked
6. **Export** — Writes all output to relational CSVs (pipe-delimited) and graph CSVs (for Neo4j)

---

## Current Results (First 100 Laws Batch)

| Metric | Value |
|--------|-------|
| Scraped from LOB | 100 |
| Pipeline success | 98 / 100 (98%) |
| Structured documents | 99 |
| Validation passed | 100% |
| QA checks | 11 / 11 passing |
| Test suite | 91 / 91 passing |
| Has publication date | 99% |
| Has topic assignment | 100% (99/99) |
| Has legal basis extracted | 38.4% (38/99) |
| Has inter-doc relationships | 16.2% (16/99) |
| Total sections | 8,512 |
| Unique entity names | 270 (549 raw rows) |
| Topic rows | 147 |
| Doc types | law×96, regulation×2, constitution×1 |
| Status mismatches | 0 (fixed) |
| Failed documents | law-2006-49, law-1972-21 |

---

## Output Files

### Structured Documents

```
data/structured/docs/law-2018-34.json    ← Full document object per law
data/structured/summaries/               ← Lightweight summary per law
data/structured/documents_index.json     ← Browsable index of all laws
```

### Relational CSV (exports/relational/)

All files are pipe-delimited (`|`), UTF-8 with BOM. Import directly into PostgreSQL, SQLite, or Supabase.

| File | Description | Rows (approx) |
|------|-------------|---------------|
| `documents.csv` | One row per law | 99 |
| `versions.csv` | One row per version. Currently v1 only | ~99 |
| `sections.csv` | One row per structural unit (article, chapter, etc.) | 8,512 actual rows* |
| `section_compliance_flags.csv` | Compliance flag columns per section | 8,512 |
| `entities.csv` | Deduplicated named institutions | 549 rows, 270 unique names |
| `topics.csv` | Topic taxonomy nodes | 21 topics |
| `document_topics.csv` | Doc-topic junction with confidence scores | 147 rows (99/99 docs covered) |
| `document_relationships.csv` | Inter-doc edges (AMENDS, REPEALS, etc.) | 16 edges |

*Note: `sections.csv` contains 8,512 actual data rows (use pandas or `csv.reader` to count — `wc -l` overcounts due to embedded newlines in Arabic text fields).

### Graph CSV (exports/graph/)

For direct Neo4j import using `neo4j-admin database import`. See ARCHITECTURE.md Section 20 for the import command.

- **5 node files:** `nodes_documents.csv`, `nodes_versions.csv`, `nodes_sections.csv`, `nodes_entities.csv`, `nodes_topics.csv`
- **11+ edge files:** `edges_has_version.csv`, `edges_has_section.csv`, `edges_issued_by.csv`, `edges_has_topic.csv`, `edges_amends.csv`, `edges_repeals.csv`, `edges_based_on.csv`, `edges_refers_to.csv`, `edges_implements.csv`, `edges_supplements.csv`, `edges_supersedes.csv`
- **6 future edge headers** in `exports/graph/future/` (empty, for Layer 2 compliance extraction)

### Raw and Clean Data

```
data/raw/html/     ← Saved HTML per fetch
data/raw/text/     ← Saved plain text per fetch
data/raw/source_registry.csv   ← Every fetch logged here
data/clean/        ← {doc_slug}_clean.json per document
data/clean/cleaning_log.csv    ← Every cleaning run logged here
data/indexes/      ← search_results_first_100.json (LOB listing metadata)
data/reports/      ← first_100_batch_report.json (quality metrics)
```

---

## How It Scrapes the LOB Website

The LOB website (`lob.gov.jo`) is an AngularJS 1.x single-page application. Search results are rendered after JavaScript executes and have no regular `<a href>` links. Standard scraping tools (requests, urllib) cannot see the content.

The pipeline uses two strategies:

**Listing scrape (discovering what to process):**  
`LOBListingScraper` monkey-patches the AngularJS `$scope.LegislationLaw.LinkToDetails()` function before clicking every result row. This captures the raw Angular scope item objects (which contain all metadata fields) without actually navigating away.

**Detail page scrape (fetching each law):**  
`LOBFetcher` uses Playwright with a headless Chromium browser, waits for `div.clicked-legislation-header` to appear, then extracts text via JavaScript (removing nav/header/footer) before saving.

---

## Data Formats

All CSV outputs use:
- **Delimiter:** `|` (pipe) — never conflicts with Arabic text
- **Encoding:** `utf-8-sig` (UTF-8 with BOM) — required for Excel to display Arabic correctly
- **NULL:** empty string

Pipe-delimited means you must specify the separator explicitly when loading:
```python
import pandas as pd
df = pd.read_csv("exports/relational/documents.csv", sep="|", encoding="utf-8-sig")
```

---

## Script Reference

| Script | What it does |
|--------|-------------|
| `scripts/run_first_100.py` | **Main batch runner.** Discovers 100 laws from LOB listing and runs the full pipeline on each |
| `scripts/run_pipeline.py` | **Single document runner.** Takes `--url` + `--slug` arguments || `scripts/retopic.py` | **Re-topic.** Re-runs topic classification on all existing structured docs without re-fetching |
| `scripts/dedup_entities.py` | **Entity dedup.** Post-processes entity names using RapidFuzz fuzzy matching to merge near-duplicates || `scripts/check_output.py` | **Diagnostic.** Prints fields of a structured document for inspection |
| `scripts/_inspect_html.py` | **Debug.** Examines saved HTML to find Angular directives |
| `scripts/run_mvp.py` | **Broken.** MVP runner with 5 hardcoded laws — URLs are not filled in yet |

---

## Configuration

All paths and parameters are in `config/settings.py`. Override with `.env`:

```bash
FETCH_DELAY_SECONDS=2        # Seconds between page fetches (be polite to LOB)
PLAYWRIGHT_TIMEOUT=30000     # Wait timeout in ms (increase if pages load slowly)
LOB_BASE_URL=https://lob.gov.jo
```

Topic taxonomy is in `config/topics.yaml`. Add keywords to improve topic coverage without code changes.

---

## What Needs Improvement

### Recently Fixed (2026-03-15)
- ~~**Status mapping bug** (غير ساري → active):~~ Fixed. Status map expanded to 20 values. 0 mismatches.
- ~~**Topic coverage (55.1%):**~~ Fixed. Topics expanded + 2 new topics added. Now 100% (99/99 docs).

### High Priority

1. **Legal basis extraction (38.4%):** The regex still misses older legal formulations. Older laws from the 1950s–1970s use different phrasing not covered by the current trigger-word list.

2. **Relationship detection (16.2%):** Only 16 of 99 laws have detected inter-document relationships. Most cross-references are not being captured. The pattern `"القانون رقم N لسنة YYYY"` misses informal citations.

3. **doc_number extraction for constitutions:** Constitutions have no "رقم" number in their title, so they get fallback slugs like `legislation-36` instead of `constitution-1952`. A year-only extraction pattern for دستور titles would fix this.

### Medium Priority

4. **Date format ambiguity:** When day and month are both ≤ 12, the code assumes DD/MM/YYYY. LOB appears to use MM/DD/YYYY in some places, causing off-by-one-month errors.

5. **Log file explosion:** Every pipeline component creates a new timestamped log file per instantiation. A 100-document batch creates 100+ log files. Fix: move `logger.add()` calls to the batch runner.

6. **topics.csv duplicates:** Running `export_all()` appends duplicate topic rows. Use `sort -u` on topic_id or add a dedup set in the exporter.

7. **run_mvp.py is not functional:** URLs in `MVP_TARGETS` are placeholder strings that need to be replaced with real LOB URLs.

### Not Started (Layer 2)

- Compliance section classification (obligation, prohibition, deadline, exception)
- Amendment version tracking (fetching and storing version 2, 3, ...)
- Arabic-to-English translation of titles
- Hijri date conversion for publication dates in older laws
- Named entity linking (associating extracted entity strings to a canonical entity registry)

---

## Architecture

See `ARCHITECTURE.md` for the full technical specification including:
- Complete class and method reference for all 10 classes
- Exact database schema for all 12 record types
- All CSV column orders for 8 relational tables and 17 graph files
- LOB scraping strategy (AngularJS monkey-patch explained)
- 10 Arabic cleaning rules with stage breakdown
- Topic classification scoring algorithm
- All 10 known bugs with locations and fixes

---

## Requirements

```
playwright>=1.41.0
beautifulsoup4>=4.12.0
lxml>=5.1.0
pyarabic>=0.6.15
loguru>=0.7.2
PyYAML>=6.0.1
python-slugify>=8.0.0
pandas>=2.1.0
regex>=2023.12.25
python-dotenv>=1.0.0
tqdm>=4.66.0
python-dateutil>=2.8.2
rapidfuzz>=3.6.0
```

Python 3.10+ required.
