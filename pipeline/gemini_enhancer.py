"""
pipeline/gemini_enhancer.py
---------------------------
Post-processor that uses Claude (Anthropic) to re-classify structured documents
whose primary topic confidence falls below a configurable threshold.

Usage (dry-run — never modifies JSON files):
    enhancer = ClaudeEnhancer()
    report   = enhancer.run_dry(docs_dir)   # returns list[EnhancementResult]

Usage (apply changes to JSON files):
    enhancer = ClaudeEnhancer()
    report   = enhancer.run(docs_dir)       # mutates JSON in place

Environment:
    ANTHROPIC_API_KEY  — picked up automatically by the anthropic client

Thresholds:
    call_threshold   = 0.80  — only call Claude if primary confidence < this
    accept_threshold = 0.90  — only adopt Claude's answer if it returns >= this
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

# ── Valid topic IDs (mirrors config/topics.yaml) ─────────────────────────────
VALID_TOPIC_IDS: frozenset[str] = frozenset([
    "constitutional-administrative-law",
    "tax-financial-law",
    "civil-law",
    "criminal-law",
    "commercial-law",
    "labor-social-insurance",
    "health-law",
    "education-law",
    "environmental-law",
    "transport-law",
    "energy-law",
    "investment-law",
    "land-planning-law",
    "personal-status-law",
    "international-agreements-law",
    "digital-assets-law",
    "banking-financial-services",
    "agricultural-insurance-law",
])

_PROMPT_TEMPLATE = """\
أنت خبير في التشريعات الأردنية.
صنّف هذا القانون في topic واحد فقط.

العنوان: {title_ar}
النص: {first_800_chars}

اختر من هذه القائمة فقط:
constitutional-administrative-law, tax-financial-law,
civil-law, criminal-law, commercial-law,
labor-social-insurance, health-law, education-law,
environmental-law, transport-law, energy-law,
investment-law, land-planning-law, personal-status-law,
international-agreements-law, digital-assets-law,
banking-financial-services, agricultural-insurance-law

