# Jordanian RegTech – Legislative Intelligence Pipeline
## Architecture & Design Document

**Version:** 3.0
**Last updated:** March 14, 2026
**Scope:** Layer 1 — Legislative Intelligence (compliance-ready design)
**Primary Source:** Jordanian Bureau of Legislation and Opinion (LOB) — lob.gov.jo
**Language:** Arabic (Jordan)

**Current status:** Working. First 100 laws scraped at 98% pipeline success rate.
**Batch run completed:** 2026-03-14T17:56–18:19 UTC (23 minutes for 100 documents)
**Quality metrics:** topic_assignments=55.1%, legal_basis=32.7%, entities=73.5%, avg_sections=59.4, validation_passed=100%

---

## Table of Contents

1. Architecture Overview
2. Recommended Tools
3. Project Structure
4. File Formats Per Layer
5. Complete Data Model & Database Schema
6. Complete Class Reference
7. Complete Data Flow
8. LOB Website & Scraping Strategy
9. LOB API Field Names
10. Arabic Text Cleaning Rules
11. Parsing Strategy
12. ID Generation Conventions
13. Topic Classification Strategy
14. Relationship Detection Strategy
15. Configuration Reference
16. Running the Pipeline
17. Current Quality Metrics
18. Known Bugs and Limitations
19. Layer 2 Compliance Design (Future)
20. Graph Database Design

---

## 1. Architecture Overview

### Pipeline Layers

```
┌─────────────────────────────────────────────────────────────┐
│                        SOURCE                               │
│   LOB Website (lob.gov.jo) — AngularJS SPA, Arabic text     │
│   URL: https://www.lob.gov.jo/?v=0&lang=ar#!Jordanian-...   │
└────────────────────────┬────────────────────────────────────┘
                         │ Playwright + AngularJS monkey-patch
┌────────────────────────▼────────────────────────────────────┐
│                  LAYER 0: LISTING SCRAPE                    │
│  LOBListingScraper captures item objects from AngularJS     │
│  $scope via monkey-patched LinkToDetails()                  │
└────────────────────────┬────────────────────────────────────┘
                         │ detail URL per legislation item
┌────────────────────────▼────────────────────────────────────┐
│                  LAYER 1: RAW DATA                          │
│  data/raw/html/  — raw HTML per document                    │
│  data/raw/text/  — raw extracted text per document          │
│  data/raw/source_registry.csv — master fetch log            │
└────────────────────────┬────────────────────────────────────┘
                         │ BeautifulSoup parse + ArabicTextUtils
┌────────────────────────▼────────────────────────────────────┐
│                  LAYER 2: CLEAN DATA                        │
│  data/clean/{doc_slug}_clean.json — original + normalized   │
│  data/clean/cleaning_log.csv — master cleaning audit log    │
└────────────────────────┬────────────────────────────────────┘
                         │ Structurer: metadata+section+entity+topic
┌────────────────────────▼────────────────────────────────────┐
│               LAYER 3: STRUCTURED LEGAL DATA                │
│  data/structured/docs/{slug}.json — full per-doc object     │
│  data/structured/summaries/{slug}_summary.json              │
│  data/structured/documents_index.json — browsable index     │
└────────────────────────┬────────────────────────────────────┘
                         │ Exporter
┌────────────────────────▼────────────────────────────────────┐
│                  LAYER 4: EXPORT                            │
│  exports/relational/  — CSV files for RDBMS import          │
│  exports/graph/       — CSV files for Neo4j import          │
└─────────────────────────────────────────────────────────────┘
```

### Six Pipeline Stages (in execution order)

| Stage | Class | Input | Output |
|-------|-------|-------|--------|
| 1. Fetch | `LOBFetcher` | LOB URL | raw HTML, raw text, source_registry.csv row |
| 2. Parse | `LOBParser` | raw HTML / text file | ParsedDocument dict with metadata + sections |
| 3. Clean | `ArabicTextCleaner` | raw text string | CleanOutput with original_text + normalized_text |
| 4. Structure | `LegislationStructurer` | ParsedDocument + CleanOutput | PipelineResult with all record types |
| 5. Validate | `PipelineValidator` | PipelineResult | list of ValidationResult objects |
| 6. Export | `LegislationExporter` | PipelineResult | relational CSVs + graph CSVs |

---

## 2. Recommended Tools

| Tool | Purpose | Why chosen |
|------|---------|-----------|
| **Playwright** | Dynamic page fetching | LOB is a JavaScript-rendered AngularJS SPA; requests/urllib cannot see content |
| **BeautifulSoup4 + lxml** | HTML parsing | Fast, robust, handles malformed HTML common in Arabic government portals |
| **regex** | Arabic pattern matching | Extended Unicode support for Arabic numerals, diacritics, and RTL patterns |
| **pyarabic** | Arabic normalization | Alef normalization, harakat removal, tatweel removal — battle-tested library |
| **python-slugify** | Stable slug generation | URL-safe, consistent ID base from Arabic text |
| **PyYAML** | Topic taxonomy config | Human-editable topic definitions without code changes |
| **loguru** | Structured logging | Async-friendly, rotation, colored output, per-component log files |
| **python-dotenv** | Environment config | Keep credentials and paths out of source code |
| **uuid** | Stable identifiers | Deterministic UUID5 for stable IDs across re-runs |
| **python-dateutil** | Flexible date parsing | Handles ambiguous date formats in LOB pages |

**Intentionally excluded:**
- No ML/NLP models in Layer 1 (camel-tools is heavy — add for Layer 2 NER)
- No database driver (export to CSV/JSON; DB import is a separate deployment step)
- No async orchestration framework (asyncio is sufficient for sequential scraping)

---

## 3. Project Structure

```
new-project/
├── ARCHITECTURE.md        ← This document
├── README.md              ← Quick-start and current status
├── requirements.txt       ← Python dependencies
├── .gitignore
├── .env.example           ← Environment variable template
│
├── config/
│   ├── __init__.py
│   ├── settings.py        ← All paths, LOB selectors, pipeline parameters, type maps
│   └── topics.yaml        ← Hierarchical topic taxonomy (22 topics, 2 levels)
│
├── models/
│   ├── __init__.py
│   └── schema.py          ← All dataclasses: Document, DocumentVersion, Section,
│                            Entity, EntityRole, Topic, TopicAssignment,
│                            DocumentRelationship, SectionRelationship,
│                            FetchRecord, CleanOutput, ValidationResult, PipelineResult
│
├── utils/
│   ├── __init__.py
│   ├── arabic_utils.py    ← Stateless Arabic text utilities (ArabicTextUtils class)
│   └── id_generator.py    ← Deterministic UUID5 ID generation (IDGenerator class)
│
├── pipeline/
│   ├── __init__.py
│   ├── fetcher.py         ← Playwright-based LOB page fetcher (LOBFetcher)
│   ├── parser.py          ← Raw HTML → ParsedDocument dict (LOBParser)
│   ├── cleaner.py         ← Arabic text cleaning pipeline (ArabicTextCleaner)
│   ├── structurer.py      ← Full document object assembly (LegislationStructurer)
│   ├── exporter.py        ← CSV export — relational + graph (LegislationExporter)
│   └── validator.py       ← QA validation rules (PipelineValidator)
│
├── scripts/
│   ├── __init__.py
│   ├── run_pipeline.py    ← Single-document full pipeline runner (CLI)
│   ├── run_first_100.py   ← Batch runner: LOBListingScraper + pipeline per item
│   ├── run_mvp.py         ← MVP: 5 target laws (URLs are placeholders — not working)
│   ├── check_output.py    ← Diagnostic: prints structured output fields
│   └── _inspect_html.py   ← HTML inspection: finds ng-click/ng-repeat in saved HTML
│
├── data/
│   ├── raw/
│   │   ├── html/          ← {doc_slug}_{YYYYMMDD_HHMMSS}.html
│   │   ├── text/          ← {doc_slug}_{YYYYMMDD_HHMMSS}.txt
│   │   └── source_registry.csv        ← Master fetch log (append-only)
│   ├── clean/
│   │   ├── {doc_slug}_clean.json      ← Per-document clean output
│   │   └── cleaning_log.csv           ← Master cleaning audit log (append-only)
│   ├── structured/
│   │   ├── docs/                      ← {doc_slug}.json — full structured document
│   │   ├── summaries/                 ← {doc_slug}_summary.json — lightweight summary
│   │   └── documents_index.json       ← Cumulative browsable index
│   ├── indexes/                       ← search_results_first_100.json
│   └── reports/                       ← first_100_batch_report.json
│
├── exports/
│   ├── relational/         ← 8 CSV files, direct RDBMS import
│   └── graph/              ← 17 node+edge CSV files, Neo4j import format
│       └── future/         ← 6 header-only edge files for Layer 2
│
└── logs/
    └── {component}.log     ← Per-run per-component rotating log files
```

---

## 4. File Formats Per Layer

| Layer | Format | Encoding | Reason |
|-------|--------|----------|--------|
| Raw HTML | `.html` | UTF-8 | Preserve full original page for reprocessing |
| Raw text | `.txt` | UTF-8 | Quick access without re-parsing HTML |
| Source registry | `.csv` pipe-delimited | UTF-8 | Simple append-log, easy to import |
| Clean data | `.json` | UTF-8 | Preserve original + normalized + cleaning log inline |
| Cleaning log | `.csv` pipe-delimited | UTF-8 | Master audit trail |
| Structured per-doc | `.json` | UTF-8 | Full document object, self-contained for re-export |
| Summary | `.json` | UTF-8 | Lightweight browse index, no full text |
| Documents index | `.json` | UTF-8 | Aggregated list for batch processing |
| Relational export | `.csv` pipe-delimited | UTF-8-sig (BOM) | Direct import into PostgreSQL/SQLite; BOM for Excel Arabic compat |
| Graph export | `.csv` pipe-delimited | UTF-8-sig (BOM) | Neo4j `neo4j-admin import` format |

