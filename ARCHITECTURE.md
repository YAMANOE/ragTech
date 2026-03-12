# Jordanian RegTech – Legislative Intelligence Pipeline
## Architecture & Design Document

**Version:** 1.0  
**Scope:** Layer 1 — Legislative Intelligence (compliance-ready design)  
**Primary Source:** Jordanian Bureau of Legislation and Opinion (LOB) — lob.gov.jo  
**Language:** Arabic (Jordan)

---

## 1. Architecture Overview

### Pipeline Layers

```
┌─────────────────────────────────────────────────────────────┐
│                        SOURCE                               │
│   LOB Website (lob.gov.jo) — Dynamic HTML, Arabic text      │
└────────────────────────┬────────────────────────────────────┘
                         │ Playwright fetch
┌────────────────────────▼────────────────────────────────────┐
│                  LAYER 1: RAW DATA                          │
│  raw HTML files │ raw text files │ source registry (CSV)    │
└────────────────────────┬────────────────────────────────────┘
                         │ BeautifulSoup / regex parse + clean
┌────────────────────────▼────────────────────────────────────┐
│                  LAYER 2: CLEAN DATA                        │
│  original text preserved │ normalized text │ cleaning log   │
└────────────────────────┬────────────────────────────────────┘
                         │ Structurer / metadata + section extractor
┌────────────────────────▼────────────────────────────────────┐
│               LAYER 3: STRUCTURED LEGAL DATA                │
│  documents.json │ versions.json │ sections.json             │
│  entities.json  │ topics.json   │ relationships.json        │
└────────────────────────┬────────────────────────────────────┘
                         │ Exporter
┌────────────────────────▼────────────────────────────────────┐
│                  LAYER 4: EXPORT                            │
│  Relational CSVs      │  Graph CSVs (nodes + edges)         │
│  (DB-ready)           │  (Neo4j / NetworkX ready)           │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow Summary
1. **Fetcher** → Playwright renders LOB page, saves HTML + text + registry entry
2. **Parser** → BeautifulSoup extracts metadata block + article sections from raw HTML
3. **Cleaner** → Arabic text normalization preserving original + normalized side-by-side
4. **Structurer** → Assembles full document objects: metadata, versions, section hierarchy, entities, topics, relationships
5. **Exporter** → Produces relational CSVs + graph node/edge CSVs
6. **Validator** → Checks all mandatory fields, section ordering, cross-reference integrity

---

## 2. Recommended Tools & Why

| Tool | Purpose | Why |
|------|---------|-----|
| **Playwright** | Dynamic page fetching | LOB pages are JavaScript-rendered, requests/urllib cannot see content |
| **BeautifulSoup4 + lxml** | HTML parsing | Fast, robust, handles malformed HTML common in Arabic government sites |
| **regex** | Arabic pattern matching | Extended Unicode support, needed for Arabic numerals and diacritics |
| **pyarabic** | Arabic text normalization | Alef normalization, diacritic removal, tatweel removal — battle-tested |
| **python-slugify** | Stable slug generation | URL-safe, consistent ID base for doc_slug |
| **pandas** | CSV export + data manipulation | Clean tabular output, easy filtering |
| **PyYAML** | Topic taxonomy config | Human-editable topic definitions |
| **loguru** | Structured logging | Async-friendly, rotation, structured output |
| **python-dotenv** | Environment config | Keep URLs/credentials out of source |
| **uuid** | Stable UUIDs | Deterministic UUID5 for stable IDs across re-runs |

**Intentionally excluded (for MVP):**
- No ML/NLP models (camel-tools is too heavy for MVP — add later for entity NER)
- No database driver (data is exported to CSV/JSON; DB import is a separate step)
- No async orchestration framework (asyncio is sufficient)

---

## 3. Folder Structure

```
new-project/
├── ARCHITECTURE.md        ← This document
├── README.md
├── requirements.txt
├── .gitignore
├── .env.example
│
├── config/
│   ├── __init__.py
│   ├── settings.py        ← Paths, pipeline config, LOB URLs
│   └── topics.yaml        ← Topic taxonomy for Jordanian legislation
│
├── models/
│   ├── __init__.py
│   └── schema.py          ← All dataclasses: Document, Version, Section, Entity, Topic, Relationship
│
├── utils/
│   ├── __init__.py
│   ├── arabic_utils.py    ← Arabic text normalization + pattern matching
│   └── id_generator.py    ← Deterministic ID/slug generation
│
├── pipeline/
│   ├── __init__.py
│   ├── fetcher.py         ← Playwright LOB page fetcher
│   ├── parser.py          ← Raw HTML → parsed metadata + sections
│   ├── cleaner.py         ← Arabic text cleaning pipeline
│   ├── structurer.py      ← Assembled structured document objects
│   ├── exporter.py        ← CSV export (relational + graph)
│   └── validator.py       ← QA and validation rules
│
├── scripts/
│   ├── run_pipeline.py    ← Full pipeline run script
│   └── run_mvp.py         ← MVP: run 5 target laws
│
└── data/
    ├── raw/
    │   ├── html/           ← Raw HTML per document per fetch
    │   ├── text/           ← Raw extracted text per document per fetch
    │   └── source_registry.csv  ← Master fetch log
    ├── clean/
    │   ├── {doc_slug}_clean.json  ← Per-document clean output
    │   └── cleaning_log.csv       ← Master cleaning log
    └── structured/
        ├── docs/           ← Per-document structured JSON
        ├── documents.json
        ├── versions.json
        ├── sections.json
        ├── entities.json
        ├── topics.json
        └── relationships.json

