# Jordanian RegTech — Legislative Intelligence Pipeline

A complete, production-ready data pipeline for collecting, cleaning, structuring, and exporting
Jordanian legislation from the Bureau of Legislation and Opinion (LOB). 

**Layer 1: Legislative Intelligence** (current implementation)  
**Layer 2: Institutional Compliance** (future extension — data model is compliance-ready)

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Playwright browsers
playwright install chromium

# 3. Copy environment file
cp .env.example .env

# 4. Run the MVP (5 target laws)
python scripts/run_mvp.py

# 5. Run full pipeline on a custom URL
python scripts/run_pipeline.py --url "https://lob.gov.jo/AR/..." --slug "law-2014-34"
```

---

## Project Structure

```
new-project/
├── ARCHITECTURE.md     ← Full design document (start here)
├── config/             ← Settings + topic taxonomy
├── models/             ← Dataclasses (Document, Version, Section, Entity, Topic, Relationship)
├── utils/              ← Arabic text utilities + ID generation
├── pipeline/           ← fetcher → parser → cleaner → structurer → exporter → validator
├── scripts/            ← run_pipeline.py, run_mvp.py
├── data/               ← raw/ clean/ structured/ (gitignored)
└── exports/            ← relational/ graph/ (gitignored)
```

---

## Pipeline Layers

| Layer | Input | Output |
|-------|-------|--------|
| **Fetch** | LOB URL | raw HTML + text + source_registry.csv |
| **Parse** | raw HTML | metadata dict + section list |
| **Clean** | raw text | original + normalized text + cleaning_log.json |
| **Structure** | parsed + clean | documents / versions / sections / entities / topics / relationships JSON |
| **Export** | structured JSON | relational CSVs + graph node/edge CSVs |
| **Validate** | all outputs | QA report with pass/fail per rule |

---

## Output Packages

### Relational CSVs (`exports/relational/`)
Direct import into PostgreSQL, SQLite, Supabase, or any RDBMS.

| File | Description |
|------|-------------|
| `documents.csv` | One row per legislation document |
| `versions.csv` | One row per version of each document |
| `sections.csv` | One row per article / paragraph / clause |
| `entities.csv` | Ministries, authorities, councils |
| `topics.csv` | Legal topic taxonomy |
| `document_topics.csv` | Multi-label topic assignments |
| `document_entities.csv` | Document-entity relationships |
| `document_relationships.csv` | AMENDS / REPEALS / BASED_ON / etc. |
| `section_compliance_flags.csv` | Compliance-readiness flags (Layer 2 prep) |

### Graph CSVs (`exports/graph/`)
Neo4j `neo4j-admin import` format.

| Node Files | Edge Files |
|------------|------------|
| `nodes_documents.csv` | `edges_has_version.csv` |
| `nodes_versions.csv` | `edges_has_section.csv` |
| `nodes_sections.csv` | `edges_issued_by.csv` |
| `nodes_entities.csv` | `edges_amends.csv` |
| `nodes_topics.csv` | `edges_repeals.csv` |
| | `edges_based_on.csv` |
| | `edges_refers_to.csv` |
| | `edges_implements.csv` |
| | `edges_has_topic.csv` |
| | `future/edges_future_*.csv` ← headers only |

---

## Key Design Decisions

- **Original text is always preserved.** Normalization is a second copy only.
- **Stable identifiers** (`doc_slug`, `version_id`, `section_id`) survive re-runs.
- **Compliance-ready schema** — Layer 2 fields exist in every section record (NULL now).
- **Default to current version** — `is_current=True` flag on every version query.
- **Arabic-first** — all patterns, cleaning rules, and ID generation handle Arabic legally.
- **Graph-ready** — all exports are compatible with Neo4j import format.

---

## Architecture Document

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full 20-section design guide including:
- Data models and field definitions
- Arabic text cleaning rules
- LOB extraction strategy
- Legal pattern batteries
- Versioning strategy
- Compliance-readiness design
- QA rules
- Edge cases
- MVP plan

---

## Adding New Laws

```python
from pipeline.fetcher import LOBFetcher
from pipeline.parser import LOBParser
from pipeline.cleaner import ArabicTextCleaner
from pipeline.structurer import LegislationStructurer
from pipeline.exporter import LegislationExporter
from config.settings import Settings

settings = Settings()
fetcher = LOBFetcher(settings)
result = fetcher.fetch_sync("https://lob.gov.jo/AR/...", "law-2020-10")
# Then: parse → clean → structure → export
```

---

## License

Internal project — not for distribution.