**CSV settings (from Settings class):**
- Delimiter: `|` (pipe) — avoids conflicts with Arabic text which never contains pipe
- Encoding: `utf-8-sig` — BOM required for Excel to render Arabic correctly
- NULL representation: empty string `""`

---

## 5. Complete Data Model & Database Schema

All records are Python dataclasses in `models/schema.py`. The `to_dict()` method returns a plain dict suitable for JSON serialization or CSV export. List/dict fields are serialized to JSON strings in CSV output via `LegislationExporter._safe_row()`.

### 5.1 Document

One row per legislation document. Primary table.

| Column | Type | Notes |
|--------|------|-------|
| doc_id | TEXT PRIMARY KEY | UUID5 from doc_slug |
| doc_slug | TEXT UNIQUE NOT NULL | e.g. `law-2018-34` |
| title_ar | TEXT NOT NULL | Full Arabic title from LOB |
| title_en | TEXT | English title (manual or future translation) |
| doc_type | TEXT NOT NULL | law, regulation, instruction, decision, circular, agreement, treaty, royal_decree, royal_order, royal_will, constitution, declaration, unknown |
| doc_number | TEXT | e.g. "34" |
| issue_year | INTEGER | Gregorian year |
| issuing_entity_id | TEXT | FK to entities.entity_id |
| issuing_entity_name_ar | TEXT | Denormalized copy |
| official_gazette_number | TEXT | عدد الجريدة الرسمية |
| publication_date | TEXT | ISO 8601: YYYY-MM-DD |
| effective_date | TEXT | ISO 8601 date |
| repeal_date | TEXT | ISO 8601 date; NULL if active |
| status | TEXT NOT NULL | active, amended, repealed, draft, pending |
| status_normalized | TEXT NOT NULL | Canonical status value |
| source_status_text | TEXT | Raw Arabic status from LOB, e.g. "نافذ" |
| legal_basis_text | TEXT | Full استناداً/بناءً clause text |
| applicability_scope | TEXT | general, government_wide, sector_specific, entity_specific, internal, unknown |
| applicability_sectors | TEXT | JSON array string |
| applicability_entities | TEXT | JSON array string |
| source_url | TEXT NOT NULL | LOB detail page URL |
| fetch_date | TEXT | ISO 8601 datetime UTC |
| raw_html_path | TEXT | Relative path to saved HTML file |
| raw_text_path | TEXT | Relative path to saved text file |
| clean_json_path | TEXT | Relative path to clean.json |
| needs_review | BOOLEAN | Flag for manual review |
| has_attachment | BOOLEAN | True if PDF attachments found |
| notes | TEXT | Parser warnings and notes |

### 5.2 DocumentVersion

One row per version. Current state: all documents have exactly one version (version 1, original).

| Column | Type | Notes |
|--------|------|-------|
| version_id | TEXT PRIMARY KEY | e.g. `law-2018-34-v1` |
| doc_id | TEXT NOT NULL | FK to documents |
| doc_slug | TEXT NOT NULL | Denormalized |
| version_number | INTEGER | 1 for original |
| version_type | TEXT | original, amendment, consolidated |
| effective_from | TEXT | ISO 8601 date |
| effective_to | TEXT | NULL = current version |
| is_current | BOOLEAN | Exactly one per doc_id must be TRUE |
| amendment_doc_id | TEXT | FK to the amending document (if amendment version) |
| amendment_doc_slug | TEXT | Denormalized |
| full_text_original | TEXT NOT NULL | Lightly cleaned, no semantic changes |
| full_text_normalized | TEXT NOT NULL | Fully normalized for search |
| source_url | TEXT | URL used for this version |
| version_notes | TEXT | — |

### 5.3 Section

One row per structural unit. Types: preamble, part, chapter, article, paragraph, clause, annex, title.

| Column | Type | Notes |
|--------|------|-------|
| section_id | TEXT PRIMARY KEY | e.g. `law-2018-34-v1-art-0003` |
| version_id | TEXT NOT NULL | FK to versions |
| doc_id | TEXT NOT NULL | FK to documents |
| doc_slug | TEXT NOT NULL | Denormalized |
| section_type | TEXT NOT NULL | preamble, part, chapter, article, paragraph, clause, annex, title |
| section_number | TEXT | "1", "أ", "ثانياً" |
| section_label | TEXT | "المادة (1)", "الباب الثاني" |
| parent_section_id | TEXT | FK to sections (self-referential) |
| display_order | INTEGER NOT NULL | 0-based sequential order in document |
| original_text | TEXT NOT NULL | Text with rules 1–6 applied only |
| normalized_text | TEXT NOT NULL | Text with all 10 rules applied |
| word_count | INTEGER | Split on whitespace |
| compliance_relevant | BOOLEAN | NULL until Layer 2 |
| contains_obligation | BOOLEAN | يجب / يلزم patterns. NULL until Layer 2 |
| contains_prohibition | BOOLEAN | يُحظر / لا يجوز patterns. NULL until Layer 2 |
| contains_approval_requirement | BOOLEAN | الحصول على موافقة patterns. NULL until Layer 2 |
| contains_deadline | BOOLEAN | خلال مدة patterns. NULL until Layer 2 |
| contains_exception | BOOLEAN | استثناءً patterns. NULL until Layer 2 |
| contains_reporting_requirement | BOOLEAN | يرفع تقريراً patterns. NULL until Layer 2 |
| applicability_targets | TEXT | JSON array. NULL until Layer 2 |
| legal_rules_json | TEXT | JSON. NULL until Layer 2 |
| evidence_hints_json | TEXT | JSON. NULL until Layer 2 |

### 5.4 Entity

Named institutional entity extracted from legislation text.

| Column | Type | Notes |
|--------|------|-------|
| entity_id | TEXT PRIMARY KEY | e.g. `entity-ministry-finance` |
| entity_slug | TEXT NOT NULL | URL-safe slug |
| entity_name_ar | TEXT NOT NULL | Arabic name as found in text |
| entity_name_en | TEXT | English name (manual) |
| entity_type | TEXT | ministry, authority, council, court, department, company, person, other |
| parent_entity_id | TEXT | For hierarchical entities |
| notes | TEXT | — |

### 5.5 EntityRole (document_entities)

Junction table linking documents to entities.

| Column | Type | Notes |
|--------|------|-------|
| role_id | TEXT PRIMARY KEY | UUID5 |
| doc_id | TEXT NOT NULL | FK to documents |
| entity_id | TEXT NOT NULL | FK to entities |
| entity_name_ar | TEXT NOT NULL | Extracted text |
| role | TEXT NOT NULL | issuer, target, mentioned |
| source_section_id | TEXT | FK to section where entity was found |
| extracted_text | TEXT | Surrounding sentence context |
| extraction_method | TEXT | rule_based, keyword, manual, model |

### 5.6 Topic

Taxonomy node from topics.yaml.

| Column | Type | Notes |
|--------|------|-------|
| topic_id | TEXT PRIMARY KEY | e.g. `topic-tax-financial-law` |
| topic_slug | TEXT NOT NULL | — |
| topic_name_ar | TEXT NOT NULL | Arabic name |
| topic_name_en | TEXT | English name |
| parent_topic_id | TEXT | FK to topics (self-referential). NULL for Level 1 |
| topic_level | INTEGER | 1 = primary category, 2 = sub-category |
| description | TEXT | — |

### 5.7 TopicAssignment (document_topics)

| Column | Type | Notes |
|--------|------|-------|
| assignment_id | TEXT PRIMARY KEY | UUID5 |
| doc_id | TEXT NOT NULL | FK to documents |
| topic_id | TEXT NOT NULL | FK to topics |
| topic_name_ar | TEXT NOT NULL | Denormalized |
| is_primary | BOOLEAN | True for the highest-confidence topic |
| confidence | REAL | 0.0–1.0 |
| extraction_method | TEXT | keyword |
| matched_keywords | TEXT | JSON array of matched keyword strings |

### 5.8 DocumentRelationship

Directed edge between two documents.

| Column | Type | Notes |
|--------|------|-------|
| rel_id | TEXT PRIMARY KEY | UUID5 from source+type+target |
| source_doc_id | TEXT NOT NULL | FK to documents |
| source_doc_slug | TEXT NOT NULL | Denormalized |
| target_doc_id | TEXT NOT NULL | May be stub (status=pending) |
| target_doc_slug | TEXT NOT NULL | e.g. `law-1997-22` |
| rel_type | TEXT NOT NULL | AMENDS, REPEALS, BASED_ON, IMPLEMENTS, SUPPLEMENTS, SUPERSEDES, REFERS_TO, APPLIES_TO |
| source_article_ref | TEXT | Article in the source that contains the reference |
| target_article_ref | TEXT | Specific article in target referenced |
| extracted_text | TEXT | The sentence(s) containing the reference |
| confidence | REAL | 0.0–1.0 |
| extraction_method | TEXT | rule_based |
| notes | TEXT | — |

### 5.9 FetchRecord (source_registry)