exports/
├── relational/
│   ├── documents.csv
│   ├── versions.csv
│   ├── sections.csv
│   ├── entities.csv
│   ├── topics.csv
│   ├── document_topics.csv
│   ├── document_entities.csv
│   ├── document_relationships.csv
│   └── section_compliance_flags.csv
└── graph/
    ├── nodes_documents.csv
    ├── nodes_versions.csv
    ├── nodes_sections.csv
    ├── nodes_entities.csv
    ├── nodes_topics.csv
    ├── edges_has_version.csv
    ├── edges_has_section.csv
    ├── edges_issued_by.csv
    ├── edges_applies_to.csv
    ├── edges_refers_to.csv
    ├── edges_based_on.csv
    ├── edges_amends.csv
    ├── edges_repeals.csv
    ├── edges_implements.csv
    ├── edges_supplements.csv
    ├── edges_supersedes.csv
    ├── edges_has_topic.csv
    └── future/
        ├── edges_future_has_obligation.csv    ← Empty, headers only
        ├── edges_future_has_prohibition.csv
        ├── edges_future_has_exception.csv
        ├── edges_future_requires_approval.csv
        ├── edges_future_has_deadline.csv
        └── edges_future_requires_reporting.csv
```

---

## 4. File Formats Per Layer

| Layer | Format | Reason |
|-------|--------|--------|
| Raw HTML | `.html` UTF-8 | Preserve full original page for reprocessing |
| Raw text | `.txt` UTF-8 | Quick access without re-parsing HTML |
| Source registry | `.csv` | Simple append-log, easy to import anywhere |
| Clean data | `.json` | Preserve original + normalized with cleaning log inline |
| Cleaning log | `.csv` | Master log of all cleaning operations |
| Structured per-doc | `.json` | Full document object, self-contained |
| Structured master | `.json` | Aggregated list for batch processing |
| Relational export | `.csv` | Direct import into PostgreSQL/SQLite/Supabase |
| Graph export | `.csv` | Neo4j `neo4j-admin import` format |

---

## 5. Exact Data Model

### 5.1 documents.csv (and Document dataclass)

| Field | Type | Description | Mandatory |
|-------|------|-------------|-----------|
| doc_id | UUID | Deterministic UUID5 from doc_slug | ✓ |
| doc_slug | string | Stable human-readable ID: `{type}-{year}-{number}` | ✓ |
| title_ar | string | Full Arabic title from source | ✓ |
| title_en | string | English title (manual or translated) | |
| doc_type | string | law, regulation, instruction, decision, circular, agreement, royal_decree, royal_order | ✓ |
| doc_number | string | Official number as printed | |
| issue_year | integer | Hijri or Gregorian year (see note) | ✓ |
| issuing_entity_id | UUID | FK to entities | |
| issuing_entity_name_ar | string | Denormalized for convenience | |
| official_gazette_number | string | الجريدة الرسمية number | |
| publication_date | date | Date published in gazette (ISO 8601) | |
| effective_date | date | Date the law came into force | |
| repeal_date | date | Date explicitly repealed (null if active) | |
| status | string | active, amended, repealed, draft | ✓ |
| legal_basis_text | string | Full text of the استناداً/بناءً clause | |
| source_url | string | LOB page URL | ✓ |
| fetch_date | datetime | ISO 8601 UTC | ✓ |
| raw_html_path | string | Relative path to raw HTML file | ✓ |
| raw_text_path | string | Relative path to raw text file | ✓ |
| applicability_scope | string | general, government_wide, sector_specific, entity_specific, internal | |
| applicability_sectors | string | Comma-separated sector names | |
| applicability_entities | string | Comma-separated entity names | |
| notes | string | Manual override notes | |

**Assumption:** Years are Gregorian in most modern LOB documents. Hijri dates when present are secondary and stored in `notes`.

### 5.2 versions.csv

| Field | Type | Description | Mandatory |
|-------|------|-------------|-----------|
| version_id | UUID | Deterministic UUID5 | ✓ |
| doc_id | UUID | FK to documents | ✓ |
| doc_slug | string | Denormalized | ✓ |
| version_number | integer | 1 = original, 2 = first amendment, etc. | ✓ |
| version_type | string | original, amendment, consolidated | ✓ |
| effective_from | date | When this version became active | |
| effective_to | date | When superseded (null if current) | |
| is_current | boolean | True for the latest effective version | ✓ |
| amendment_doc_id | UUID | FK to the document that caused this version | |
| amendment_doc_slug | string | Denormalized | |
| full_text_original | text | Full Arabic text as extracted | ✓ |
| full_text_normalized | text | Normalized for search | ✓ |
| source_url | string | URL this version was fetched from | ✓ |
| version_notes | string | | |

### 5.3 sections.csv

| Field | Type | Description | Mandatory |
|-------|------|-------------|-----------|
| section_id | UUID | Deterministic UUID5 | ✓ |
| version_id | UUID | FK to versions | ✓ |
| doc_id | UUID | FK to documents | ✓ |
| doc_slug | string | Denormalized | ✓ |
| section_type | string | preamble, part, chapter, article, paragraph, clause, annex | ✓ |
| section_number | string | "1", "أ", "ثانياً" — as printed | |
| section_label | string | Full label: "المادة (3)", "الباب الثاني" | |
| parent_section_id | UUID | FK to parent section (null for top-level) | |
| display_order | integer | Global ordering within version | ✓ |
| original_text | text | Text exactly as extracted | ✓ |
| normalized_text | text | Cleaned for search/parsing | ✓ |
| word_count | integer | Word count of original_text | |
| compliance_relevant | boolean | Manual or pattern flag (Layer 2 prep) | |
| contains_obligation | boolean | NULL until Layer 2 populated | |
| contains_prohibition | boolean | NULL until Layer 2 populated | |
| contains_approval_requirement | boolean | NULL until Layer 2 populated | |
| contains_deadline | boolean | NULL until Layer 2 populated | |
| contains_exception | boolean | NULL until Layer 2 populated | |
| contains_reporting_requirement | boolean | NULL until Layer 2 populated | |
| applicability_targets | string | JSON array of target entity/sector names | |
| legal_rules_json | string | JSON: future rule extraction placeholder | |
| evidence_hints_json | string | JSON: future compliance evidence hints | |

### 5.4 entities.csv

| Field | Type | Description |
|-------|------|-------------|
| entity_id | UUID | |
| entity_slug | string | |
| entity_name_ar | string | ✓ |
| entity_name_en | string | |
| entity_type | string | ministry, authority, council, court, department, company, person |
| parent_entity_id | UUID | For organizational hierarchy |
| notes | string | |

### 5.5 topics.csv

| Field | Type | Description |
|-------|------|-------------|
| topic_id | UUID | |
| topic_slug | string | |
| topic_name_ar | string | ✓ |
| topic_name_en | string | |
| parent_topic_id | UUID | For topic hierarchy |
| topic_level | integer | 1 = primary, 2 = secondary |
| description | string | |

### 5.6 document_topics.csv (junction)

| Field | Type |
|-------|------|
| doc_id | UUID |
| topic_id | UUID |
| is_primary | boolean |
| confidence | float |
| extraction_method | string (keyword, manual, model) |

### 5.7 document_relationships.csv

| Field | Type | Description |
|-------|------|-------------|
| rel_id | UUID | |
| source_doc_id | UUID | ✓ |
| target_doc_id | UUID | ✓ |
| rel_type | string | AMENDS, REPEALS, IMPLEMENTS, BASED_ON, REFERS_TO, SUPPLEMENTS, SUPERSEDES ✓ |
| source_article_ref | string | e.g., "Article 5" |
| target_article_ref | string | |
| extracted_text | string | The raw clause that triggered extraction |
| confidence | float | 0.0–1.0 |
| extraction_method | string | rule_based, manual |
| notes | string | |

### 5.8 Graph Node Schemas

**nodes_documents.csv:**
```
:ID(doc), doc_slug, title_ar, doc_type, issue_year, status, source_url, :LABEL
```

**nodes_versions.csv:**
```
:ID(version), version_id, doc_slug, version_number, version_type, is_current, effective_from, effective_to, :LABEL
```

**nodes_sections.csv:**
```
:ID(section), section_id, doc_slug, section_type, section_number, section_label, display_order, word_count, :LABEL
```

**nodes_entities.csv:**
```
:ID(entity), entity_id, entity_slug, entity_name_ar, entity_type, :LABEL
```

**nodes_topics.csv:**
```
:ID(topic), topic_id, topic_slug, topic_name_ar, topic_level, :LABEL
```

### 5.9 Graph Edge Schemas

All edge files follow:
```
:START_ID, :END_ID, :TYPE, [optional properties]
```

| Edge File | :TYPE | Properties |
|-----------|-------|------------|
| edges_has_version.csv | HAS_VERSION | is_current |
| edges_has_section.csv | HAS_SECTION | display_order, section_type |
| edges_issued_by.csv | ISSUED_BY | |
| edges_applies_to.csv | APPLIES_TO | scope |
| edges_refers_to.csv | REFERS_TO | article_ref, confidence |
| edges_based_on.csv | BASED_ON | extracted_text |
| edges_amends.csv | AMENDS | effective_from |
| edges_repeals.csv | REPEALS | effective_from |
| edges_implements.csv | IMPLEMENTS | |
| edges_supplements.csv | SUPPLEMENTS | |
| edges_supersedes.csv | SUPERSEDES | |
| edges_has_topic.csv | HAS_TOPIC | is_primary, confidence |

**Future-ready edges (headers only):**

| Edge File | :TYPE | Properties |
|-----------|-------|------------|
| edges_future_has_obligation.csv | HAS_OBLIGATION | obligation_text, target_entity |
| edges_future_has_prohibition.csv | HAS_PROHIBITION | prohibition_text |
| edges_future_has_exception.csv | HAS_EXCEPTION | exception_condition |
| edges_future_requires_approval.csv | REQUIRES_APPROVAL | approving_entity |
| edges_future_has_deadline.csv | HAS_DEADLINE | deadline_text, days |
| edges_future_requires_reporting.csv | REQUIRES_REPORTING | reporting_entity, frequency |

---

## 6. Naming Conventions

### doc_slug
Pattern: `{doc_type_en}-{year}-{number}`
Examples:
- `law-2018-46` → قانون رقم (46) لسنة 2018
- `regulation-2021-3` → نظام رقم (3) لسنة 2021
- `decision-2022-101` → قرار رقم (101) لسنة 2022
- `instruction-2019-income-tax` → تعليمات ... لسنة 2019 (no number, use title slug)

Doc type short codes:
```
دستور         → constitution
قانون         → law
نظام          → regulation
تعليمات       → instruction
قرار          → decision
منشور/تعميم   → circular
اتفاقية       → agreement
مرسوم ملكي   → royal_decree
أمر ملكي     → royal_order
إرادة ملكية  → royal_will
```

### version_id
Pattern: `{doc_slug}-v{version_number}`
Example: `law-2018-46-v1`, `law-2018-46-v2`

### section_id
Pattern: `{version_id}-{section_type[:3]}-{display_order:04d}`
Example: `law-2018-46-v1-art-0003`

### entity_id
Pattern: `entity-{entity_slug}`
Example: `entity-ministry-finance`

### topic_id
Pattern: `topic-{topic_slug}`
Example: `topic-tax-law`

### File naming for raw data
- HTML: `{doc_slug}_{YYYYMMDD_HHMMSS}.html`
- Text: `{doc_slug}_{YYYYMMDD_HHMMSS}.txt`
- Clean: `{doc_slug}_clean.json` (latest clean, overwrites on re-run)
- Structured: `data/structured/docs/{doc_slug}.json`

---

## 7. Extraction Strategy from LOB Pages

### 7.1 LOB URL Patterns (Assumption — verify with dev tools)
```
Base:          https://lob.gov.jo/
Search:        https://lob.gov.jo/AR/LobContent.aspx
Law page:      https://lob.gov.jo/AR/{LegislationContent}.aspx?id={numeric_id}
```

**Important:** LOB pages are rendered dynamically. The content is loaded via JavaScript after page load. Playwright must wait for content through `networkidle` + selector presence before extracting HTML.

### 7.2 Recommended Fetch Strategy
```python
# Wait conditions (tune after inspecting actual DOM with Playwright)
await page.goto(url, wait_until="networkidle", timeout=30000)
await page.wait_for_selector("div.legislation-content, .law-body, article", timeout=15000)
```

### 7.3 Content Areas to Target
| Content Area | CSS / XPath Hint | Content |
|---|---|---|
| Title block | `.law-title`, `h1`, `.legislation-header` | Full Arabic title |
| Metadata block | `.law-meta`, `.legislation-info`, `.sidebar` | Number, year, gazette, dates |
| Legal text body | `.law-body`, `.legislation-text`, `article`, `#content` | Full Arabic text |
| Attachments | `a[href$=".pdf"]`, `.attachments` | Linked PDFs |

