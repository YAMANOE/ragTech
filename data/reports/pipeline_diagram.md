# Jordanian Legislative Intelligence Pipeline — Architecture Diagram
**Last updated:** March 15, 2026  
**Status:** 99 documents structured · 11/11 QA checks passing · 91/91 tests passing

---

## Full Pipeline Overview

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                            ENTRY POINTS                                        ║
║                                                                                ║
║  scripts/run_first_100.py        scripts/run_pipeline.py                       ║
║  (batch: up to 100 laws)         (single document by URL or saved file)        ║
╚════════════════════════════════╤═══════════════════════════════════════════════╝
                                 │
                                 ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  STAGE 1 — FETCH                                                               ║
║  Class: LOBFetcher  (pipeline/fetcher.py)                                      ║
║  Tool:  Playwright + headless Chromium                                         ║
║                                                                                ║
║  INPUT                                                                         ║
║  ├── LOB detail URL                                                            ║
║  │   https://lob.gov.jo/?v=2&lang=ar#!/LegislationDetails?LegislationID=N     ║
║  └── doc_slug  (e.g. "law-2018-34")                                            ║
║                                                                                ║
║  PROCESS                                                                       ║
║  1. Launch Chromium (locale=ar-JO, Accept-Language: ar-JO,ar)                 ║
║  2. page.goto(url, wait_until="networkidle")                                   ║
║  3. Wait for: div.clicked-legislation-header                                   ║
║  4. page.evaluate() — strip nav/header/footer, return body.innerText           ║
║  5. page.content() — save full HTML                                            ║
║  6. Sleep FETCH_DELAY_SECONDS (default 2s)                                     ║
║  7. On selector miss → save debug screenshot to logs/                          ║
║                                                                                ║
║  OUTPUT                                                                        ║
║  ├── data/raw/html/{slug}_{YYYYMMDD_HHMMSS}.html                               ║
║  ├── data/raw/text/{slug}_{YYYYMMDD_HHMMSS}.txt                                ║
║  ├── data/raw/source_registry.csv  (append-only, pipe-delimited)               ║
║  └── FetchRecord  (in-memory → passed to Stage 2)                              ║
╚══════════════════════════════════════════════════════════════════════════════════╝
                                 │
                                 ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  STAGE 2 — PARSE                                                               ║
║  Class: LOBParser  (pipeline/parser.py)                                        ║
║  Tools: BeautifulSoup4 + lxml, regex, ArabicTextUtils                          ║
║                                                                                ║
║  INPUT                                                                         ║
║  ├── raw HTML string (from Stage 1 or saved .html file)                        ║
║  └── doc_slug                                                                  ║
║                                                                                ║
║  PROCESS                                                                       ║
║  1. BeautifulSoup parse — decompose script/style/nav/header/footer             ║
║  2. Extract title via CSS selector cascade                                     ║
║     → div.clicked-legislation-header h3  (primary)                             ║
║     → h2 / h1 / h3.animated  (fallbacks)                                      ║
║  3. Extract body via div.clicked-legislation-content                           ║
║  4. ATU.remove_lob_artifacts() — strip 17 Angular UI label strings             ║
║  5. _extract_metadata()  3-pass doc_type detection:                            ║
║     Pass 1: title first-word exact match against DOC_TYPE_MAP                  ║
║     Pass 2: title keyword scan                                                 ║
║     Pass 3: fallback to LOB API TypeArName field                               ║
║     Also: doc_number, issue_year, gazette, dates, legal_basis, status          ║
║  6. _segment_sections() — line-by-line classification:                         ║
║     preamble → باب/جزء → فصل/قسم → المادة → أ.ب.ج. → numeric → ملحق          ║
║  7. Collect parse_notes (WARN/INFO strings)                                    ║
║                                                                                ║
║  OUTPUT                                                                        ║
║  └── ParsedDocument dict                                                       ║
║      ├── metadata   (doc_type, doc_number, year, gazette, dates, status ...)   ║
║      ├── sections   list[ParsedSection]                                        ║
║      ├── attachments                                                           ║
║      └── parse_notes                                                           ║
╚══════════════════════════════════════════════════════════════════════════════════╝
                                 │
                                 ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  STAGE 3 — CLEAN                                                               ║