| Column | Type | Notes |
|--------|------|-------|
| fetch_id | TEXT PRIMARY KEY | UUID5 from URL + timestamp |
| doc_slug | TEXT NOT NULL | — |
| source_url | TEXT NOT NULL | Full LOB detail URL |
| fetch_timestamp | TEXT NOT NULL | ISO 8601 UTC |
| http_status | INTEGER | 200 if selector found, 0 if JS error |
| page_title | TEXT | Page `<title>` content |
| html_file_path | TEXT | Relative path to saved .html |
| text_file_path | TEXT | Relative path to saved .txt |
| fetch_notes | TEXT | Errors, selector fallback warnings |

### 5.10 CleanOutput (JSON only)

Stored as `data/clean/{doc_slug}_clean.json`. Not exported to CSV.

```
doc_slug:              string
source_file:           string — path to raw text input
cleaned_at:            string — ISO 8601 UTC
original_text:         string — rules 1–6 applied
normalized_text:       string — rules 1–10 applied
cleaning_rules_applied: list[string] — e.g. ["strip_html_artifacts", "remove_zero_width", ...]
cleaning_log:          list[dict] — [{rule_name, chars_before, chars_after, change_count}]
```

### 5.11 ValidationResult (in-memory only)

```
check_name:  string — e.g. "required_field_title_ar"
passed:      bool
doc_slug:    string (optional)
record_id:   string (optional)
detail:      string — human-readable failure message
severity:    string — error | warning | info
```

### 5.12 PipelineResult (in-memory aggregation)

```
doc_slug:           string
success:            bool
fetch_record:       FetchRecord (optional)
clean_output:       CleanOutput (optional)
document:           Document (optional)
versions:           list[DocumentVersion]
sections:           list[Section]
entities:           list[Entity]
topic_assignments:  list[TopicAssignment]
relationships:      list[DocumentRelationship]
validation_results: list[ValidationResult]
errors:             list[string]

Properties:
  validation_passed → bool: True if no error-severity ValidationResults failed

Methods:
  summary() → dict: {doc_slug, success, sections, entities, topics, relationships,
                     validation_passed, errors}
```

### 5.13 Graph Node Schemas

**nodes_documents.csv**: `:ID(doc)` | `doc_slug` | `title_ar` | `doc_type` | `issue_year` | `status` | `source_url` | `:LABEL`

**nodes_versions.csv**: `:ID(version)` | `version_id` | `doc_slug` | `version_number` | `version_type` | `is_current` | `effective_from` | `effective_to` | `:LABEL`

**nodes_sections.csv**: `:ID(section)` | `section_id` | `doc_slug` | `section_type` | `section_number` | `section_label` | `display_order` | `word_count` | `:LABEL`

**nodes_entities.csv**: `:ID(entity)` | `entity_id` | `entity_slug` | `entity_name_ar` | `entity_type` | `:LABEL`

**nodes_topics.csv**: `:ID(topic)` | `topic_id` | `topic_slug` | `topic_name_ar` | `topic_level` | `:LABEL`

### 5.14 Graph Edge Schemas

| Edge File | Type | Additional Properties |
|-----------|------|-----------------------|
| edges_has_version.csv | HAS_VERSION | is_current |
| edges_has_section.csv | HAS_SECTION | display_order, section_type |
| edges_issued_by.csv | ISSUED_BY | — |
| edges_has_topic.csv | HAS_TOPIC | is_primary, confidence |
| edges_amends.csv | AMENDS | extracted_text, confidence |
| edges_repeals.csv | REPEALS | extracted_text, confidence |
| edges_based_on.csv | BASED_ON | extracted_text, confidence |
| edges_refers_to.csv | REFERS_TO | extracted_text, confidence |
| edges_implements.csv | IMPLEMENTS | extracted_text, confidence |
| edges_supplements.csv | SUPPLEMENTS | extracted_text, confidence |
| edges_supersedes.csv | SUPERSEDES | extracted_text, confidence |

**Future edge files (headers only, for Layer 2):**

| File | Type |
|------|------|
| edges_future_has_obligation.csv | HAS_OBLIGATION |
| edges_future_has_prohibition.csv | HAS_PROHIBITION |
| edges_future_has_exception.csv | HAS_EXCEPTION |
| edges_future_requires_approval.csv | REQUIRES_APPROVAL |
| edges_future_has_deadline.csv | HAS_DEADLINE |
| edges_future_requires_reporting.csv | REQUIRES_REPORTING |

---

## 6. Complete Class Reference

### 6.1 `LOBFetcher` — pipeline/fetcher.py

Playwright-based fetcher. Manages browser lifecycle, file saving, and source registry.

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(settings)` | Sets up directories and loguru rotation at `logs/fetcher.log` |
| `fetch_sync` | `(url, doc_slug, wait_selector=None) → FetchRecord` | Synchronous wrapper for scripts |
| `fetch_page` | `async (url, doc_slug, wait_selector=None) → FetchRecord` | Core Playwright fetch: launches Chromium headless, sets locale `ar-JO` + Arabic Accept-Language header, navigates with `wait_until="networkidle"`, waits for configured selector, extracts text via `page.evaluate()` after removing nav/header/footer elements, saves HTML + text, appends registry row |
| `fetch_batch` | `async (items) → list[FetchRecord]` | Sequential async batch. items: `[{url, doc_slug, wait_selector?}]` |
| `fetch_batch_sync` | `(items) → list[FetchRecord]` | Synchronous wrapper for fetch_batch |
| `_try_selectors` | `async (page, selectors) → str or None` | Try each CSS selector in order; return first hit's `innerText` |
| `_append_registry` | `(record: FetchRecord)` | Append one row to `data/raw/source_registry.csv` |
| `load_registry` | `() → list[dict]` | Load all rows from source_registry.csv |
| `already_fetched` | `(doc_slug) → bool` | Check if doc_slug is in registry |
| `latest_fetch` | `(doc_slug) → dict or None` | Return most recent registry entry for slug |

**Additional behavior:**
- On selector miss: saves debug screenshot to `logs/{slug}_{ts}_debug.png` and continues with fallback
- On exception: saves error screenshot to `logs/{slug}_{ts}_error.png`
- Sleeps `FETCH_DELAY_SECONDS` (default 2.0s) after every page

---

### 6.2 `LOBParser` — pipeline/parser.py

Converts raw HTML into a structured ParsedDocument dictionary.

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(settings)` | Stores settings; no I/O |
| `parse_html` | `(raw_html, doc_slug, source_url="") → dict` | Main entry. Returns ParsedDocument dict |
| `parse_text_file` | `(text_path, doc_slug, source_url="") → dict` | Parse from saved .txt (skips BS4) |
| `_extract_title` | `(soup, notes) → str` | Tries `LOB_SELECTORS["title"]` cascade, then h1/h2, then first Arabic line |
| `_extract_body_text` | `(soup, notes) → str` | Tries `content_body` selectors, falls back to full body. Inserts `\n` before every block element before extracting text |
| `_extract_metadata` | `(title_ar, context_text, doc_slug, notes) → dict` | Extracts: doc_type, doc_number, issue_year, gazette, dates, legal_basis, entities, status via ArabicTextUtils |
| `_extract_attachments` | `(soup, base_url) → list[dict]` | Find PDF links and attachment-labelled anchors |
| `_segment_sections` | `(text, notes) → list[ParsedSection]` | Line-by-line classification into ParsedSection hierarchy |

**ParsedSection class:**
```
section_type:   str — preamble, part, chapter, article, paragraph, clause, annex, title
section_number: str or None
section_label:  str or None
raw_text:       str — accumulated text belonging to this section
display_order:  int — 0-based sequential index
parent_order:   int or None — display_order of the parent section
```

**ParsedDocument dict:**
```python
{
    "doc_slug":    str,
    "source_url":  str,
    "metadata":    dict,          # fields match Document schema
    "raw_text":    str,           # full body, artifacts removed
    "sections":    list[ParsedSection],
    "attachments": list[dict],    # [{"url": "...", "label": "..."}]
    "parse_notes": list[str],     # WARN/INFO from parser steps
}
```

---

### 6.3 `ArabicTextCleaner` — pipeline/cleaner.py