**Critical:** After inspecting the first page with Playwright's screenshot capability, update these selectors in `config/settings.py`.

### 7.4 Pagination
Some long laws are paginated. Detect by:
- Presence of `next page` / `الصفحة التالية` button
- URL parameter `page=`
- Collect all pages before parsing

### 7.5 Rate Limiting
- Add 2–3 second delay between requests (`asyncio.sleep(2)`)
- Set a realistic User-Agent string
- Do not run concurrent fetches against the same domain

---

## 8. Arabic Text Cleaning Rules

**Golden rule:** ALWAYS preserve `original_text` before normalization. `normalized_text` is for search/parsing only.

### Cleaning Rules (Applied in Order)

| # | Rule | What It Does | What It Preserves |
|---|------|-------------|-------------------|
| 1 | Strip HTML tags | Remove `<p>`, `<span>`, `<br>` etc. | All text content |
| 2 | Decode HTML entities | `&amp;` → `&`, `&nbsp;` → ` ` | Symbols |
| 3 | Remove zero-width chars | `\u200b`, `\u200c`, `\u200d`, `\uFEFF` | Everything else |
| 4 | Normalize tatweel | Remove `ـ` (kashida stretchers) | Letter identity |
| 5 | Remove harakat | Remove diacritics `ً ٌ ٍ َ ُ ِ ّ ْ ` | Base letters |
| 6 | Normalize Alef forms | `أإآ` → `ا` (search copy only) | Original script |
| 7 | Normalize Lam-Alef | `لأ لإ لآ` → `لا` (search copy) | Legal meaning |
| 8 | Normalize Tamarbouta | `ة` → `ه` (search copy only) | Legal meaning |
| 9 | Normalize spaces | Collapse multiple spaces/newlines | Paragraph breaks |
| 10 | Remove page markers | "Page X of Y" artifacts | — |
| 11 | Deduplicate blank lines | `\n\n\n+` → `\n\n` | Article separation |

