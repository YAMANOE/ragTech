"""
pipeline/validator.py
----------------------
QA and validation rules for the legislative intelligence pipeline.

Checks all mandatory fields, structural integrity, cross-reference validity,
and data quality after each pipeline run.

Severity levels:
  - error:   Blocks export / must be fixed
  - warning: Logged but does not block export
  - info:    Informational note

ValidationResult objects are collected in PipelineResult.validation_results.

Usage:
    from pipeline.validator import PipelineValidator
    from config.settings import Settings

    validator = PipelineValidator(Settings())
    val_results = validator.validate(pipeline_result)
    # Check validator.has_errors(val_results) before export
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger

from config.settings import Settings
from models.schema import (
    Document, DocumentRelationship, DocumentVersion,
    PipelineResult, Section, ValidationResult,
)
from utils.arabic_utils import ArabicTextUtils as ATU


class PipelineValidator:
    """
    Runs all QA checks on a PipelineResult.
    Returns a list of ValidationResult objects.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def validate(self, result: PipelineResult) -> list[ValidationResult]:
        """Run all validation checks. Returns list of ValidationResult."""
        checks: list[ValidationResult] = []

        if not result.document:
            checks.append(ValidationResult(
                check_name="has_document",
                passed=False,
                doc_slug=result.doc_slug,
                detail="PipelineResult has no Document record",
                severity="error",
            ))
            return checks

        doc = result.document

        # ── Document-level checks ─────────────────────────────────────────
        checks += self._check_required_fields(doc)
        checks += self._check_arabic_text(doc)
        checks += self._check_status_valid(doc)
        checks += self._check_source_url(doc)
        checks += self._check_raw_files_exist(doc)

        # ── Version checks ────────────────────────────────────────────────
        checks += self._check_versions(result.versions, doc)

        # ── Section checks ────────────────────────────────────────────────
        checks += self._check_sections(result.sections, result.versions)

        # ── Relationship checks ───────────────────────────────────────────
        checks += self._check_relationships(result.relationships, doc.doc_id)

        # ── Topic assignment checks ───────────────────────────────────────
        checks += self._check_topics(result.topic_assignments)

        # ── Metadata quality checks ───────────────────────────────────────
        full_text = result.versions[0].full_text_original if result.versions else ""
        checks += self._check_metadata_quality(doc, full_text)

        # ── UI artifact leak checks ───────────────────────────────────────
        checks += self._check_section_artifact_leak(result.sections)

        # ── Summary log ───────────────────────────────────────────────────
        errors   = [r for r in checks if not r.passed and r.severity == "error"]
        warnings = [r for r in checks if not r.passed and r.severity == "warning"]

        if errors:
            logger.error(
                f"[Validator] {doc.doc_slug}: {len(errors)} errors, "
                f"{len(warnings)} warnings"
            )
            for e in errors:
                logger.error(f"  ✗ [{e.check_name}] {e.detail}")
        elif warnings:
            logger.warning(
                f"[Validator] {doc.doc_slug}: 0 errors, {len(warnings)} warnings"
            )
            for w in warnings:
                logger.warning(f"  ⚠ [{w.check_name}] {w.detail}")
        else:
            logger.success(f"[Validator] {doc.doc_slug}: All checks passed ✓")

        return checks

    @staticmethod
    def has_errors(results: list[ValidationResult]) -> bool:
        """Return True if any error-severity check failed."""
        return any(not r.passed and r.severity == "error" for r in results)

    @staticmethod
    def has_warnings(results: list[ValidationResult]) -> bool:
        return any(not r.passed and r.severity == "warning" for r in results)

    def validate_export_files(self) -> list[ValidationResult]:
        """
        Check that all expected relational and graph CSV files exist
        and have at least a header row.
        """
        checks: list[ValidationResult] = []

        expected_relational = [
            "documents.csv", "versions.csv", "sections.csv",
            "entities.csv", "topics.csv", "document_topics.csv",
            "document_relationships.csv", "section_compliance_flags.csv",
        ]
        expected_graph = [
            "nodes_documents.csv", "nodes_versions.csv", "nodes_sections.csv",
            "nodes_entities.csv", "nodes_topics.csv",
            "edges_has_version.csv", "edges_has_section.csv",
        ]

        for fname in expected_relational:
            fpath = self.settings.RELATIONAL_DIR / fname
            checks.append(ValidationResult(
                check_name=f"relational_file_exists_{fname}",
                passed=fpath.exists(),
                detail=f"{'Found' if fpath.exists() else 'MISSING'}: {fpath}",
                severity="warning" if not fpath.exists() else "info",
            ))

        for fname in expected_graph:
            fpath = self.settings.GRAPH_DIR / fname
            checks.append(ValidationResult(
                check_name=f"graph_file_exists_{fname}",
                passed=fpath.exists(),
                detail=f"{'Found' if fpath.exists() else 'MISSING'}: {fpath}",
                severity="warning" if not fpath.exists() else "info",
            ))

        return checks

    # ── Document-level checks ─────────────────────────────────────────────────

    def _check_required_fields(self, doc: Document) -> list[ValidationResult]:
        checks = []
        required = {
            "doc_slug":   doc.doc_slug,
            "doc_id":     doc.doc_id,
            "title_ar":   doc.title_ar,
            "doc_type":   doc.doc_type,
            "status":     doc.status,
            "source_url": doc.source_url,
        }
        for field, value in required.items():
            passed = bool(value and str(value).strip())
            checks.append(ValidationResult(
                check_name=f"required_field_{field}",
                passed=passed,
                doc_slug=doc.doc_slug,
                detail="" if passed else f"Required field '{field}' is empty",
                severity="error",
            ))
        return checks

    def _check_arabic_text(self, doc: Document) -> list[ValidationResult]:
        has_arabic = ATU.contains_arabic(doc.title_ar or "")
        return [ValidationResult(
            check_name="title_contains_arabic",
            passed=has_arabic,
            doc_slug=doc.doc_slug,
            detail="" if has_arabic else f"title_ar has no Arabic characters: '{doc.title_ar}'",
            severity="error",
        )]

    def _check_status_valid(self, doc: Document) -> list[ValidationResult]:
        valid_statuses = {"active", "amended", "repealed", "draft", "pending", "suspended"}
        passed = doc.status in valid_statuses
        return [ValidationResult(
            check_name="status_valid",
            passed=passed,
            doc_slug=doc.doc_slug,
            detail="" if passed else f"Invalid status value: '{doc.status}'",
            severity="error",
        )]

    def _check_source_url(self, doc: Document) -> list[ValidationResult]:
        passed = bool(doc.source_url) and doc.source_url.startswith("http")
        return [ValidationResult(
            check_name="source_url_format",
            passed=passed,
            doc_slug=doc.doc_slug,
            detail="" if passed else f"source_url looks invalid: '{doc.source_url}'",
            severity="warning",
        )]

    def _check_raw_files_exist(self, doc: Document) -> list[ValidationResult]:
        checks = []
        for field, fpath in [("raw_text_path", doc.raw_text_path),
                               ("clean_json_path", doc.clean_json_path)]:
            if fpath:
                exists = Path(fpath).exists()
                checks.append(ValidationResult(
                    check_name=f"file_exists_{field}",
                    passed=exists,
                    doc_slug=doc.doc_slug,
                    detail="" if exists else f"File not found: {fpath}",
                    severity="warning",
                ))
        return checks

    # ── Version checks ────────────────────────────────────────────────────────

    def _check_versions(
        self,
        versions: list[DocumentVersion],
        doc: Document,
    ) -> list[ValidationResult]:
        checks = []

        # Must have at least one version
        checks.append(ValidationResult(
            check_name="has_at_least_one_version",
            passed=len(versions) >= 1,
            doc_slug=doc.doc_slug,
            detail="" if versions else "No versions found",
            severity="error",
        ))

        if not versions:
            return checks

        # Must have exactly one is_current=True
        current = [v for v in versions if v.is_current]
        checks.append(ValidationResult(
            check_name="exactly_one_current_version",
            passed=len(current) == 1,
            doc_slug=doc.doc_slug,
            detail="" if len(current) == 1
                else f"Expected 1 current version, found {len(current)}",
            severity="error",
        ))

        # effective_from on current version
        for v in current:
            if v.effective_to is not None and v.effective_from is not None:
                try:
                    from dateutil.parser import parse as parse_date
                    eff_from = parse_date(v.effective_from)
                    eff_to   = parse_date(v.effective_to)
                    passed = eff_to > eff_from
                    checks.append(ValidationResult(
                        check_name="version_date_order",
                        passed=passed,
                        doc_slug=doc.doc_slug,
                        record_id=v.version_id,
                        detail="" if passed
                            else f"effective_to {v.effective_to} not after effective_from {v.effective_from}",
                        severity="warning",
                    ))
                except Exception:
                    pass

        # Full text not empty
        for v in versions:
            passed = bool(v.full_text_original.strip())
            checks.append(ValidationResult(
                check_name="version_text_not_empty",
                passed=passed,
                doc_slug=doc.doc_slug,
                record_id=v.version_id,
                detail="" if passed else "full_text_original is empty",
                severity="error",
            ))

        return checks

    # ── Section checks ────────────────────────────────────────────────────────

    def _check_sections(
        self,
        sections: list[Section],
        versions: list[DocumentVersion],
    ) -> list[ValidationResult]:
        checks = []

        if not sections:
            checks.append(ValidationResult(
                check_name="has_sections",
                passed=False,
                detail="No sections found — law may need manual review",
                severity="warning",
            ))
            return checks

        # Uniqueness of display_order within each version
        version_orders: dict[str, set[int]] = {}
        for s in sections:
            if s.version_id not in version_orders:
                version_orders[s.version_id] = set()
            if s.display_order in version_orders[s.version_id]:
                checks.append(ValidationResult(
                    check_name="unique_section_order",
                    passed=False,
                    record_id=s.section_id,
                    detail=f"Duplicate display_order {s.display_order} in version {s.version_id}",
                    severity="error",
                ))
            version_orders[s.version_id].add(s.display_order)

        # No empty section text
        for s in sections:
            if s.section_type not in {"chapter", "part", "title"}:
                passed = bool(s.original_text.strip())
                if not passed:
                    checks.append(ValidationResult(
                        check_name="section_text_not_empty",
                        passed=False,
                        record_id=s.section_id,
                        detail=f"Empty original_text in section {s.section_id}",
                        severity="warning",
                    ))

        # Parent-child FK integrity
        all_ids = {s.section_id for s in sections}
        for s in sections:
            if s.parent_section_id and s.parent_section_id not in all_ids:
                checks.append(ValidationResult(
                    check_name="section_parent_fk",
                    passed=False,
                    record_id=s.section_id,
                    detail=(
                        f"parent_section_id '{s.parent_section_id}' "
                        "not found in section list"
                    ),
                    severity="warning",
                ))

        # Article count summary (info)
        article_count = sum(1 for s in sections if s.section_type == "article")
        checks.append(ValidationResult(
            check_name="article_count_info",
            passed=True,
            detail=f"{len(sections)} total sections, {article_count} articles",
            severity="info",
        ))

        return checks

    # ── Relationship checks ───────────────────────────────────────────────────

    def _check_relationships(
        self,
        relationships: list[DocumentRelationship],
        source_doc_id: str,
    ) -> list[ValidationResult]:
        checks = []
        for rel in relationships:
            # Source must match the current document
            if rel.source_doc_id != source_doc_id:
                checks.append(ValidationResult(
                    check_name="relationship_source_matches_doc",
                    passed=False,
                    record_id=rel.rel_id,
                    detail=f"Relationship source_doc_id '{rel.source_doc_id}' != '{source_doc_id}'",
                    severity="error",
                ))

            # Confidence must be in range
            if not (0.0 <= rel.confidence <= 1.0):
                checks.append(ValidationResult(
                    check_name="relationship_confidence_range",
                    passed=False,
                    record_id=rel.rel_id,
                    detail=f"Confidence {rel.confidence} out of range [0, 1]",
                    severity="warning",
                ))
        return checks

    # ── Topic checks ──────────────────────────────────────────────────────────

    def _check_topics(
        self,
        assignments: list,
    ) -> list[ValidationResult]:
        checks = []

        if not assignments:
            checks.append(ValidationResult(
                check_name="has_topic_assignments",
                passed=False,
                detail="No topic assignments — check topic keyword dictionary",
                severity="warning",
            ))
            return checks

        primary_count = sum(1 for a in assignments if a.is_primary)
        checks.append(ValidationResult(
            check_name="exactly_one_primary_topic",
            passed=primary_count == 1,
            detail="" if primary_count == 1
                else f"Expected 1 primary topic, found {primary_count}",
            severity="warning",
        ))
        return checks

    # ── Metadata quality checks ───────────────────────────────────────────────

    def _check_metadata_quality(
        self,
        doc: Document,
        full_text: str = "",
    ) -> list[ValidationResult]:
        """Warn when metadata that should be extractable is missing."""
        checks = []

        # Gazette present but no publication_date
        if doc.official_gazette_number and not doc.publication_date:
            checks.append(ValidationResult(
                check_name="missing_publication_date",
                passed=False,
                doc_slug=doc.doc_slug,
                detail=(
                    f"Gazette #{doc.official_gazette_number} found but "
                    "publication_date is null — check date parsing (MM/DD/YYYY?)"
                ),
                severity="warning",
            ))

        # Legal basis trigger in text but field empty
        if not doc.legal_basis_text and full_text:
            if re.search(r"بمقتضى|استناداً|بناءً على", full_text[:1500]):
                checks.append(ValidationResult(
                    check_name="missing_legal_basis_text",
                    passed=False,
                    doc_slug=doc.doc_slug,
                    detail=(
                        "Legal basis trigger found in text but "
                        "legal_basis_text field is null"
                    ),
                    severity="warning",
                ))

        return checks

    # ── UI artifact leak checks ───────────────────────────────────────────────

    def _check_section_artifact_leak(
        self,
        sections: list[Section],
    ) -> list[ValidationResult]:
        """Warn if any known LOB UI artifact strings leaked into section text."""
        from utils.arabic_utils import LOB_UI_ARTIFACTS

        checks = []
        leaking_section_id: Optional[str] = None

        for sec in sections:
            text = sec.original_text or ""
            for artifact in LOB_UI_ARTIFACTS:
                if artifact in text:
                    leaking_section_id = sec.section_id
                    break
            if leaking_section_id:
                break

        if leaking_section_id:
            checks.append(ValidationResult(
                check_name="ui_artifact_in_section_text",
                passed=False,
                record_id=leaking_section_id,
                detail=(
                    "LOB UI artifact string found in section text — "
                    "check remove_lob_artifacts() in parser"
                ),
                severity="warning",
            ))

        return checks