Runs the 10-rule cleaning pipeline. Saves CleanOutput and updates cleaning_log.csv.

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(settings)` | Sets up directories and loguru file handler |
| `clean` | `(raw_text, doc_slug, source_file="") → CleanOutput` | Full pipeline. Saves to disk and returns CleanOutput |
| `clean_section_text` | `(text) → tuple[str, str]` | Clean a single section. Returns `(original_text, normalized_text)` |
| `_apply_rule` | `(text, rule_name, fn) → tuple[str, dict]` | Apply one cleaning function, record char-change count |
| `_sanity_check` | `(text, doc_slug) → list[str]` | Post-clean checks: Arabic present, len > 200, "المادة" found |
| `_save_clean_json` | `(output: CleanOutput)` | Write `data/clean/{doc_slug}_clean.json` |
| `_append_cleaning_log` | `(output, warn_notes)` | Append row to `data/clean/cleaning_log.csv` |
| `load_clean_output` | `(doc_slug) → CleanOutput or None` | Load previously saved CleanOutput from disk |

---

### 6.4 `LegislationStructurer` — pipeline/structurer.py

Assembles all typed record objects from parser + cleaner outputs into a complete PipelineResult.

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(settings)` | Loads topic taxonomy, initializes global entity_registry dict, creates internal ArabicTextCleaner |
| `structure` | `(parsed_doc, clean_output) → PipelineResult` | Main entry. Runs all 10 sub-steps, saves to disk |
| `_build_document` | `(doc_slug, metadata, source_url, clean_output) → Document` | Creates Document record from extracted metadata |
| `_build_version` | `(document, clean_output, source_url) → DocumentVersion` | Creates version 1 (original) record |
| `_build_sections` | `(parsed_sections, version, document) → tuple[list[Section], dict]` | Two-pass: first assigns section_ids, second builds Section objects with parent resolution + compliance flag scanning per section |
| `_extract_entities` | `(document, full_text) → tuple[list[Entity], list[EntityRole]]` | Regex extraction + validate_entity_candidate() filter + global dedup via entity_registry; first entity = issuer role |
| `_classify_topics` | `(document, normalized_text) → tuple[list[Topic], list[TopicAssignment]]` | Weighted keyword scoring against topics.yaml; first sorted by confidence = primary |
| `_detect_relationships` | `(document, original_text, parse_notes) → list[DocumentRelationship]` | BASED_ON from legal basis, AMENDS/REPEALS from trigger words + ref-count heuristic, REFERS_TO for remainder |
| `_add_relationship` | `(relationships, seen, source, target_slug, rel_type, text, confidence)` | Dedup guard + confidence threshold check before adding |
| `_load_topics` | `() → list[dict]` | Read and parse config/topics.yaml |
| `_save_structured` | `(result: PipelineResult)` | Write `data/structured/docs/{slug}.json`, call _save_summary(), _update_documents_index() |
| `_save_summary` | `(result, structured_at)` | Write lightweight summary to `data/structured/summaries/{slug}_summary.json` |
| `_update_documents_index` | `(result)` | Append or update entry in `data/structured/documents_index.json` |
| `load_structured` | `(doc_slug) → dict or None` | Load previously saved structured document from disk |

---

### 6.5 `PipelineValidator` — pipeline/validator.py

Runs all QA checks on a PipelineResult. Returns list of ValidationResult objects.

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(settings)` | Stores settings |
| `validate` | `(result: PipelineResult) → list[ValidationResult]` | Run all checks. Returns results list |
| `has_errors` | `(results) → bool` | True if any error-severity check failed |
| `has_warnings` | `(results) → bool` | True if any warning-severity check failed |
| `validate_export_files` | `() → list[ValidationResult]` | Check all expected relational + graph CSV files exist |
| `_check_required_fields` | `(doc) → list[ValidationResult]` | Requires: doc_slug, doc_id, title_ar, doc_type, status, source_url — severity: error |
| `_check_arabic_text` | `(doc) → list[ValidationResult]` | title_ar must contain Arabic characters — severity: error |
| `_check_status_valid` | `(doc) → list[ValidationResult]` | status must be in {active, amended, repealed, draft, pending} |
| `_check_source_url` | `(doc) → list[ValidationResult]` | source_url must start with "http" |
| `_check_raw_files_exist` | `(doc) → list[ValidationResult]` | raw_text_path and clean_json_path must exist on disk |
| `_check_versions` | `(versions, doc) → list[ValidationResult]` | At least 1 version; exactly 1 is_current=True; full_text not empty; effective date ordering |
| `_check_sections` | `(sections, versions) → list[ValidationResult]` | At least 1 section; section_id/version_id/doc_id populated; display_order unique per version |
| `_check_relationships` | `(relationships, doc_id) → list[ValidationResult]` | No self-references; confidence in [0, 1] |
| `_check_topics` | `(topic_assignments) → list[ValidationResult]` | confidence in [0, 1]; at most 1 is_primary |
| `_check_metadata_quality` | `(doc, full_text) → list[ValidationResult]` | Warns if issue_year or doc_number absent |
| `_check_section_artifact_leak` | `(sections) → list[ValidationResult]` | Warns if known LOB UI artifacts found in section text |

---

### 6.6 `LegislationExporter` — pipeline/exporter.py

Produces all relational and graph CSV exports.

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(settings)` | Sets up directories and loguru file handler |
| `export_result` | `(result: PipelineResult)` | Export a single PipelineResult. Appends to existing CSVs |
| `export_all` | `()` | Full rebuild. Clears all CSVs first, then re-exports all `data/structured/docs/*.json` |
| `_append_csv` | `(name, rows, fields)` | Append rows to `exports/relational/{name}.csv`; write header row if file is new |
| `_append_graph_csv` | `(name, rows, fields)` | Append rows to `exports/graph/{name}.csv` |
| `_export_graph_edges` | `(result)` | Write all edge files: HAS_VERSION, HAS_SECTION, ISSUED_BY, HAS_TOPIC + all relationship types |
| `_safe_row` | `(row, fields) → dict` | Serialize: `None→""`, `bool→"true/false"`, `list/dict→json.dumps()` |
| `_compliance_rows` | `(sections) → list[dict]` | Extract compliance flag rows from Section list |
| `_clear_exports` | `()` | Delete all CSVs in relational/ and graph/ before rebuild |
| `_write_future_edge_headers` | `()` | Write header-only files in graph/future/ |

**Relational CSV column order (exact):**

`documents.csv`:
doc_id | doc_slug | title_ar | title_en | doc_type | doc_number | issue_year | issuing_entity_id | issuing_entity_name_ar | official_gazette_number | publication_date | effective_date | repeal_date | status | status_normalized | source_status_text | legal_basis_text | applicability_scope | applicability_sectors | applicability_entities | source_url | fetch_date | raw_html_path | raw_text_path | has_attachment | needs_review | notes

`versions.csv`:
version_id | doc_id | doc_slug | version_number | version_type | effective_from | effective_to | is_current | amendment_doc_id | amendment_doc_slug | full_text_original | full_text_normalized | source_url | version_notes

`sections.csv`:
section_id | version_id | doc_id | doc_slug | section_type | section_number | section_label | parent_section_id | display_order | word_count | original_text | normalized_text

`section_compliance_flags.csv`:
section_id | doc_slug | version_id | section_type | section_number | compliance_relevant | contains_obligation | contains_prohibition | contains_approval_requirement | contains_deadline | contains_exception | contains_reporting_requirement | applicability_targets | legal_rules_json | evidence_hints_json

`entities.csv`:
entity_id | entity_slug | entity_name_ar | entity_name_en | entity_type | parent_entity_id | notes

`topics.csv`:
topic_id | topic_slug | topic_name_ar | topic_name_en | parent_topic_id | topic_level | description

`document_topics.csv`:
assignment_id | doc_id | topic_id | topic_name_ar | is_primary | confidence | extraction_method | matched_keywords

`document_relationships.csv`:
rel_id | source_doc_id | source_doc_slug | target_doc_id | target_doc_slug | rel_type | source_article_ref | target_article_ref | extracted_text | confidence | extraction_method | notes

---

### 6.7 `ArabicTextUtils` — utils/arabic_utils.py

All static methods. Import as `ATU = ArabicTextUtils`.

| Method | Description |
|--------|-------------|
| `remove_html_artifacts(text)` | Strip HTML tags; decode `&amp;` `&nbsp;` `&lt;` `&gt;` `&quot;` `&#8206;` `&#8207;` |
| `remove_lob_artifacts(text)` | Remove known LOB Angular UI label lines. Checks each line against `LOB_UI_ARTIFACTS` frozenset (17 entries) |
| `remove_zero_width(text)` | Remove U+200B U+200C U+200D U+FEFF U+00AD |
| `remove_tatweel(text)` | Remove U+0640 kashida |
| `remove_harakat(text)` | Remove Arabic diacritics U+064B–U+065F. Uses pyarabic when available |
| `normalize_alef(text)` | `آ أ إ ٱ → ا` (normalized copy only) |
| `normalize_tamarbouta(text)` | `ة → ه` at word boundaries (normalized copy only) |
| `normalize_yeh(text)` | `ى → ي` (normalized copy only) |
| `normalize_spaces(text)` | Collapse multi-space/tabs to single space; 3+ newlines to 2 |
| `convert_arabic_to_western_digits(text)` | `٠١٢٣٤٥٦٧٨٩ → 0-9` (normalized copy only) |
| `normalize(text, for_search=True)` | Full pipeline: all 10 rules if for_search=True; rules 1–6 only if False |
| `remove_page_markers(text)` | Remove "صفحة N", "Page N of M", "– N –" patterns |
| `detect_article(line)` | Return regex match if line is an article header (المادة) |
| `detect_part(line)` | Return regex match if line is a Part header (الباب / الجزء) |
| `detect_chapter(line)` | Return regex match if line is a Chapter header (الفصل / القسم) |
| `detect_paragraph_letter(line)` | Return match for Arabic-letter paragraph marker (أ. ب. ج.) |
| `detect_paragraph_ordinal(line)` | Return match for ordinal paragraph marker (أولاً: ثانياً:) |
| `detect_annex(line)` | Return match for annex/table/form header |
| `extract_legal_basis(text)` | Find `استناداً/بناءً/بمقتضى` clauses → `list[{trigger, basis_text, start}]` |
| `extract_cross_references(text)` | Find "القانون رقم N لسنة YYYY" → `list[{doc_number, year, start, raw}]` |
| `extract_doc_number_year(text)` | Extract (number, year) from title/heading → tuple or None |
| `extract_gazette_number(text)` | Extract Official Gazette number → string or None |
| `extract_dates(text)` | Find all dates → `list[YYYY-MM-DD]`. Handles MM/DD/YYYY and DD/MM/YYYY |
| `detect_doc_type(title, type_map)` | Match first word of title → English doc type string |
| `validate_entity_candidate(name)` | Return False if candidate is too long or contains known false-positive words |
| `extract_entities(text)` | Extract named institutions via ministry/authority/council/court/department patterns → `list[{entity_name_ar, entity_type, start}]` |
| `detect_amendment(text)` | Return True if amendment trigger found (`يُعدَّل` etc.) |
| `detect_repeal(text)` | Return True if repeal trigger found (`يُلغى` etc.) |
| `classify_scope(text)` | Classify applicability scope from text patterns |
| `scan_compliance_flags(text)` | Scan for rule-type patterns → `dict[str, bool]` with keys: `compliance_relevant`, `contains_obligation`, `contains_prohibition`, `contains_approval_requirement`, `contains_deadline`, `contains_exception`, `contains_reporting_requirement` |
| `contains_arabic(text)` | Return True if text contains at least one Unicode Arabic character |
| `count_words(text)` | Return word count (split on whitespace) |