**Rules 6–8 apply ONLY to `normalized_text`, NEVER to `original_text`.**

### What MUST NOT Be Changed
- Article numbers (المادة 1, المادة (أ))
- Legal references ("استناداً لأحكام المادة 6 من قانون ...")
- Arabic ordinal text (الأولى، الثانية، ثالثاً)
- Paragraph markers (أ. ب. ج.)
- Dates and year references
- Document numbers

---

## 9. Parsing Strategy

### 9.1 Metadata Extraction (from title block)

**Pattern battery for Jordanian legislation:**

```python
# Document number and year
DOC_NUMBER_YEAR  = r'رقم\s*[\(\（]?\s*(\d+|[\u0660-\u0669]+)\s*[\)\）]?\s*لسنة\s*([\d\u0660-\u0669]{4})'

# Document type (first word of title)
DOC_TYPE_MAP = {
    'قانون': 'law',
    'نظام': 'regulation',
    'تعليمات': 'instruction',
    'قرار': 'decision',
    'دستور': 'constitution',
    'اتفاقية': 'agreement',
    'معاهدة': 'treaty',
    'مرسوم': 'royal_decree',
    'أمر': 'royal_order',
    'منشور': 'circular',
    'تعميم': 'circular',
}

# Gazette reference
GAZETTE_REF = r'الجريدة الرسمية\s*(?:رقم)?\s*[\(\（]?\s*(\d+)\s*[\)\）]?'

# Effective date markers
EFFECTIVE_DATE = r'(?:تاريخ|بتاريخ|اعتباراً من)\s+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})'
```