║  Class: ArabicTextCleaner  (pipeline/cleaner.py)                               ║
║  Tools: pyarabic, regex, ArabicTextUtils                                       ║
║                                                                                ║
║  INPUT                                                                         ║
║  └── raw text string  (from data/raw/text/ or Stage 2 body text)               ║
║                                                                                ║
║  PROCESS — Stage A → original_text (rules 1–6, no semantic change)            ║
║  1. strip_html_artifacts    — remove tags, decode &amp; &nbsp; etc.            ║
║  2. remove_zero_width       — U+200B/C/D, U+FEFF, U+00AD                      ║
║  3. remove_tatweel          — U+0640 kashida                                   ║
║  4. remove_harakat          — diacritics U+064B–U+065F                         ║
║  5. remove_page_markers     — "صفحة N", "Page N of M"                         ║
║  6. normalize_spaces        — multi-space → 1 space; 3+ newlines → 2          ║
║                                                                                ║
║  PROCESS — Stage B → normalized_text (rules 7–10 on top of Stage A)           ║
║  7. convert_arabic_to_western_digits  — ٠١٢٣ → 0123                           ║
║  8. normalize_alef                    — آ أ إ ٱ → ا                           ║
║  9. normalize_tamarbouta              — ة → ه at word boundaries               ║
║  10. normalize_yeh                    — ى → ي                                 ║
║                                                                                ║
║  PROCESS — Stage C → sanity check                                              ║
║  - contains_arabic() must be True                                              ║
║  - len(text) >= 200 chars                                                      ║
║  - "المادة" pattern present                                                    ║
║                                                                                ║
║  OUTPUT                                                                        ║
║  ├── data/clean/{slug}_clean.json    (original_text + normalized_text)         ║
║  ├── data/clean/cleaning_log.csv     (append-only audit trail)                 ║
║  └── CleanOutput  (in-memory → passed to Stage 4)                              ║
╚══════════════════════════════════════════════════════════════════════════════════╝
                                 │
                                 ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  STAGE 4 — STRUCTURE                                                           ║
║  Class: LegislationStructurer  (pipeline/structurer.py)                        ║
║  Tools: ArabicTextUtils, IDGenerator, yaml, ArabicTextCleaner                  ║
║                                                                                ║
║  INPUT                                                                         ║
║  ├── ParsedDocument  (from Stage 2)                                            ║
║  └── CleanOutput     (from Stage 3)                                            ║
║                                                                                ║
║  PROCESS — 10 sequential sub-steps                                             ║
║  1. _build_document()       → Document record                                  ║
║  2. _build_version()        → DocumentVersion (v1, original)                   ║
║  3. _build_sections()       → list[Section]                                    ║
║     • Two-pass: assign section_ids then build objects                          ║
║     • Para splitting fix: sub-paragraph blocks split individually              ║
║     • Per-section: clean_section_text() + scan_compliance_flags()              ║
║  4. _extract_entities()     → list[Entity] + list[EntityRole]                  ║
║     • Regex: وزارة / هيئة / مجلس / محكمة / دائرة / سلطة / مديرية             ║
║     • validate_entity_candidate() filter (length + false-positive words)       ║
║     • Global dedup via entity_registry dict                                    ║
║     • First entity in doc → role="issuer"                                      ║
║  5. _classify_topics()      → list[Topic] + list[TopicAssignment]              ║
║     • Load 21 topics from config/topics.yaml                                   ║
║     • Keyword scoring:  title match → +0.40                                    ║
║                         early text (first 800 chars) → +0.20                  ║
║                         full body → +0.05                                      ║
║     • Threshold: 0.40; highest confidence = is_primary                         ║
║     • AraBERT fallback (optional): if best_keyword_conf < 0.5                  ║
║       → AraBERTClassifier (pipeline/arabert_classifier.py)                     ║
║       → cosine similarity on aubmindlab/bert-base-arabertv2 embeddings          ║
║  6. _detect_relationships() → list[DocumentRelationship]                       ║
║     • BASED_ON:   استناداً / بناءً / بمقتضى clauses (conf 0.85)               ║
║     • BASED_ON+:  "الدستور" in basis text → const. slug (conf 0.90)            ║
║     • AMENDS:     يُعدَّل trigger + ≤5 refs (conf 0.80)                       ║
║     • REPEALS:    يُلغى trigger + ≤3 refs (conf 0.80)                         ║
║     • REFERS_TO:  other cross-references (conf 0.60)                           ║
║  7. classify_scope()        → document.applicability_scope                     ║
║  8. _save_structured()      → writes JSON + summary + updates index            ║
║  9. _save_summary()         → lightweight summary JSON                         ║
║  10. _update_documents_index()                                                 ║
║                                                                                ║
║  OUTPUT                                                                        ║
║  ├── data/structured/docs/{slug}.json          (full document object)          ║
║  ├── data/structured/summaries/{slug}_summary.json                             ║
║  ├── data/structured/documents_index.json      (cumulative, upserted)          ║
║  └── PipelineResult  (in-memory → passed to Stage 5)                           ║
╚══════════════════════════════════════════════════════════════════════════════════╝
                                 │
                                 ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  STAGE 5 — VALIDATE                                                            ║