**`LOB_UI_ARTIFACTS` frozenset (17 strings removed by `remove_lob_artifacts`):**

These AngularJS view label strings are injected by the LOB app into the content div and must be stripped before section segmentation. The most impactful is "ارتباطات المادة" which appears after every article header:

```
ديوان التشريع والرأي
التشريعات الأردنية
ارتباطات المادة
رقم التشريع
سنة التشريع
نوع التشريع
الاسم التفصيلي
التشريع كما صدر
التشريعات المرتبطة
طباعة التشريع
العودة الى الصفحة السابقة
العودة إلى الصفحة السابقة
تعديل التشريع
المعلومات الاساسية
المعلومات الأساسية
سجل التعديلات
الجريدة الرسمية
```

**Compiled regex constants (module-level):**

`RE_ARTICLE`, `RE_PART`, `RE_CHAPTER`, `RE_PARAGRAPH_LETTER`, `RE_PARAGRAPH_ORDINAL`, `RE_ANNEX`, `RE_LEGAL_BASIS`, `RE_DOC_REFERENCE`, `RE_ARTICLE_REF`, `RE_DOC_NUMBER_YEAR`, `RE_GAZETTE`, `RE_DATE_DMY`, `RE_AMEND`, `RE_REPEAL`, `RE_MINISTRY`, `RE_AUTHORITY`, `RE_COUNCIL`, `RE_COURT`, `RE_DEPARTMENT`, `RE_SCOPE_ALL`, `RE_SCOPE_ALL_GOV`, `RE_OBLIGATION`, `RE_PROHIBITION`, `RE_DEADLINE`, `RE_EXCEPTION`, `RE_APPROVAL`, `RE_REPORTING`

---

### 6.8 `IDGenerator` — utils/id_generator.py

All static methods. Import as `IDG = IDGenerator`.

| Method | Returns | Example |
|--------|---------|---------|
| `doc_slug(doc_type_en, year, number, title_slug=None)` | str | `law-2018-34` |
| `doc_id(doc_slug)` | UUID5 string | |
| `version_id(doc_slug, version_number)` | str | `law-2018-34-v1` |
| `version_uuid(version_id)` | UUID5 string | |
| `section_id(version_id, section_type, display_order)` | str | `law-2018-34-v1-art-0003` |
| `section_uuid(section_id)` | UUID5 string | |
| `entity_slug(entity_name_ar, entity_type)` | str | `ministry-finance` |
| `entity_id(entity_slug)` | str | `entity-ministry-finance` |
| `entity_uuid(entity_id)` | UUID5 string | |
| `topic_id(topic_slug)` | str | `topic-tax-financial-law` |
| `topic_uuid(topic_id)` | UUID5 string | |
| `relationship_id(source_doc_slug, rel_type, target_doc_slug)` | UUID5 string | |
| `fetch_id(url, timestamp)` | UUID5 string | |
| `topic_assignment_id(doc_id, topic_id)` | UUID5 string | |
| `entity_role_id(doc_id, entity_id, role)` | UUID5 string | |

UUID5 namespaces per record type (preventing cross-type ID collisions):
- Documents: `6ba7b810-9dad-11d1-80b4-00c04fd430c8`
- Versions: `6ba7b811-9dad-11d1-80b4-00c04fd430c8`
- Sections: `6ba7b812-9dad-11d1-80b4-00c04fd430c8`
- Entities: `6ba7b813-9dad-11d1-80b4-00c04fd430c8`
- Topics: `6ba7b814-9dad-11d1-80b4-00c04fd430c8`
- Relationships: `6ba7b815-9dad-11d1-80b4-00c04fd430c8`
- Fetch records: `6ba7b816-9dad-11d1-80b4-00c04fd430c8`

---

### 6.9 `LOBListingScraper` — scripts/run_first_100.py

Scrapes the AngularJS listing page to discover legislation metadata without pre-known URLs.

| Method | Signature | Description |
|--------|-----------|-------------|
| `__init__` | `(settings, headless=True)` | headless=False shows browser window |
| `scrape_sync` | `(limit, law_type_ar=None, active_only=False) → list[dict]` | Synchronous wrapper |
| `scrape_listing` | `async (limit, law_type_ar=None, active_only=False) → list[dict]` | Navigate to listing, apply filters, paginate, collect all items |
| `_extract_current_page` | `async (page, seen_ids, active_only) → list[dict]` | Monkey-patch + click rows to capture Angular scope items |
| `_build_item` | `(entry: dict) → dict or None` | Normalize raw Angular object → standard item format |
| `_apply_filters` | `async (page, law_type_ar, active_only)` | Set search form dropdowns |
| `_submit_search` | `async (page)` | Click submit and wait for result rows |
| `_goto_next_page` | `async (page) → bool` | Click next-page; return False if no more pages |
| `_save_diagnostic` | `async (page, step_name)` | Save screenshot to logs/ |
| `_log_page_debug` | `async (page)` | Log page title + URL |

---

### 6.10 `Settings` — config/settings.py

Central configuration object. All fields are class attributes.

| Attribute | Default | Description |
|-----------|---------|-------------|
| `LOB_BASE_URL` | `"https://lob.gov.jo"` | LOB base URL |
| `PLAYWRIGHT_TIMEOUT` | 30000 | Page wait timeout (ms) |
| `FETCH_DELAY_SECONDS` | 2.0 | Sleep between fetches |
| `PLAYWRIGHT_HEADLESS` | True | Headless browser mode |
| `LOB_SELECTORS` | dict | CSS selectors for LOB DOM elements (see Section 8) |
| `CSV_DELIMITER` | `"\|"` | Pipe character |
| `CSV_ENCODING` | `"utf-8-sig"` | BOM encoding for Excel |
| `CSV_NULL` | `""` | NULL representation |
| `TOPIC_CONFIDENCE_THRESHOLD` | 0.4 | Min confidence for topic assignment |
| `RELATIONSHIP_CONFIDENCE_THRESHOLD` | 0.5 | Min confidence for relationship |
| `DOC_TYPE_MAP` | dict | Arabic doc type → English slug |
| `SECTION_TYPE_LABELS` | dict | section_type → Arabic header keywords |
| `ARABIC_ORDINALS` | dict | Arabic ordinal word → integer |
| `DATA_DIR`, `RAW_DIR`, `CLEAN_DIR`, etc. | Path | All data directory paths |
| `EXPORTS_DIR`, `RELATIONAL_DIR`, `GRAPH_DIR` | Path | Export directory paths |
| `LOGS_DIR`, `REPORTS_DIR`, `INDEXES_DIR` | Path | Misc directory paths |
| `SOURCE_REGISTRY_PATH`, `CLEANING_LOG_PATH` | Path | Append-only log file paths |
| `TOPICS_CONFIG_PATH` | Path | Path to topics.yaml |
| `ensure_directories()` | method | Create all required directories |

---

## 7. Complete Data Flow

### Stage 1: Fetch

```
Input:  LOB detail URL + doc_slug
        https://www.lob.gov.jo/?v=2&lang=ar#!/LegislationDetails?LegislationID=36&LegislationType=1&isMod=false

1. Launch Chromium headless (locale="ar-JO", Accept-Language: ar-JO,ar)
2. page.goto(url, wait_until="networkidle")
3. page.wait_for_selector("div.clicked-legislation-header", timeout=15000)
4. page.evaluate() — strip nav/header/footer, return body.innerText
5. page.content() — get full HTML
6. Save → data/raw/html/{doc_slug}_{YYYYMMDD_HHMMSS}.html
7. Save → data/raw/text/{doc_slug}_{YYYYMMDD_HHMMSS}.txt
8. Append row → data/raw/source_registry.csv
9. asyncio.sleep(FETCH_DELAY_SECONDS)

Output: FetchRecord
```

### Stage 2: Parse

```
Input:  raw HTML + doc_slug + source_url

1. BeautifulSoup(raw_html, "lxml")
2. soup.decompose() script, style, nav, header, footer, noscript, iframe, button
3. Extract title via LOB_SELECTORS["title"] cascade
4. Extract body via LOB_SELECTORS["content_body"] cascade
5. ATU.remove_lob_artifacts() — strip Angular UI labels
6. _extract_metadata() — doc_type, number, year, gazette, dates, basis, entities, status
7. _extract_attachments() — PDF links
8. _segment_sections() — line-by-line Part/Chapter/Article/Para/Annex detection

Output: ParsedDocument dict
```

### Stage 3: Clean

```
Input:  raw_text string

Stage A — original_text (rules 1–6):
  1. remove_html_artifacts()
  2. remove_zero_width()
  3. remove_tatweel()
  4. remove_harakat()
  5. remove_page_markers()
  6. normalize_spaces()

Stage B — normalized_text (rules 7–10 on top of Stage A):
  7. convert_arabic_to_western_digits()
  8. normalize_alef()
  9. normalize_tamarbouta()
 10. normalize_yeh()

Stage C — sanity checks:
  - contains_arabic() must be True
  - len(text) >= 200
  - "المادة" pattern present

Output: CleanOutput → saved to data/clean/{doc_slug}_clean.json
Also appends: data/clean/cleaning_log.csv
```

