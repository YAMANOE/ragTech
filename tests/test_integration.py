"""
tests/test_integration.py
--------------------------
Full end-to-end pipeline test on legislation-3452.

Runs all 6 stages (parse → clean → structure → validate → export) using the
existing raw HTML file so no network access is required.
The test reads the most recent HTML file from data/raw/html/ for the slug.

Assertions:
  - success == True
  - sections > 10
  - topics assigned (≥ 1)
  - all entity names are ≤ 4 words (or explicitly in the known-valid set)
  - doc_type != "unknown"
  - structured JSON written to data/structured/docs/
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SLUG = "legislation-3452"
HTML_DIR  = ROOT / "data" / "raw" / "html"
STRUCT_DIR = ROOT / "data" / "structured" / "docs"

# Known-valid entity names that may exceed 4 words
KNOWN_VALID_LONG_ENTITIES: frozenset = frozenset([
    "المؤسسة العامة للضمان الاجتماعي",
    "دائرة ضريبة الدخل والمبيعات",
    "هيئة تنظيم قطاع الاتصالات",
    "هيئة تنظيم الطاقة والمعادن",
])


@pytest.fixture(scope="module")
def pipeline_result(settings, tmp_path_factory):
    """
    Run the pipeline for legislation-3452 using its latest HTML file.
    Skips if no HTML file is available.
    Returns the PipelineResult object (not the summary dict).

    Exports are written to a temporary directory so that this test never
    contaminates the production exports/relational/*.csv files.
    """
    import copy
    from pipeline.cleaner import ArabicTextCleaner
    from pipeline.exporter import LegislationExporter
    from pipeline.parser import LOBParser
    from pipeline.structurer import LegislationStructurer
    from pipeline.validator import PipelineValidator

    html_files = sorted(HTML_DIR.glob(f"{SLUG}_*.html"))
    if not html_files:
        pytest.skip(f"No HTML file found for {SLUG} in {HTML_DIR}")

    html_path  = html_files[-1]   # most recent
    raw_html   = html_path.read_text(encoding="utf-8")

    # Isolated settings: redirect exports to a temp dir so pytest never
    # appends to the live exports/relational/ and exports/graph/ CSVs.
    tmp_dir = tmp_path_factory.mktemp("integration_exports")
    test_settings = copy.copy(settings)
    test_settings.RELATIONAL_DIR   = tmp_dir / "relational"
    test_settings.GRAPH_DIR        = tmp_dir / "graph"
    test_settings.GRAPH_FUTURE_DIR = tmp_dir / "graph" / "future"
    test_settings.RELATIONAL_DIR.mkdir(parents=True, exist_ok=True)
    test_settings.GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    test_settings.GRAPH_FUTURE_DIR.mkdir(parents=True, exist_ok=True)

    parser     = LOBParser(settings)
    cleaner    = ArabicTextCleaner(settings)
    structurer = LegislationStructurer(settings)
    validator  = PipelineValidator(settings)
    exporter   = LegislationExporter(test_settings)

    parsed_doc = parser.parse_html(raw_html, SLUG, source_url="")
    clean_out  = cleaner.clean(parsed_doc["raw_text"], doc_slug=SLUG, source_file=str(html_path))
    result     = structurer.structure(parsed_doc, clean_out)
    result.validation_results = validator.validate(result)
    exporter.export_result(result)

    return result


# ── Tests ──────────────────────────────────────────────────────────────────

class TestPipelineSuccess:
    def test_success_flag_is_true(self, pipeline_result):
        assert pipeline_result.success is True, (
            f"Pipeline returned success=False. Errors: {pipeline_result.errors}"
        )

    def test_no_pipeline_errors(self, pipeline_result):
        assert not pipeline_result.errors, (
            f"Pipeline produced errors: {pipeline_result.errors}"
        )


class TestSections:
    def test_section_count_above_10(self, pipeline_result):
        count = len(pipeline_result.sections)
        assert count > 10, f"Expected >10 sections, got {count}"

    def test_no_section_has_empty_text(self, pipeline_result):
        empty = [s for s in pipeline_result.sections if not (s.original_text or "").strip()]
        assert not empty, f"{len(empty)} sections have empty text"

    def test_paragraphs_exist(self, pipeline_result):
        pars = [s for s in pipeline_result.sections if s.section_type == "paragraph"]
        assert len(pars) > 0, "Expected at least one paragraph section"


class TestTopics:
    def test_at_least_one_topic_assigned(self, pipeline_result):
        count = len(pipeline_result.topic_assignments)
        assert count >= 1, f"Expected ≥1 topic assignment, got {count}"

    def test_primary_topic_exists(self, pipeline_result):
        primary = [a for a in pipeline_result.topic_assignments if a.is_primary]
        assert len(primary) == 1, f"Expected exactly 1 primary topic, got {len(primary)}"

    def test_agricultural_topic_assigned(self, pipeline_result):
        # legislation-3452 is the Agricultural Mutual Fund law
        slugs = [a.topic_id for a in pipeline_result.topic_assignments]
        assert any("agricultural" in s for s in slugs), (
            f"Expected agricultural topic in {slugs}"
        )


class TestEntities:
    def test_entities_extracted(self, pipeline_result):
        assert len(pipeline_result.entities) > 0, "Expected at least one entity"

    def test_no_entity_name_exceeds_4_words(self, pipeline_result):
        bad = []
        for ent in pipeline_result.entities:
            name = ent.entity_name_ar or ""
            if len(name.split()) > 4 and name not in KNOWN_VALID_LONG_ENTITIES:
                bad.append(name)
        assert not bad, f"Entities with >4 words: {bad}"

    def test_no_entity_name_is_empty(self, pipeline_result):
        empty = [e for e in pipeline_result.entities if not (e.entity_name_ar or "").strip()]
        assert not empty, f"{len(empty)} entities have empty names"


class TestDocType:
    def test_doc_type_is_not_unknown(self, pipeline_result):
        doc_type = pipeline_result.document.doc_type
        assert doc_type != "unknown", f"doc_type is 'unknown' — detection failed"

    def test_doc_type_is_law(self, pipeline_result):
        # legislation-3452 = قانون صندوق التكافل للحد من المخاطر الزراعية
        doc_type = pipeline_result.document.doc_type
        assert doc_type == "law", f"Expected 'law', got {doc_type!r}"


class TestStructuredJsonWritten:
    def test_json_file_exists(self, pipeline_result):
        json_path = STRUCT_DIR / f"{SLUG}.json"
        assert json_path.exists(), f"Structured JSON not written to {json_path}"

    def test_json_section_count_matches_result(self, pipeline_result):
        json_path = STRUCT_DIR / f"{SLUG}.json"
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        json_count   = len(data.get("sections", []))
        result_count = len(pipeline_result.sections)
        assert json_count == result_count, (
            f"JSON has {json_count} sections but PipelineResult has {result_count}"
        )