║  Class: PipelineValidator  (pipeline/validator.py)                             ║
║  Tools: ArabicTextUtils, regex                                                 ║
║                                                                                ║
║  INPUT                                                                         ║
║  └── PipelineResult  (from Stage 4)                                            ║
║                                                                                ║
║  CHECKS (11 checks, in order)                                                  ║
║  1. required_fields         doc_slug, doc_id, title_ar, doc_type, status,      ║
║                             source_url  (severity: ERROR — blocks export)      ║
║  2. arabic_text             title_ar must contain Arabic chars (ERROR)          ║
║  3. status_valid            status ∈ {active, amended, repealed, draft,        ║
║                             pending}  (ERROR)                                  ║
║  4. source_url              must start with "http"  (ERROR)                    ║
║  5. raw_files_exist         raw_text_path + clean_json_path on disk  (ERROR)   ║
║  6. versions                ≥1 version; exactly 1 is_current=True;             ║
║                             full_text not empty  (ERROR)                       ║
║  7. sections                ≥1 section; IDs populated; order unique (ERROR)    ║
║  8. relationships           no self-references; confidence ∈ [0,1]  (WARN)    ║
║  9. topics                  confidence ∈ [0,1]; ≤1 is_primary  (WARN)         ║
║  10. metadata_quality       warns if issue_year or doc_number absent (WARN)    ║
║  11. section_artifact_leak  warns if LOB UI strings in section text  (WARN)    ║
║                                                                                ║
║  OUTPUT                                                                        ║
║  └── list[ValidationResult]  (in-memory → controls whether Stage 6 runs)      ║
╚══════════════════════════════════════════════════════════════════════════════════╝
                                 │
             ┌───────────────────┴───────────────────┐
             │ has_errors() == False?                 │
             ▼ YES                                    ▼ NO
╔═════════════════════════════╗          ╔══════════════════════════════╗
║  STAGE 6 — EXPORT           ║          ║  PIPELINE ABORTED            ║
║  Class: LegislationExporter ║          ║  Error logged to:            ║
║  (pipeline/exporter.py)     ║          ║  logs/structurer.log         ║
║  Tools: csv, json           ║          ║  Batch report updated with   ║
╚═════════════════════════════╝          ║  failure entry               ║
             │                           ╚══════════════════════════════╝
             ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  STAGE 6 — EXPORT                                                              ║