### 9.2 Legal Basis Extraction

Target clauses that reference the authority behind this legislation:
```python
LEGAL_BASIS_PATTERNS = [
    r'استناداً?\s+لأحكام\s+(.{20,200})(?=\n|،|\.)',
    r'بناءً?\s+على\s+أحكام\s+(.{20,200})(?=\n|،|\.)',
    r'عملاً?\s+بأحكام\s+(.{20,200})(?=\n|،|\.)',
    r'تطبيقاً?\s+لأحكام\s+(.{20,200})(?=\n|،|\.)',
    r'وفقاً?\s+لأحكام\s+(.{20,200})(?=\n|،|\.)',
    r'بمقتضى\s+أحكام\s+(.{20,200})(?=\n|،|\.)',
    r'استناداً?\s+إلى\s+(.{20,200})(?=\n|،|\.)',
]
```

### 9.3 Article / Section Hierarchy

**Section detection (process text line by line):**

```
Level 1 Patterns → Part (الجزء / الباب):
  الباب\s*(الأول|الثاني|الثالث|...|الأخير|\d+)
  الجزء\s*(الأول|...)

Level 2 Patterns → Chapter (الفصل / القسم):
  الفصل\s*(الأول|...)
  القسم\s*(الأول|...)

Level 3 Patterns → Article (المادة):
  المادة\s*[\(\（]?\s*(\d+|ordinal_text)\s*[\)\）]?

Level 4 Patterns → Paragraph (inside article):
  ^([أبجدهوزحطيكلمنسعفصقرشت])[).\-]\s+
  ^(\d+)[).\-]\s+
  ^(أولاً|ثانياً|ثالثاً|رابعاً|خامساً|...)\s*[:]\s+

Level 5 Patterns → Clause (inside paragraph):
  ^([١-٩\d])\s*[).\-]\s+  (sub-numbering)
  ^[-–]\s+                  (dashes)
```

### 9.4 Preamble Extraction
- Text before the first article = preamble/introductory clause
- Usually contains: legal basis, issuing authority, recitals
- Store as `section_type = 'preamble'`

### 9.5 Annex / Attachment Detection
- Look for: `ملحق`, `جدول`, `نموذج` headers after the last article
- Look for linked PDF files on the page
- Store as `section_type = 'annex'` with attachment URL if linked

---

## 10. Topic Classification Strategy

### Approach (MVP: keyword-based, future: model-based)

1. Load taxonomy from `config/topics.yaml`
2. Each topic has a list of Arabic keyword patterns
3. Match against combined `title_ar + full text normalized`
4. Return all matched topics with confidence score
5. First highest-confidence match = primary topic
6. All others = secondary topics

### Jordanian Legislation Topic Taxonomy

Primary categories (Level 1):
```
القانون الدستوري والإداري | Constitutional & Administrative Law
القانون المدني            | Civil Law
القانون التجاري           | Commercial Law
القانون الجنائي           | Criminal Law
قانون العمل والتأمينات   | Labor & Social Insurance Law
القانون الضريبي والمالي   | Tax & Financial Law
قانون الاستثمار           | Investment Law
قانون البيئة              | Environmental Law
قانون الصحة              | Health Law
قانون التعليم             | Education Law
الأحوال الشخصية          | Personal Status Law
قانون الأراضي والتخطيط   | Land & Planning Law
قانون تكنولوجيا المعلومات | Information Technology Law
قانون الطاقة              | Energy Law
قانون النقل والمواصلات   | Transport Law
الأمن العام والدفاع       | Public Security & Defense
```