### Stage 4: Structure

```
Input:  ParsedDocument + CleanOutput

 1. _build_document()     → Document record
 2. _build_version()      → DocumentVersion (version 1, original)
 3. _build_sections()     → list[Section] + order→id map
                             • assign section_ids via IDGenerator
                             • resolve parent_section_id
                             • call clean_section_text() per section
                             • call scan_compliance_flags() per section
 4. _extract_entities()   → list[Entity] + list[EntityRole]
                             • regex for وزارة, هيئة, مجلس, محكمة, دائرة/مديرية
                             • validate_entity_candidate() filter
                             • global dedup via entity_registry
 5. _classify_topics()    → list[Topic] + list[TopicAssignment]
                             • title match: +0.40
                             • early text (first 800 chars): +0.20
                             • full body: +0.05
                             • threshold: 0.40
 6. _detect_relationships() → list[DocumentRelationship]
                             • BASED_ON: legal basis clauses
                             • AMENDS/REPEALS: trigger words + ref count
                             • REFERS_TO: other cross-references
 7. classify_scope()       → document.applicability_scope
 8. _save_structured()     → data/structured/docs/{slug}.json
 9. _save_summary()        → data/structured/summaries/{slug}_summary.json
10. _update_documents_index() → data/structured/documents_index.json

Output: PipelineResult
```

### Stage 5: Validate

```
Input:  PipelineResult

Checks run in order:
  required_fields → arabic_text → status_valid → source_url →
  raw_files_exist → versions → sections → relationships →
  topics → metadata_quality → section_artifact_leak

All checks run regardless of prior failures. Results aggregated.

Output: list[ValidationResult]
```

### Stage 6: Export

```
Input:  PipelineResult

Relational (8 CSV files appended):
  documents.csv, versions.csv, sections.csv, entities.csv,
  topics.csv, document_topics.csv, document_relationships.csv,
  section_compliance_flags.csv

Graph nodes (5 CSV files appended):
  nodes_documents.csv, nodes_versions.csv, nodes_sections.csv,
  nodes_entities.csv, nodes_topics.csv

Graph edges (up to 12 CSV files appended):
  edges_has_version.csv, edges_has_section.csv, edges_issued_by.csv,
  edges_has_topic.csv, edges_amends.csv, edges_repeals.csv,
  edges_based_on.csv, edges_refers_to.csv, edges_implements.csv,
  edges_supplements.csv, edges_supersedes.csv

Output: writes/appends to exports/relational/ and exports/graph/
```

---

## 8. LOB Website & Scraping Strategy

### Website Technology

- Base: `https://www.lob.gov.jo/?v=0&lang=ar`
- Listing page hash route: `#!Jordanian-Legislation`
- Detail page hash route: `#!/LegislationDetails?LegislationID={id}&LegislationType={type_id}&isMod=false`
- Framework: AngularJS 1.x — all content client-side rendered
- Result rows: `ng-click="LegislationLaw.LinkToDetails(item)"` — **no href attributes**
- Traditional scraping with requests/urllib cannot see rendered content

### AngularJS Monkey-Patch Strategy

```javascript
// 1. Get Angular scope from the first result row
var rows = document.querySelectorAll('tr[ng-click]');
var scope = angular.element(rows[0]).scope();
// Walk up parent scope chain until LegislationLaw is found
while (scope && !scope.LegislationLaw) { scope = scope.$parent; }

// 2. Replace the navigation function with a capture function
var captured = [];
var originalFn = scope.LegislationLaw.LinkToDetails;
scope.LegislationLaw.LinkToDetails = function(item) { captured.push(item); };

// 3. Click every row — Angular evaluates ng-click and calls our function
rows.forEach(function(row) { row.click(); });

// 4. Restore immediately
scope.LegislationLaw.LinkToDetails = originalFn;

// 5. Serialize: use for...in (not Object.keys) to traverse prototype chain
return captured.map(function(raw) {
    var clean = {};
    for (var k in raw) {
        if (k.slice(0, 2) !== '$$') clean[k] = raw[k];  // skip Angular internals
    }
    return clean;
});
```

### Confirmed LOB CSS Selectors (verified 2026-03-11)

```python
LOB_SELECTORS = {
    "content_body": [
        "div.clicked-legislation-content",  # confirmed working
        "div.main-content",
        "div[dir='rtl']",
    ],
    "title": [
        "div.clicked-legislation-header h3",  # confirmed working
        "div.clicked-legislation-header h2",
        "div.clicked-legislation-header h1",
        "h3.animated",
    ],
    "metadata_block": [
        "div.clicked-legislation-body",     # confirmed: tabs panel
        "div.clicked-legislation-content",
    ],
    "wait_for": "div.clicked-legislation-header",  # confirmed: signals page ready
}
```

---

## 9. LOB API Field Names

When items are captured from `$scope` via the monkey-patch, the raw Angular field names are:

| LOB Field | Python Type | Description | Pipeline Field |
|-----------|-------------|-------------|----------------|
| `pmk_ID` | int | Primary key (legislation ID) | `legislation_id` |
| `Name` | str | Full Arabic title | `title_ar` |
| `Number` | str or int | Official document number | `doc_number` |
| `Year` | int | Issue year (Gregorian) | `issue_year` |
| `Status_AR` | str | Arabic status: نافذ / ملغى / معدّل / غير ساري / ساري | `source_status_text` |
| `Type` | int | Type ID (see below) | `legislation_type_id` |
| `TypeArName` | str | Arabic type label | `doc_type_ar` |

### LegislationType ID → English slug

| ID | Arabic | English slug |
|----|--------|--------------|
| 1 | دستور | constitution |
| 2 | قانون | law |
| 3 | نظام | regulation |
| 4 | تعليمات | instruction |
| 5 | قرار | decision |
| 6 | منشور | circular |
| 7 | أمر ملكي | royal_order |
| 8 | إرادة ملكية | royal_will |
| 9 | مرسوم ملكي | royal_decree |
| 10 | اتفاقية | agreement |
| 11 | معاهدة | treaty |

### Detail URL Pattern

```
https://www.lob.gov.jo/?v=2&lang=ar#!/LegislationDetails?LegislationID={pmk_ID}&LegislationType={Type}&isMod=false
```

### Status_AR → status_normalized Mapping

| Arabic | Normalized |
|--------|-----------|
| نافذ | active |
| نافذة | active |
| ساري | active |
| ملغى | repealed |
| ملغية | repealed |
| معدّل | amended |
| معدل | amended |
| مؤقت | draft |
| غير ساري | **BUG: maps to active instead of repealed** (see Section 18, Bug 1) |

---

## 10. Arabic Text Cleaning Rules

**Golden rule:** `original_text` must never lose article numbers, legal references, or any Arabic text with legal meaning. All normalization changes live only in `normalized_text`.

| Rule | # | Applies To | Operation |
|------|---|-----------|-----------|
| `strip_html_artifacts` | 1 | Both | Remove HTML tags; decode `&amp;` `&nbsp;` and other HTML entities |
| `remove_zero_width` | 2 | Both | Remove: U+200B (ZWSP), U+200C (ZWNJ), U+200D (ZWJ), U+FEFF (BOM), U+00AD (soft-hyphen) |
| `remove_tatweel` | 3 | Both | Remove U+0640 Arabic Tatweel (kashida stretcher) |
| `remove_harakat` | 4 | Both | Remove all Arabic diacritical marks U+064B–U+065F (Fathatan through Sukun) |
| `remove_page_markers` | 5 | Both | Remove "صفحة N", "Page N of M", "– N –" patterns |
| `normalize_spaces` | 6 | Both | Collapse whitespace: multi-space/tabs → single space; 3+ newlines → 2 newlines |
| `convert_arabic_to_western_digits` | 7 | Normalized only | `٠١٢٣٤٥٦٧٨٩ → 0123456789` |
| `normalize_alef` | 8 | Normalized only | `آ أ إ ٱ → ا` |
| `normalize_tamarbouta` | 9 | Normalized only | `ة → ه` at word boundaries |
| `normalize_yeh` | 10 | Normalized only | `ى → ي` (Alef Maqsoura → Dotted Yeh) |

---

## 11. Parsing Strategy

### Section Hierarchy Detection (priority order)

Lines are classified one at a time against these patterns:

```
1 (highest): Preamble — all lines before the first structural marker
2: Part     — الباب / الجزء + ordinal/number
3: Chapter  — الفصل / القسم + ordinal/number
4: Article  — المادة + (number in parens or ordinal)
5: Paragraph — Arabic letter: أ. ب. ج.  OR  ordinal: أولاً: ثانياً:
6: Clause   — numeric sub-numbering within a paragraph
7: Annex    — ملحق / جدول / نموذج / مرفق
```

When a higher-priority marker is detected, the current accumulated text buffer is flushed as the previous section, and a new section begins.

### Metadata Extraction Regexes

**Document number + year:**
```python
re.compile(r"رقم\s*[\(\（]?\s*(\d+|[\u0660-\u0669]+)\s*[\)\）]?\s*لسنة\s*([\d\u0660-\u0669]{4})")
```

**Official Gazette number:**
```python
re.compile(r"الجريدة الرسمية\s*(?:رقم|عدد|,)?\s*[\(\（]?\s*(\d+)\s*[\)\）]?")
```