أجب بـ JSON فقط:
{{
  "topic_id": "...",
  "confidence": 0.95,
  "reasoning": "سبب بجملة وحدة"
}}"""

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class EnhancementResult:
    doc_slug:           str
    title_ar:           str
    original_topic:     str
    original_conf:      float
    claude_topic:       Optional[str]   = None
    claude_conf:        Optional[float] = None
    claude_reasoning:   Optional[str]   = None
    accepted:           bool            = False   # True if Claude result adopted
    skipped:            bool            = False   # True if conf >= call_threshold
    error:              Optional[str]   = None


# ── Main class ────────────────────────────────────────────────────────────────

class ClaudeEnhancer:
    """
    Post-processing step: re-classify low-confidence topic assignments via
    Claude (Anthropic).  Operates on the structured JSON files produced by
    the pipeline.

    Parameters
    ----------
    call_threshold:   float = 0.80  — only call Claude if primary confidence < this
    accept_threshold: float = 0.90  — only adopt Claude's answer if it returns >= this
    model:            str    Claude model name
    """

    def __init__(
        self,
        call_threshold:   float = 0.80,
        accept_threshold: float = 0.90,
        model:            str   = "claude-sonnet-4-6",
    ) -> None:
        self.call_threshold   = call_threshold
        self.accept_threshold = accept_threshold
        self.model            = model
        self._client          = self._init_client()

    # ── Client initialisation ─────────────────────────────────────────────────

    def _init_client(self):
        try:
            import anthropic  # type: ignore
            return anthropic.Anthropic()
        except ImportError as exc:
            raise ImportError(
                "anthropic is not installed. Run: pip install anthropic"
            ) from exc

    # ── Public API ────────────────────────────────────────────────────────────

    def run_dry(self, docs_dir: Path) -> list[EnhancementResult]:
        """
        Process all JSON files in *docs_dir* but DO NOT write back to disk.
        Returns the full result list for inspection/reporting.
        """
        return self._process_all(docs_dir, write=False)

    def run(self, docs_dir: Path) -> list[EnhancementResult]:
        """
        Process all JSON files in *docs_dir* and WRITE accepted changes to disk.
        Only files where Claude's answer was accepted are modified.
        """
        return self._process_all(docs_dir, write=True)

    # ── Core processing ───────────────────────────────────────────────────────

    def _process_all(
        self, docs_dir: Path, write: bool
    ) -> list[EnhancementResult]:
        json_files = sorted(docs_dir.glob("*.json"))
        logger.info(
            f"[ClaudeEnhancer] Scanning {len(json_files)} docs "
            f"(call_threshold={self.call_threshold}, write={write})"
        )

        results: list[EnhancementResult] = []
        api_calls = 0

        call_queue  = [p for p in json_files if self._needs_claude_call(p)]
        total_calls = len(call_queue)
        call_index  = 0
        logger.info(f"[ClaudeEnhancer] {total_calls} docs will be sent to Claude")

        for path in json_files:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)

            doc_slug    = raw.get("doc_slug", path.stem)
            doc         = raw.get("document", {})
            title_ar    = doc.get("title_ar", "")
            assignments = raw.get("topic_assignments", [])

            # ── Determine primary topic ───────────────────────────────────
            if not assignments:
                logger.warning(f"[ClaudeEnhancer] {doc_slug}: no topic assignments, skipping")
                continue

            primary    = max(assignments, key=lambda a: a.get("confidence", 0))
            orig_topic = primary.get("topic_id", "").replace("topic-", "")
            orig_conf  = float(primary.get("confidence", 0))

            result = EnhancementResult(
                doc_slug=doc_slug,
                title_ar=title_ar,
                original_topic=orig_topic,
                original_conf=orig_conf,
            )

            # ── Skip high-confidence docs ─────────────────────────────────
            if orig_conf >= self.call_threshold:
                result.skipped = True
                results.append(result)
                continue

            # orig_conf < call_threshold → call Claude
            call_index += 1
            print(
                f"Processing {call_index}/{total_calls} — {doc_slug}",
                flush=True,
            )
            logger.info(
                f"[ClaudeEnhancer] {doc_slug}: conf={orig_conf:.2f} "
                f"topic={orig_topic} \u2192 calling Claude"
            )

            body_text = " ".join(
                s.get("normalized_text", "") or s.get("original_text", "")
                for s in raw.get("sections", [])
            )
            first_800 = (title_ar + " " + body_text)[:800].strip()

            claude_result = self._call_claude(doc_slug, title_ar, first_800)
            api_calls += 1

            if claude_result is None:
                result.error = "Claude call failed or returned invalid JSON"
                results.append(result)
                continue

            c_topic, c_conf, c_reasoning = claude_result
            result.claude_topic     = c_topic
            result.claude_conf      = c_conf
            result.claude_reasoning = c_reasoning

            # ── Accept only if Claude is confident enough ─────────────────
            if c_conf >= self.accept_threshold and c_topic in VALID_TOPIC_IDS:
                result.accepted = True
                if write:
                    self._apply_to_json(path, raw, c_topic, c_conf, c_reasoning, primary)
                    logger.success(
                        f"[ClaudeEnhancer] {doc_slug}: accepted "
                        f"{orig_topic}({orig_conf:.2f}) \u2192 "
                        f"{c_topic}({c_conf:.2f})"
                    )
            else:
                logger.info(
                    f"[ClaudeEnhancer] {doc_slug}: Claude conf={c_conf:.2f} "
                    f"topic={c_topic} \u2014 below accept threshold, keeping original"
                )

            results.append(result)

        logger.info(
            f"[ClaudeEnhancer] Done \u2014 {len(results)} processed, "
            f"{api_calls} API calls, "
            f"{sum(1 for r in results if r.accepted)} accepted"
        )
        return results

    # ── Claude API call ───────────────────────────────────────────────────────

    def _call_claude(
        self,
        doc_slug: str,
        title_ar: str,
        first_800: str,
    ) -> Optional[tuple[str, float, str]]:
        """
        Call Claude and return (topic_id, confidence, reasoning) or None on failure.
        """
        prompt = _PROMPT_TEMPLATE.format(
            title_ar=title_ar,
            first_800_chars=first_800,
        )

        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            return self._parse_claude_response(message.content[0].text, doc_slug)
        except Exception as exc:
            logger.error(f"[ClaudeEnhancer] {doc_slug}: API error — {exc}")
            return None

    def _parse_claude_response(
        self, text: str, doc_slug: str
    ) -> Optional[tuple[str, float, str]]:
        """
        Extract topic_id, confidence, and reasoning from Claude's text response.
        Handles responses with or without markdown code fences.
        """
        cleaned = re.sub(r"```(?:json)?|```", "", text).strip()

        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            logger.warning(f"[ClaudeEnhancer] {doc_slug}: no JSON object in response")
            return None

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning(f"[ClaudeEnhancer] {doc_slug}: JSON parse error — {exc}")
            return None

        topic_id   = str(data.get("topic_id", "")).strip()
        confidence = float(data.get("confidence", 0))
        reasoning  = str(data.get("reasoning", "")).strip()

        if not topic_id:
            logger.warning(f"[ClaudeEnhancer] {doc_slug}: missing topic_id")
            return None

        # Normalise: sometimes returns "topic-xxx" form
        topic_id = topic_id.replace("topic-", "")

        return topic_id, confidence, reasoning

    # ── Apply patch to JSON ───────────────────────────────────────────────────

    def _apply_to_json(
        self,
        path: Path,
        raw: dict,
        new_topic: str,
        new_conf: float,
        reasoning: str,
        original_primary: dict,
    ) -> None:
        """
        Mutate *raw* in place and write back to *path*.
        Saves original values and marks the document as claude_enhanced.
        """
        assignments = raw.get("topic_assignments", [])

        for assignment in assignments:
            if assignment.get("is_primary"):
                # Preserve original values before overwriting
                assignment["original_topic_id"]   = assignment.get("topic_id", "")
                assignment["original_confidence"]  = assignment.get("confidence", 0)
                # Apply Claude's result
                assignment["topic_id"]   = f"topic-{new_topic}"
                assignment["confidence"] = new_conf
                assignment["claude_enhanced"]  = True
                assignment["claude_reasoning"] = reasoning
                break

        raw["claude_enhanced"] = True

        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _needs_claude_call(self, path: Path) -> bool:
        """Peek at a JSON file and return True if its primary conf < call_threshold."""
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            assignments = raw.get("topic_assignments", [])
            if not assignments:
                return False
            primary = max(assignments, key=lambda a: a.get("confidence", 0))
            conf = float(primary.get("confidence", 0))
            return conf < self.call_threshold
        except Exception:
            return False


# ── CLI entry point ───────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    import sys
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    parser = argparse.ArgumentParser(
        description="Post-process structured docs with Claude topic re-classification."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Call Claude but do NOT write results back to JSON files.",
    )
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="Only process the first N low-confidence docs (0 = no limit).",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.80, metavar="F",
        help="Call Claude for docs whose primary confidence is below this value (default: 0.80).",
    )
    parser.add_argument(
        "--docs-dir", type=Path, default=None, metavar="PATH",
        help="Path to structured docs directory (default: data/structured/docs).",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Starting Claude enhancer...")
    print("=" * 60)

    # ── API key check ─────────────────────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        masked = api_key[:8] + "..." + api_key[-4:]
        print(f"API key  : loaded ({masked})")
    else:
        print("API key  : *** MISSING — set ANTHROPIC_API_KEY environment variable ***")
        sys.exit(1)

    # ── Resolve docs dir ──────────────────────────────────────────────────────
    if args.docs_dir:
        docs_dir = args.docs_dir
    else:
        from config.settings import Settings
        docs_dir = Settings().STRUCTURED_DOCS_DIR

    if not docs_dir.exists():
        print(f"ERROR: docs dir not found: {docs_dir}")
        sys.exit(1)

    # ── Count eligible docs ───────────────────────────────────────────────────
    all_json = sorted(docs_dir.glob("*.json"))
    print(f"Docs dir : {docs_dir}")
    print(f"Total docs found : {len(all_json)}")

    eligible = []
    for p in all_json:
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            assignments = raw.get("topic_assignments", [])
            if not assignments:
                continue
            primary = max(assignments, key=lambda a: a.get("confidence", 0))
            conf = float(primary.get("confidence", 0))
            topic = primary.get("topic_id", "").replace("topic-", "")
            if conf < args.threshold:
                eligible.append((conf, p.stem, topic))
        except Exception as exc:
            print(f"  WARNING: could not read {p.name}: {exc}")

    eligible.sort()
    print(f"Docs with confidence < {args.threshold:.2f} : {len(eligible)}")

    if not eligible:
        print("\nNothing to do — all docs are above the confidence threshold.")
        return

    # ── Show eligible list ────────────────────────────────────────────────────
    limit = args.limit if args.limit > 0 else len(eligible)
    to_process = eligible[:limit]
    print(f"Will process      : {len(to_process)} docs "
          f"{'(limited by --limit)' if args.limit > 0 else ''}")
    print(f"Mode              : {'DRY RUN (no writes)' if args.dry_run else 'LIVE (will write JSON)'}")
    print(f"Model             : claude-sonnet-4-6")
    print()

    print(f"{'CONF':>5}  {'SLUG':<35} CURRENT TOPIC")
    print("-" * 80)
    for conf, slug, topic in to_process:
        print(f"{conf:>5.2f}  {slug:<35} {topic}")
    print()

    # ── Run enhancer on the limited set ──────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for _, slug, _ in to_process:
            src = docs_dir / f"{slug}.json"
            os.symlink(src, tmp_path / src.name)

        enhancer = ClaudeEnhancer(
            call_threshold=args.threshold,
            accept_threshold=0.90,
        )

        if args.dry_run:
            results = enhancer.run_dry(tmp_path)
        else:
            results = enhancer.run(tmp_path)

    # ── Results table ─────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    W = 34
    print(f"{'SLUG':<{W}} {'ORIG TOPIC':<{W}} {'OCONF':>5}  {'CLAUDE TOPIC':<{W}} {'CCONF':>5}  STATUS")
    print("-" * (W*3 + 30))
    for r in sorted(results, key=lambda x: x.original_conf):
        ctopic = (r.claude_topic or "N/A")[:W]
        cconf  = f"{r.claude_conf:.2f}" if r.claude_conf is not None else "  N/A"
        if r.skipped:
            status = "— SKIP"
        elif r.accepted:
            status = "✅ ACCEPTED"
        elif r.error:
            status = f"⚠️  ERR: {r.error[:30]}"
        else:
            status = "❌ REJECTED (low conf)"
        print(f"{r.doc_slug:<{W}} {r.original_topic:<{W}} {r.original_conf:>5.2f}  {ctopic:<{W}} {cconf:>5}  {status}")
        if r.accepted and r.claude_reasoning:
            print(f"  → {r.claude_reasoning[:90]}")

    accepted = sum(1 for r in results if r.accepted)
    errors   = sum(1 for r in results if r.error)
    print()
    print(f"Summary: {len(results)} processed | {accepted} accepted | {errors} errors")


if __name__ == "__main__":
    _main()