### Confidence Scoring
- Direct keyword in title → 0.9
- Keyword in preamble → 0.7
- Keyword in body text (multiple occurrences) → 0.6
- Keyword in body (single) → 0.4
- Threshold for inclusion: 0.4

---

## 11. Scope / Applicability Classification

| Scope | Definition | Detection |
|-------|------------|-----------|
| `general` | Applies to all persons/entities in Jordan | No specific ministry/sector named in scope clause |
| `government_wide` | Applies to all government entities | Keywords: جميع الجهات الحكومية، الوزارات والدوائر |
| `sector_specific` | Applies to a specific economic/legal sector | Named sector in title/scope (الطاقة، الصحة، التعليم) |
| `entity_specific` | Applies to one named institution | Named ministry/authority in scope |
| `internal` | Internal government procedure (instructions/circulars) | تعليمات داخلية, منشور داخلي |

**Detection patterns:**
```python
SCOPE_GENERAL     = r'يسري على|يطبق على|ينطبق على'
SCOPE_ALL_GOV     = r'جميع الجهات الحكومية|الوزارات والدوائر|الجهات الرسمية'
SCOPE_SECTOR      = r'قطاع\s+(الطاقة|الصحة|التعليم|المصارف|التأمين|...)'
```

---

## 12. Entity Extraction Strategy

### Extraction Steps
1. **Pattern-based extraction** against normalized text
2. **Against known entity dictionary** (built incrementally)
3. **Context disambiguation** (issuing entity vs. target entity)

### Key Patterns (Arabic)
```python
MINISTRY_PATTERN   = r'وزارة\s+[\u0600-\u06FF\s]{3,40}'
AUTHORITY_PATTERN  = r'(?:هيئة|سلطة)\s+[\u0600-\u06FF\s]{3,40}'
COUNCIL_PATTERN    = r'مجلس\s+[\u0600-\u06FF\s]{3,40}'
COURT_PATTERN      = r'محكمة\s+[\u0600-\u06FF\s]{3,40}'
DEPARTMENT_PATTERN = r'(?:دائرة|مديرية)\s+[\u0600-\u06FF\s]{3,40}'
COMPANY_PATTERN    = r'شركة\s+[\u0600-\u06FF\s]{3,40}'
```

### Entity Roles
- `ISSUED_BY`: Entity that issued the document (from metadata block)
- `APPLIES_TO`: Entity named in scope/applicability clause
- `REFERS_TO`: Entity mentioned in article body (informational reference)

---

## 13. Legal Relationship Extraction

### Relationship Detection Patterns

| Relationship | Arabic Trigger Phrases | Confidence |
|---|---|---|
| AMENDS | يُعدَّل، التعديل، بتعديل، معدَّل | 0.9 |
| REPEALS | يُلغى، تُلغى، إلغاء، ملغى | 0.9 |
| BASED_ON | استناداً، بناءً على، عملاً بأحكام | 0.85 |
| IMPLEMENTS | تطبيقاً، تنفيذاً، تنفيذ أحكام | 0.8 |
| SUPPLEMENTS | يُضاف، إضافة إلى، يُكمّل | 0.75 |
| SUPERSEDES | يحلّ محلّ، يستعيض عن | 0.85 |
| REFERS_TO | بمعنى هذا القانون، المشار إليه | 0.6 |

### Target Document Identification
After detecting relationship type, extract the referenced document:
```python
DOC_REF_PATTERN = r'(?:القانون|النظام|قرار)\s+رقم\s*[\(\（]?\s*(\d+)\s*[\)\）]?\s*لسنة\s*([\d]{4})'
```
Then look up target doc in the registry. If not found → create a pending cross-reference record.

---

## 14. Versioning Strategy

### Core Design
- A `Document` represents a law's identity regardless of amendments
- Each `Version` represents the text at a specific point in time
- `is_current = True` on the latest effective version
- When an amending law is processed, a new version record is created for the amended law

### Version Creation Flow
1. Fetch original law → create `version_number=1`, `version_type='original'`, `is_current=True`
2. Detect amending law referencing this law → create `version_number=2`, `version_type='amendment'`, `is_current=True`; set old version `effective_to = amendment effective_date`
3. If amended text is consolidated on LOB → mark `version_type='consolidated'`

### Default Behavior
- System ALWAYS returns `is_current=True` version unless history is explicitly requested
- Retrieval query example: `SELECT * FROM versions WHERE doc_id=X AND is_current=1`

---

## 15. Compliance-Readiness Strategy (Layer 2 Preparation)

All section records include these NULL-initialized fields that Layer 2 will populate:
```
compliance_relevant          BOOLEAN (NULL now, populated by Layer 2 keyword scan)
contains_obligation          BOOLEAN
contains_prohibition         BOOLEAN
contains_approval_requirement BOOLEAN
contains_deadline            BOOLEAN
contains_exception           BOOLEAN
contains_reporting_requirement BOOLEAN
applicability_targets        JSON array
legal_rules_json             JSON (future: structured rule extraction)
evidence_hints_json          JSON (future: compliance evidence pointers)
```