**Date pattern:**
```python
re.compile(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})")
```

**Date disambiguation logic:**
- If part2 > 12 and part1 ≤ 12: treat as MM/DD/YYYY
- If part1 > 12 and part2 ≤ 12: treat as DD/MM/YYYY
- If both ≤ 12: default to DD/MM/YYYY (Arabic convention)
- **Bug:** LOB pages appear to use MM/DD/YYYY in some date tables — this can cause off-by-one month errors

**Legal basis:**
```python
re.compile(r"(استناداً|بناءً|عملاً|بمقتضى|وفقاً لأحكام)\s+(.{10,300}?)(?=\n|\.\s|$)")
```

---

## 12. ID Generation Conventions

### Slug Patterns

```
doc_slug:    {doc_type_en}-{year}-{number}
             Examples: law-2018-34  |  regulation-2021-3  |  legislation-36 (fallback)

version_id:  {doc_slug}-v{version_number}
             Example:  law-2018-34-v1

section_id:  {version_id}-{type_abbr}-{display_order:04d}
             Type abbreviations: pre, prt, cha, art, par, cla, anx, ttl
             Example:  law-2018-34-v1-art-0003

entity_id:   entity-{entity_type}-{name_slug}
             Example:  entity-ministry-finance

topic_id:    topic-{topic_slug}
             Example:  topic-tax-financial-law
```

### Fallback Slug

When `doc_number` or `issue_year` cannot be extracted (e.g., constitutions, unusual formatting), the slug falls back to:
```
legislation-{pmk_ID}
```
Example: `legislation-36` (the 1952 Hashemite Constitution).

---

## 13. Topic Classification Strategy

### Taxonomy

- 22 topics at 2 levels in `config/topics.yaml`
- Each topic: `id`, `name_ar`, `name_en`, `level` (1 or 2), `parent` (null for top-level), `keywords_ar` (list)

### Scoring Algorithm

```python
for topic in all_topics:
    confidence = 0.0
    for keyword in topic["keywords_ar"]:
        norm_kw = ATU.normalize(keyword)
        if norm_kw in title_normalized:
            confidence += 0.40     # title match — highest weight
        elif norm_kw in early_text_normalized:  # first 800 chars
            confidence += 0.20
        elif norm_kw in body_normalized:
            confidence += 0.05
    confidence = min(1.0, confidence)
    if confidence >= 0.40:   # TOPIC_CONFIDENCE_THRESHOLD
        assign_topic(...)
```

The highest-confidence topic gets `is_primary=True`. A single title keyword match (0.40) exactly meets the threshold.

### Primary Topic Categories

```
constitutional-administrative-law  القانون الدستوري والإداري
civil-law                          القانون المدني
commercial-law                     القانون التجاري
criminal-law                       القانون الجنائي والعقوبات
labor-social-insurance             قانون العمل والتأمينات الاجتماعية
tax-financial-law                  القانون الضريبي والمالي
investment-law                     قانون الاستثمار
banking-financial-services         القانون المصرفي والخدمات المالية
environmental-law                  قانون البيئة والموارد الطبيعية
health-law                         قانون الصحة العامة
education-law                      قانون التعليم والبحث العلمي
real-estate-law                    قانون العقارات والإنشاءات
transport-infrastructure           قانون النقل والبنية التحتية
it-telecom-law                     قانون تكنولوجيا المعلومات والاتصالات
food-agriculture-law               قانون الغذاء والزراعة
energy-mining-law                  قانون الطاقة والثروة المعدنية
family-personal-status             قانون الأحوال الشخصية والأسرة
social-welfare-law                 قانون الرعاية الاجتماعية والتنمية
military-security-law              القانون العسكري والأمني
intellectual-property              قانون الملكية الفكرية
international-treaties             المعاهدات والاتفاقيات الدولية
customs-trade-law                  قانون الجمارك والتجارة الخارجية
```

---

## 14. Relationship Detection Strategy

### Relationship Types and Detection Logic

| Type | Trigger | Confidence | Logic |
|------|---------|-----------|-------|
| BASED_ON | استناداً / بناءً / بمقتضى / عملاً / وفقاً لأحكام | 0.85 | Extracted from preamble legal basis clause via `extract_legal_basis()` |
| BASED_ON (Constitution) | "الدستور" in legal basis text | 0.90 | Hard-coded target: `constitution-hashemite-kingdom-1952` |
| AMENDS | `detect_amendment()` returns True AND ≤5 cross-references in text | 0.80 | يُعدَّل / تُعدَّل / التعديل trigger words |
| REPEALS | `detect_repeal()` returns True AND ≤3 cross-references in text | 0.80 | يُلغى / تُلغى / ملغى / إلغاء trigger words |
| REFERS_TO | Cross-reference exists but not meeting AMENDS/REPEALS criteria | 0.60 | Generic citation of another law |

All relationships below `RELATIONSHIP_CONFIDENCE_THRESHOLD = 0.5` are discarded. All current types pass.

### Cross-Reference Extraction

```python
RE_DOC_REFERENCE = re.compile(
    r"(?:القانون|النظام|قرار|الاتفاقية)\s+رقم\s*[\(\（]?\s*(\d+)\s*[\)\）]?\s*لسنة\s*(\d{4})"
)
```

Extracted `(number, year)` is converted to a target_doc_slug via `IDGenerator.doc_slug()`. If the target doesn't exist in the database yet, it is stored as a stub relationship — the target doc may be added in a later batch run.

---

## 15. Configuration Reference

### .env Variables

```bash
LOB_BASE_URL=https://lob.gov.jo       # Override default base URL
FETCH_DELAY_SECONDS=2                  # Seconds between page fetches
PLAYWRIGHT_TIMEOUT=30000               # Playwright wait timeout in ms
PROJECT_ROOT=/absolute/path/to/project # Override project root path detection
HTTP_PROXY=http://proxy:8080           # Optional HTTP proxy (not implemented yet)
```

### topics.yaml Entry Structure

```yaml
topics:
  - id: tax-financial-law
    name_ar: القانون الضريبي والمالي
    name_en: Tax & Financial Law
    level: 1
    parent: null
    keywords_ar:
      - ضريبة الدخل
      - ضريبة المبيعات
      - الموازنة العامة
      - الرسوم الجمركية
```

---

## 16. Running the Pipeline

### Single Document

```bash
# Full pipeline from URL
python scripts/run_pipeline.py \
  --url "https://www.lob.gov.jo/?v=2&lang=ar#!/LegislationDetails?LegislationID=36&LegislationType=1&isMod=false" \
  --slug "legislation-36"

# From saved HTML (skip fetch)
python scripts/run_pipeline.py --html-file data/raw/html/law-2018-34_20260314.html --slug "law-2018-34"

# From saved text file (skip fetch and HTML parse)
python scripts/run_pipeline.py --text-file data/raw/text/law-2018-34.txt --slug "law-2018-34"

# Force re-fetch even if already in source_registry
python scripts/run_pipeline.py --url "..." --slug "law-2018-34" --force-refetch

# Skip export step (structure and validate only)
python scripts/run_pipeline.py --url "..." --slug "law-2018-34" --skip-export
```

### Batch: 100 Laws

```bash
# Default batch (100 items)
python scripts/run_first_100.py

# Test run (5 items)
python scripts/run_first_100.py --limit 5

# Resume interrupted batch (skip already-processed slugs)
python scripts/run_first_100.py --resume --limit 100

# Active laws only
python scripts/run_first_100.py --limit 50 --active-only

# Filter by legislation type
python scripts/run_first_100.py --law-type قانون --limit 50

# Show browser (for debugging Angular scraping)
python scripts/run_first_100.py --limit 5 --no-headless
```

### Rebuild All CSV Exports from Existing Structured Data

```bash
python -c "
from config.settings import Settings
from pipeline.exporter import LegislationExporter
LegislationExporter(Settings()).export_all()
print('Done')
"
```

---

## 17. Current Quality Metrics

From batch run completed 2026-03-14T17:56–18:19 UTC.

| Metric | Value | Detail |
|--------|-------|--------|
| Items discovered on LOB | 100 | Via LOBListingScraper |
| Items attempted | 100 | — |
| Pipeline success | 98 (98%) | 2 fatal failures |
| Pipeline failures | 2 (2%) | law-2006-49, law-1972-21 |
| Validation passed | 100% | All 98 pass error-level checks |
| has_publication_date | 99.0% | Only 1 doc missing |
| has_entities | 73.5% | 72 of 98 docs have extracted entities |
| has_topic_assignments | 55.1% | 54 of 98 docs have at least one topic |
| has_legal_basis | 32.7% | 32 of 98 have extracted legal basis clause |
| has_relationships | 12.2% | 12 of 98 have detected inter-doc relationships |
| avg_sections per doc | 59.4 | Range: ~20 (short regs) to 175 (Constitution) |
| Batch duration | ~23 minutes | ~14 seconds/document |
| Structured JSON files | 99 | (99 = 98 success + 1 from earlier standalone run) |
| Relational CSV rows | 132 documents.csv rows | Includes reprocessed docs |

---

## 18. Known Bugs and Limitations

### Bug 1: "غير ساري" incorrectly maps to "active"
**Location:** `scripts/run_first_100.py`, `_AR_TO_EN_STATUS` dict  
**Symptom:** Laws with `Status_AR="غير ساري"` (no longer in effect) are stored as `status_normalized="active"`.  
**Example:** The pre-1952 Jordanian Constitution (legislation-9) shows `source_status_text="غير ساري"` but `status="active"`.  
**Fix:** Add `"غير ساري": "repealed"` and `"ساري": "active"` to the `_AR_TO_EN_STATUS` dict.