║  Class: LegislationExporter  (pipeline/exporter.py)                            ║
║  Tools: csv, json                                                              ║
║                                                                                ║
║  INPUT                                                                         ║
║  └── PipelineResult  (from Stage 5)                                            ║
║                                                                                ║
║  RELATIONAL OUTPUT  (exports/relational/ — pipe-delimited, utf-8-sig BOM)     ║
║  ├── documents.csv               1 row/law         (99 rows)                   ║
║  ├── versions.csv                1 row/version     (99 rows — v1 only)         ║
║  ├── sections.csv                1 row/section     (8,512 rows)                ║
║  ├── section_compliance_flags.csv                  (8,512 rows)                ║
║  ├── entities.csv                deduplicated      (549 rows)                  ║
║  ├── topics.csv                  taxonomy nodes    (21 topics)                 ║
║  ├── document_topics.csv         junction          (147 rows)                  ║
║  └── document_relationships.csv  edges             (16 edges)                  ║
║                                                                                ║
║  GRAPH OUTPUT  (exports/graph/ — for Neo4j neo4j-admin import)                 ║
║  Node files (5):                                                               ║
║  ├── nodes_documents.csv         nodes_versions.csv                            ║
║  ├── nodes_sections.csv          nodes_entities.csv                            ║
║  └── nodes_topics.csv                                                          ║
║  Edge files (11+):                                                             ║
║  ├── edges_has_version.csv       edges_has_section.csv                         ║
║  ├── edges_issued_by.csv         edges_has_topic.csv                           ║
║  ├── edges_amends.csv            edges_repeals.csv                             ║
║  ├── edges_based_on.csv          edges_refers_to.csv                           ║
║  ├── edges_implements.csv        edges_supplements.csv                         ║
║  └── edges_supersedes.csv                                                      ║
║  Future edge headers (6, header-only, Layer 2):                                ║
║  └── future/edges_future_has_obligation.csv  etc.                              ║
╚══════════════════════════════════════════════════════════════════════════════════╝
```

---

## Post-Processing Layer (runs separately after main pipeline)

These scripts operate on the `data/structured/docs/*.json` files already produced by Stages 1–6. They do not re-fetch or re-parse anything.

```
data/structured/docs/*.json  ─────────────────────────────────────────────────┐
data/clean/*.json                                                              │
                                                                               │
              ┌────────────────────────────────────────────────────┐          │
              │            POST-PROCESSING TOOLS                   │◄─────────┘
              └────────────────────────────────────────────────────┘
                       │                │                 │
                       ▼                ▼                 ▼
╔══════════════════╗  ╔═══════════════════════╗  ╔═══════════════════════════╗
║  PP-A: RETOPIC   ║  ║  PP-B: ENTITY DEDUP   ║  ║  PP-C: CLAUDE/GEMINI      ║
║ scripts/         ║  ║ scripts/              ║  ║  ENHANCER                 ║
║  retopic.py      ║  ║  dedup_entities.py    ║  ║ pipeline/                 ║
║                  ║  ║                       ║  ║  gemini_enhancer.py       ║
║ Re-runs topic    ║  ║ Merges near-duplicate ║  ║  (ClaudeEnhancer class)   ║
║ classification   ║  ║ entity names using    ║  ║                           ║
║ from topics.yaml ║  ║ SAFE_MERGES map       ║  ║ Re-classifies docs with   ║
║ against all      ║  ║ (explicit hamza/alef  ║  ║ primary_conf < 0.80 via   ║
║ existing docs    ║  ║ spelling variants,    ║  ║ Claude API call           ║
║ (no re-fetch,    ║  ║ typos, grammatical    ║  ║ Only adopts result if     ║
║  no re-parse)    ║  ║ case differences)     ║  ║ Claude returns ≥ 0.90     ║
║                  ║  ║                       ║  ║                           ║
║ Tools:           ║  ║ Tools:                ║  ║ Tools:                    ║
║ • yaml           ║  ║ • IDGenerator         ║  ║ • anthropic SDK           ║
║ • ArabicTextUtils║  ║ • json (patch-in-     ║  ║ • claude-sonnet-4-6       ║
║ • IDGenerator    ║  ║   place on JSON)      ║  ║ • VALID_TOPIC_IDS set     ║
║                  ║  ║                       ║  ║                           ║
║ Reads:           ║  ║ Reads:                ║  ║ Reads:                    ║
║ • config/        ║  ║ • data/structured/    ║  ║ • data/structured/        ║
║   topics.yaml    ║  ║   docs/*.json         ║  ║   docs/*.json             ║
║ • data/clean/    ║  ║                       ║  ║                           ║
║   {slug}_clean   ║  ║ Patches:              ║  ║ Writes:                   ║
║   .json          ║  ║ • entities[]          ║  ║ • topic_assignments[]     ║
║ • data/          ║  ║   entity_id/slug/name ║  ║   in structured JSON      ║
║   structured/    ║  ║ • entity_roles[]      ║  ║   (primary topic +        ║
║   docs/*.json    ║  ║   entity_id/name/     ║  ║    confidence updated)    ║
║                  ║  ║   role_id             ║  ║                           ║
║ Writes:          ║  ║ • document{}          ║  ║ Result:                   ║
║ • topic_         ║  ║   issuing_entity_id/  ║  ║ • 298 → 270 entity names  ║
║   assignments[]  ║  ║   name                ║  ║   after SAFE_MERGES       ║
║   in structured  ║  ║                       ║  ║ • conf threshold run on   ║
║   JSON (in-place)║  ║ Result:               ║  ║   all 99 docs             ║
║                  ║  ║ 28 clusters merged    ║  ║                           ║
║ Result:          ║  ║ 298 → 270 unique      ║  ║                           ║
║ 55.1% → 100%     ║  ║ canonical names       ║  ║                           ║
║ topic coverage   ║  ║                       ║  ║                           ║
╚══════════════════╝  ╚═══════════════════════╝  ╚═══════════════════════════╝
              │                │                 │
              └────────────────┴─────────────────┘
                                 │
                                 ▼
              Re-export to update CSVs  →  LegislationExporter.export_all()
```

---

## Quality Gate: Tests & QA Checks

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║  QUALITY ASSURANCE                                                             ║
╠══════════════════════════════════════════════════════════════════════════════════╣
║                                                                                ║
║  AUTOMATED TESTS  (tests/)                      91 / 91 passing               ║
║  ├── test_parser.py              LOBParser unit tests                          ║
║  ├── test_entity_extraction.py   Entity regex + validation tests               ║
║  ├── test_topic_classifier.py    Topic scoring algorithm tests                 ║
║  ├── test_doc_type_detection.py  3-pass doc_type detection tests               ║
║  ├── test_status_mapping.py      Status normalization (20 values)              ║
║  ├── test_integration.py         End-to-end pipeline integration tests         ║
║  └── conftest.py                 Shared fixtures                               ║
║                                                                                ║
║  QA CHECKS  (data/reports/final_qa_report_v2.txt)   11 / 11 passing           ║
║  1a. Total document rows == 99                                 ✅ PASS         ║
║  1b. All slugs unique (no duplicates)                          ✅ PASS         ║
║  1c. Zero unknown doc_type                                     ✅ PASS         ║
║  2a. Section row count > 0                  (rows=8,512)       ✅ PASS         ║
║  2b. No empty original_text in sections                        ✅ PASS         ║
║  3a. No dirty entity names (LOB UI artifact leak)              ✅ PASS         ║
║  4a. All 99 docs have ≥1 topic assignment                      ✅ PASS         ║
║  5a. No غير ساري + active status mismatch                     ✅ PASS         ║
║  6a. Same set of slugs in JSON and CSV                         ✅ PASS         ║
║  6b. doc_type matches JSON ↔ CSV                               ✅ PASS         ║
║  6c. Section counts match JSON ↔ CSV                           ✅ PASS         ║
╚══════════════════════════════════════════════════════════════════════════════════╝
```

---

## Data Flow Summary (Files Only)

```
lob.gov.jo URL
     │
     ▼
data/raw/html/{slug}_{ts}.html          ← Stage 1 (Fetch)
data/raw/text/{slug}_{ts}.txt           ←
data/raw/source_registry.csv            ←
     │
     ▼  (parse from HTML)
[ParsedDocument in memory]              ← Stage 2 (Parse)
     │
     ▼  (clean from .txt)
data/clean/{slug}_clean.json            ← Stage 3 (Clean)
data/clean/cleaning_log.csv             ←
     │
     ▼
data/structured/docs/{slug}.json        ← Stage 4 (Structure)
data/structured/summaries/{slug}_summary.json
data/structured/documents_index.json    ←
     │
     ▼  (validate — no new files)
[ValidationResult list in memory]       ← Stage 5 (Validate)
     │
     ▼
exports/relational/documents.csv        ← Stage 6 (Export)
exports/relational/versions.csv
exports/relational/sections.csv
exports/relational/section_compliance_flags.csv
exports/relational/entities.csv
exports/relational/topics.csv
exports/relational/document_topics.csv
exports/relational/document_relationships.csv
exports/graph/nodes_*.csv  (×5)
exports/graph/edges_*.csv  (×11)
exports/graph/future/edges_future_*.csv (×6 — header-only)
     │
     ▼  (post-processing)
data/structured/docs/{slug}.json        ← PP-A retopic.py   (topic_assignments patched)
data/structured/docs/{slug}.json        ← PP-B dedup_entities.py (entities patched)
data/structured/docs/{slug}.json        ← PP-C gemini_enhancer.py (low-conf topics)
     │
     ▼  (re-export after post-processing)
exports/relational/  (all CSVs rebuilt) ← LegislationExporter.export_all()
exports/graph/
```

---

## Tool & Dependency Map

| Stage | Class / Script | Key Tools |
|-------|----------------|-----------|
| 1 — Fetch | `LOBFetcher` | Playwright, Chromium (headless), `asyncio` |
| 2 — Parse | `LOBParser` | BeautifulSoup4, lxml, `regex`, `ArabicTextUtils` |
| 3 — Clean | `ArabicTextCleaner` | `pyarabic`, `regex`, `ArabicTextUtils` |
| 4 — Structure | `LegislationStructurer` | `ArabicTextUtils`, `IDGenerator`, `PyYAML`, `ArabicTextCleaner` |
| 4 — Topic fallback | `AraBERTClassifier` | `transformers` (aubmindlab/bert-base-arabertv2), `torch` |
| 5 — Validate | `PipelineValidator` | `ArabicTextUtils`, `regex` |
| 6 — Export | `LegislationExporter` | `csv`, `json` (stdlib) |
| PP-A — Retopic | `scripts/retopic.py` | `PyYAML`, `ArabicTextUtils`, `IDGenerator` |
| PP-B — Dedup | `scripts/dedup_entities.py` | `IDGenerator`, `json` (stdlib), `RapidFuzz` (implicit in SAFE_MERGES design) |
| PP-C — Enhance | `ClaudeEnhancer` | `anthropic` SDK, `claude-sonnet-4-6` via API |
| Config | `Settings` | `python-dotenv`, `pathlib` |
| IDs | `IDGenerator` | `uuid` (UUID5), `python-slugify` |
| Logging | All components | `loguru` (rotating, per-component log files in `logs/`) |

---

## Config Files Referenced by Pipeline

| File | Used by | Contents |
|------|---------|----------|
| `config/settings.py` | All stages | Paths, timeouts, thresholds, CSS selectors, type maps |
| `config/topics.yaml` | Stage 4, PP-A, PP-C | 21 topic definitions + keyword lists |
| `.env` | Settings loader | `LOB_BASE_URL`, `FETCH_DELAY_SECONDS`, `PLAYWRIGHT_TIMEOUT`, `ANTHROPIC_API_KEY` |

---

## Current Metrics (March 15, 2026)

| Metric | Value |
|--------|-------|
| Structured documents | 99 |
| Total sections | 8,512 |
| Entity rows | 549 (270 unique canonical names) |
| Entity clusters merged (dedup) | 28 clusters → 298 to 270 names |
| Topic taxonomy entries | 21 (all Level 1) |
| Topic assignment rows | 147 |
| Topic coverage | 100% (99/99 docs) |
| Inter-doc relationship edges | 16 |
| Legal basis extracted | 38.4% (38/99) |
| Status map values | 20 entries |
| QA checks passing | 11/11 |
| Tests passing | 91/91 |
| Pipeline failures | 2 (law-2006-49, law-1972-21) |