### Layer 2 Trigger Markers (for reference, not implemented now)
```
Obligations:          يجب، يلزم، يتعين، يتوجب، على [Entity] أن
Prohibitions:         يُحظر، يُمنع، لا يجوز، لا يُسمح
Approvals:            بعد الحصول على موافقة، يستلزم الحصول على
Deadlines:            خلال مدة، في غضون، يجب أن يتم خلال، موعد لا يتجاوز
Exceptions:           مع مراعاة، استثناءً من، باستثناء، على الرغم من
Reporting:            تقديم تقرير، الإفصاح، الإبلاغ عن، يرفع تقريراً
```

---

## 16. Export Design

### 16.1 Relational CSV Package

Designed for direct import into PostgreSQL / SQLite / Supabase:

```sql
-- Recommended import order (FK dependencies)
1. entities.csv
2. topics.csv
3. documents.csv
4. versions.csv
5. sections.csv
6. document_topics.csv
7. document_entities.csv
8. document_relationships.csv
9. section_compliance_flags.csv (Layer 2)
```

All CSVs use:
- UTF-8 encoding
- `|` pipe delimiter (avoids conflicts with Arabic commas)
- ISO 8601 dates
- Boolean as `true/false`
- NULL as empty string

### 16.2 Graph CSV Package (Neo4j Format)

Import command:
```bash
neo4j-admin database import full \
  --nodes=Document=exports/graph/nodes_documents.csv \
  --nodes=Version=exports/graph/nodes_versions.csv \
  --nodes=Section=exports/graph/nodes_sections.csv \
  --nodes=Entity=exports/graph/nodes_entities.csv \
  --nodes=Topic=exports/graph/nodes_topics.csv \
  --relationships=exports/graph/edges_*.csv
```

---

## 17. QA and Validation Rules

| Check | Level | Rule |
|-------|-------|------|
| Required fields | Document | doc_slug, title_ar, doc_type, status, source_url must not be empty |
| Arabic text presence | Document | title_ar must contain at least one Arabic character |
| Version coverage | Document | Every document must have at least 1 version with is_current=True |
| Section ordering | Version | display_order must be unique and sequential within a version |
| Orphan sections | Section | parent_section_id must reference an existing section_id or be NULL |
| Duplicate docs | Document | doc_slug must be unique in documents.json |
| Date ordering | Version | effective_to must be > effective_from if both present |
| Relationship integrity | Relationship | source_doc_id and target_doc_id must exist in documents |
| File paths | Raw | html_file_path and text_file_path must point to existing files |
| Encoding | All text | All text must be valid UTF-8 |
| Empty sections | Section | original_text must not be empty or only whitespace |

---

## 18. Common Edge Cases

| Edge Case | Detection | Handling Strategy |
|-----------|-----------|-------------------|
| Law with no article numbers | No `المادة` pattern found | Store as single `preamble` section, flag `needs_review: true` |
| Tabular content in articles | `<table>` tags in HTML | Preserve as tab-separated text in original, note in cleaning log |
| Mixed Arabic/English text | Contains Latin characters | Keep mixed; normalize only Arabic portions |
| Amended article (partial) | "تُعدَّل المادة X لتصبح..." | Extract the amendment clause; create new section record with amendment reference |
| Multiple gazette publications | Two gazette numbers found | Store both; log ambiguity |
| Hijri dates | Arabic month names or H suffix | Convert to Gregorian estimate; store both in notes |
| Long pagination | Multiple pages on LOB | Collect all pages before parsing; concatenate with `\n\n` |
| Annex-only PDF | Button links to PDF, no HTML text | Store PDF URL; mark `has_attachment: true`; defer text extraction |
| Legislation with same number/year | Title disambiguates | Append abbreviated title to slug: `law-2018-X-income-tax` |
| Unresolved cross-references | Referenced doc not yet in registry | Create stub record with status `pending` in relationships |
| Instructions with no number | تعليمات with only year | Slug uses title slug: `instruction-2019-{title_slug}` |
| Repealed by unknown law | "ملغى" found but no reference | Set status='repealed'; leave repeal_date and amendment_doc_id NULL |

---

## 19. MVP Plan (5 Target Laws)

Start with well-structured, commonly referenced Jordanian laws:

| # | Document | Type | LOB ID (verify) | Expected Complexity |
|---|----------|------|-----------------|---------------------|
| 1 | قانون ضريبة الدخل | قانون | Verify on LOB | Medium — many articles, clear structure |
| 2 | قانون الشركات | قانون | Verify on LOB | High — many chapters, amendments |
| 3 | نظام الخدمة المدنية | نظام | Verify on LOB | Medium — government-wide regulation |
| 4 | قانون العمل | قانون | Verify on LOB | High — referenced frequently |
| 5 | تعليمات تقديم الخدمات الإلكترونية | تعليمات | Verify on LOB | Low — shorter, simpler structure |

**MVP expected outputs per law:**
- 1 source_registry row
- 1 clean JSON file
- 1 structured doc JSON
- 1 version record
- 20–150 section records
- 2–5 topic assignments
- 1–3 entity records
- 0–5 relationship records

---

## 20. Implementation Order

### Phase 1 — Foundation (Week 1)
1. `config/settings.py` — set all paths, configure LOB base URL
2. `utils/arabic_utils.py` — Arabic normalization functions
3. `utils/id_generator.py` — stable slug + UUID generation
4. `models/schema.py` — all dataclasses