### Bug 2: doc_number not extracted for constitutions
**Location:** `utils/arabic_utils.py`, `extract_doc_number_year()`  
**Symptom:** The regex `رقم N لسنة YYYY` fails on constitutions (دستور) which have no رقم. Both `doc_number` and `issue_year` may come back None from title parsing.  
**Consequence:** Slug falls back to `legislation-{id}` instead of `constitution-1952`.  
**Fix:** Add a secondary extraction pattern for `"دستور ... لسنة YYYY"` that captures only the year.

### Bug 3: Log file explosion in batch runs
**Location:** All pipeline components (`__init__` methods call `logger.add()`)  
**Symptom:** Every instantiation of LOBFetcher/ArabicTextCleaner/LegislationStructurer/LegislationExporter creates a new timestamped `.log` file. A 100-document batch generates 100+ log files.  
**Fix:** Call `logger.add()` once at batch-runner startup. Remove `logger.add()` calls from component constructors and instead expect the caller to configure logging.

### Bug 4: Date format ambiguity (MM/DD vs DD/MM)
**Location:** `utils/arabic_utils.py`, `extract_dates()`  
**Symptom:** When both day and month parts are ≤ 12, code defaults to DD/MM/YYYY convention. However, LOB appears to use MM/DD/YYYY in some data tables.  
**Example:** "04/05/2025" is interpreted as 5 April instead of 4 May.  
**Fix:** Default to MM/DD/YYYY for LOB-origin pages. Add contextual label detection (تاريخ النشر) to anchor field meaning.

### Bug 5: Topic coverage only 55.1%
**Location:** `config/topics.yaml`, `pipeline/structurer.py`  
**Symptom:** 44.9% of scraped laws have no topic assigned.  
**Root causes:**
- Constitutional and framework laws don't match specific domain keywords
- Topic taxonomy missing coverage areas (general administrative procedures, etc.)
- Keyword lists too narrow for legal language from 1940s–1970s laws
**Fix:** Expand keyword lists; add `general-administrative-law` and `framework-legislation` topics; consider lowering threshold cautiously to 0.30 for documents without any title keyword match.

### Bug 6: Relationship coverage only 12.2%
**Location:** `pipeline/structurer.py`, `_detect_relationships()`  
**Symptom:** Only 12 of 98 laws have detected inter-document relationships.  
**Root causes:**
- Cross-reference regex requires formal "القانون/النظام رقم N لسنة YYYY" which misses informal references
- The AMENDS heuristic (≤5 refs) may be too conservative — some amendment laws cite exactly the one law they amend
- Older laws don't contain explicit cross-references
**Fix:** Add more reference pattern variations; reduce AMENDS ref-count upper bound to 2 for explicit amendment laws.

### Bug 7: Entity "issuer" role incorrectly assigned
**Location:** `pipeline/structurer.py`, `_extract_entities()`  
**Symptom:** The first entity found anywhere in the document text gets `role="issuer"` regardless of context.  
**Example:** A law issued by Council of Ministers may get a different ministry as issuer if it appears first in the preamble text.  
**Fix:** Restrict issuer-role scanning to preamble section only; look for specific issuing formula patterns.

### Bug 8: topics.csv contains duplicate topic rows
**Location:** `pipeline/exporter.py`  
**Symptom:** Each document rebuild appends all its assigned topics to topics.csv. After export_all() processes 98 documents, topics.csv contains many rows per topic ID.  
**Fix:** In `_append_csv` for topics, track written topic_ids in a set and skip if already written. Or post-process: `sort -u` on topic_id column.

### Bug 9: sections.csv row count misleading
**Location:** `pipeline/exporter.py`  
**Symptom:** `wc -l sections.csv` returns 93,031 but there are approximately 5,800 actual section rows. Multi-line Arabic text in `original_text` and `normalized_text` fields contains embedded newlines that are valid inside CSV quoted strings.  
**Status:** Not a bug per se — the CSV is valid and readable by pandas and csv.reader. Just don't count rows with shell line-count tools.  
**Fix:** Use `python -c "import pandas as pd; print(len(pd.read_csv(..., sep='|')))"` to count actual rows.

### Bug 10: section_id type-abbreviation collision risk
**Location:** `utils/id_generator.py`, `section_id()`  
**Symptom:** Section type abbreviation uses first 3 chars. If a custom section type like "ann" and "annex" both truncate to "ann", they would share the same abbreviation prefix.  
**Current fixed abbreviations:** pre, prt, cha, art, par, cla, anx, ttl — no collision risk for these 8 known types. Future types should be added explicitly.  

### Limitation: Sequential fetching only
No parallelization for fetching. At 14s/document, a full corpus scrape at 1000 laws would take ~4 hours. Parallelization risks IP blocking or rate-limit violations.

### Limitation: No amendment version tracking
Every document has exactly one version (version_number=1). Laws with `status=amended` do NOT automatically fetch and store the amended version. Version history requires a separate amendment-resolution pipeline.

### Limitation: No Hijri date handling
Older laws may have publication dates in Hijri calendar. The date parser does not detect or convert Hijri dates. They will fail to parse and `publication_date` will be NULL.

### Limitation: run_mvp.py is not functional
All 5 URLs in `MVP_TARGETS` inside `scripts/run_mvp.py` are `"REPLACE_WITH_REAL_LOB_URL"` placeholders. The script will fail immediately if run. Use `run_first_100.py` instead.

---

## 19. Layer 2 Compliance Design (Future)

The data model is compliance-ready by design. Section records carry the full set of compliance fields from day one — currently populated only by basic keyword scanning. A proper Layer 2 ML pass will replace these.

### Layer 2 Tasks

1. Classify each section: is it compliance-relevant or administrative boilerplate?
2. Extract obligations: `{obligated_entity, obligation_type, obligation_text, article_ref}`
3. Extract prohibitions: `{subject_entity, prohibited_action, penalty_ref}`
4. Extract deadlines: `{entity, action, days, deadline_type}`
5. Extract approval requirements: `{requesting_entity, approving_entity, subject}`
6. Extract exceptions: `{base_rule_section_id, exception_condition, beneficiary}`
7. Extract reporting requirements: `{reporting_entity, receiving_entity, frequency, subject}`

### Recommended Tools for Layer 2

- **CAMeL Tools**: Arabic NER, morphological analysis, dependency parsing
- **AraBERT / JAIS**: Transformer-based text classification for compliance relevance
- **spaCy (Arabic model)**: Dependency parsing for obligation/prohibition sentence structure

### Future Edge Files

Six edge CSV files are already created with headers only under `exports/graph/future/`. When Layer 2 runs, it reads the structured documents and populates these files using the same `_append_graph_csv()` mechanism. No schema changes needed.

---

## 20. Graph Database Design

### Neo4j Import Command

```bash
neo4j-admin database import full \
  --nodes=Document=exports/graph/nodes_documents.csv \
  --nodes=Version=exports/graph/nodes_versions.csv \
  --nodes=Section=exports/graph/nodes_sections.csv \
  --nodes=Entity=exports/graph/nodes_entities.csv \
  --nodes=Topic=exports/graph/nodes_topics.csv \
  --relationships=exports/graph/edges_has_version.csv \
  --relationships=exports/graph/edges_has_section.csv \
  --relationships=exports/graph/edges_issued_by.csv \
  --relationships=exports/graph/edges_has_topic.csv \
  --relationships=exports/graph/edges_amends.csv \
  --relationships=exports/graph/edges_repeals.csv \
  --relationships=exports/graph/edges_based_on.csv \
  --relationships=exports/graph/edges_refers_to.csv \
  --overwrite-destination \
  legislation
```

### Useful Cypher Queries

```cypher
// All active laws in a topic
MATCH (d:Document)-[:HAS_TOPIC]->(t:Topic {topic_slug: "tax-financial-law"})
WHERE d.status = "active"
RETURN d.doc_slug, d.title_ar, d.issue_year ORDER BY d.issue_year;

// Laws that amend a given law
MATCH (d:Document)-[:AMENDS]->(target:Document {doc_slug: "law-2018-34"})
RETURN d.doc_slug, d.title_ar;

// Full repeal chain
MATCH path=(d:Document)-[:REPEALS*]->(oldest:Document)
WHERE NOT (oldest)-[:REPEALS]->()
RETURN path;

// All laws based on the Constitution
MATCH (d:Document)-[:BASED_ON]->(c:Document {doc_slug: "constitution-hashemite-kingdom-1952"})
RETURN d.doc_slug, d.doc_type, d.issue_year ORDER BY d.issue_year;

// Laws issued by a specific ministry
MATCH (d:Document)-[:ISSUED_BY]->(e:Entity {entity_name_ar: "وزارة المالية"})
RETURN d.doc_slug, d.title_ar, d.issue_year;

// Section hierarchy for a given law
MATCH (d:Document {doc_slug: "law-2018-34"})-[:HAS_VERSION]->(v:Version)-[:HAS_SECTION]->(s:Section)
RETURN s.section_type, s.section_number, s.section_label, s.display_order
ORDER BY s.display_order;

// Disconnected laws (no relationship edges at all)
MATCH (d:Document)
WHERE NOT (d)-[:AMENDS|REPEALS|BASED_ON|REFERS_TO]->()
AND NOT ()<-[:AMENDS|REPEALS|BASED_ON|REFERS_TO]-(d)
RETURN d.doc_slug ORDER BY d.issue_year;
```

---

*End of Architecture Document v3.0*