### Phase 2 — Fetch & Raw (Week 1–2)
5. `pipeline/fetcher.py` — Playwright fetch + raw storage + registry
6. Manual test: fetch 1 LOB law page, inspect saved HTML
7. Update `config/settings.py` with correct CSS selectors

### Phase 3 — Parse & Clean (Week 2)
8. `pipeline/parser.py` — HTML → metadata + raw sections
9. `pipeline/cleaner.py` — clean pipeline + cleaning log
10. Test on 1 law, verify section boundary detection

### Phase 4 — Structure (Week 3)
11. `pipeline/structurer.py` — full document assembly
12. Topic classification
13. Entity extraction
14. Relationship extraction

### Phase 5 — Export & Validate (Week 3–4)
15. `pipeline/exporter.py` — relational + graph CSVs
16. `pipeline/validator.py` — QA checks
17. `scripts/run_pipeline.py` — full pipeline script
18. `scripts/run_mvp.py` — MVP batch run

---

## Sample Example: RAW → CLEAN → STRUCTURED → EXPORT

### Input — Hypothetical LOB Law Page: قانون ضريبة الدخل رقم (34) لسنة 2014

**RAW (data/raw/text/law-2014-34_20260311_120000.txt):**
```
قانون ضريبة الدخل رقم (34) لسنة 2014
نُشر في الجريدة الرسمية عدد (5277) بتاريخ 16/10/2014
استناداً لأحكام المادة (93) من الدستور، وبناءً على ما قرره مجلس الأمة، يسن الملك القانون الآتي:
الفصل الأول – أحكام عامة
المادة (1)
يُسمى هذا القانون (قانون ضريبة الدخل  لسنة 2014) ويعمل به من تاريخ نشره في الجريدة الرسمية.
المادة (2)
يكون للكلمات والعبارات التالية حيثما وردت في هذا القانون المعاني المخصصة لها أدناه...
```

**CLEAN (data/clean/law-2014-34_clean.json):**
```json
{
  "doc_slug": "law-2014-34",
  "cleaned_at": "2026-03-11T12:01:00Z",
  "original_text": "قانون ضريبة الدخل رقم (34) لسنة 2014\n...",
  "normalized_text": "قانون ضريبه الدخل رقم (34) لسنه 2014\n...",
  "cleaning_rules_applied": ["strip_html","remove_zero_width","remove_harakat","normalize_spaces"],
  "cleaning_log": [
    {"rule": "remove_harakat", "instances_removed": 12},
    {"rule": "remove_zero_width", "instances_removed": 3}
  ]
}
```

**STRUCTURED (data/structured/docs/law-2014-34.json):**
```json
{
  "document": {
    "doc_id": "a3b4c5d6...",
    "doc_slug": "law-2014-34",
    "title_ar": "قانون ضريبة الدخل رقم (34) لسنة 2014",
    "doc_type": "law",
    "doc_number": "34",
    "issue_year": 2014,
    "issuing_entity_name_ar": "مجلس الأمة",
    "official_gazette_number": "5277",
    "publication_date": "2014-10-16",
    "effective_date": "2014-10-16",
    "status": "amended",
    "legal_basis_text": "استناداً لأحكام المادة (93) من الدستور",
    "source_url": "https://lob.gov.jo/AR/...",
    "applicability_scope": "general"
  },
  "versions": [{
    "version_id": "law-2014-34-v1",
    "version_number": 1,
    "version_type": "original",
    "is_current": false,
    "effective_from": "2014-10-16"
  }],
  "sections": [
    {"section_type": "preamble", "display_order": 1, "original_text": "استناداً لأحكام..."},
    {"section_type": "chapter", "section_number": "1", "section_label": "الفصل الأول – أحكام عامة", "display_order": 2},
    {"section_type": "article", "section_number": "1", "section_label": "المادة (1)", "display_order": 3, "original_text": "يُسمى هذا القانون..."}
  ],
  "topics": [{"topic_slug": "tax-law", "is_primary": true, "confidence": 0.9}],
  "relationships": [
    {"rel_type": "BASED_ON", "extracted_text": "استناداً لأحكام المادة (93) من الدستور", "target_doc_slug": "constitution-1952"}
  ]
}
```

**EXPORT — relational/documents.csv (one row):**
```
doc_id|doc_slug|title_ar|doc_type|doc_number|issue_year|status|publication_date|source_url
a3b4c5d6|law-2014-34|قانون ضريبة الدخل رقم (34) لسنة 2014|law|34|2014|amended|2014-10-16|https://lob.gov.jo/...
```

**EXPORT — graph/nodes_documents.csv (one row):**
```
:ID(doc)|doc_slug|title_ar|doc_type|issue_year|status|:LABEL
a3b4c5d6|law-2014-34|قانون ضريبة الدخل…|law|2014|amended|Document
```

**EXPORT — graph/edges_based_on.csv (one row):**
```
:START_ID|:END_ID|:TYPE|extracted_text
a3b4c5d6|constitution-id|BASED_ON|استناداً لأحكام المادة (93) من الدستور
```

---

*End of Architecture Document*
