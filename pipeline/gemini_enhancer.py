"""
pipeline/gemini_enhancer.py
---------------------------
Post-processor that uses Gemini to re-classify structured documents whose
primary topic confidence falls below a configurable threshold.

Usage (dry-run — never modifies JSON files):
    enhancer = GeminiEnhancer()
    report   = enhancer.run_dry(docs_dir)   # returns list[EnhancementResult]

Usage (apply changes to JSON files):
    enhancer = GeminiEnhancer()
    report   = enhancer.run(docs_dir)       # mutates JSON in place

Environment:
    GEMINI_API_KEY  — required; no fallback
    GEMINI_MODEL    — optional, defaults to "gemini-2.0-flash"

Thresholds:
    call_threshold   = 0.80  — only call Gemini if primary confidence < this
    skip_threshold   = 0.85  — docs at or above this are never sent to Gemini
    accept_threshold = 0.90  — only adopt Gemini's answer if it returns >= this
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
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

_TOPIC_LIST_AR = "\n".join(f"- {t}" for t in sorted(VALID_TOPIC_IDS))

_PROMPT_TEMPLATE = """\
أنت خبير في التشريعات الأردنية.
صنّف هذا القانون بدقة عالية.

العنوان: {title_ar}
النص: {first_800_chars}

اختر topic_id واحد فقط من هذه القائمة:
{topic_list}

أجب بـ JSON فقط:
{{
  "topic_id": "...",
  "confidence": 0.95,
  "reasoning": "سبب التصنيف"
}}"""

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class EnhancementResult:
    doc_slug:           str
    title_ar:           str
    original_topic:     str
    original_conf:      float
    gemini_topic:       Optional[str]   = None
    gemini_conf:        Optional[float] = None
    gemini_reasoning:   Optional[str]   = None
    accepted:           bool            = False   # True if Gemini result adopted
    skipped:            bool            = False   # True if conf >= skip_threshold
    error:              Optional[str]   = None


# ── Main class ────────────────────────────────────────────────────────────────

class GeminiEnhancer:
    """
    Post-processing step: re-classify low-confidence topic assignments via
    Gemini.  Operates on the structured JSON files produced by the pipeline.

    Parameters
    ----------
    call_threshold:   float = 0.90  — only call Gemini if primary confidence < this
    skip_threshold:   float = 0.95  — docs at or above this are never sent to Gemini
    accept_threshold: float = 0.90  — only adopt Gemini's answer if it returns >= this
    model:            str    Gemini model name (env GEMINI_MODEL overrides)
    requests_per_min: int    rate-limit guard (sleep between calls)
    """

    call_threshold:   float = 0.80
    skip_threshold:   float = 0.85
    accept_threshold: float = 0.90

    def __init__(
        self,
        call_threshold:   float = 0.80,
        skip_threshold:   float = 0.85,
        accept_threshold: float = 0.90,
        model:            str   = "gemini-2.0-flash",
        requests_per_min: int   = 14,
    ) -> None:
        self.call_threshold   = call_threshold
        self.skip_threshold   = skip_threshold
        self.accept_threshold = accept_threshold
        self.model            = os.getenv("GEMINI_MODEL", model)
        self._rpm             = requests_per_min
        self._client          = self._init_client()

    # ── Client initialisation ─────────────────────────────────────────────────

    def _init_client(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY environment variable is not set. "
                "Export it before running GeminiEnhancer."
            )
        try:
            from google import genai  # type: ignore
            return genai.Client(api_key=api_key)
        except ImportError as exc:
            raise ImportError(
                "google-genai is not installed. Run: pip install google-genai"
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
        Only files where Gemini's answer was accepted are modified.
        """
        return self._process_all(docs_dir, write=True)

    # ── Core processing ───────────────────────────────────────────────────────

    def _process_all(
        self, docs_dir: Path, write: bool
    ) -> list[EnhancementResult]:
        json_files = sorted(docs_dir.glob("*.json"))
        logger.info(
            f"[GeminiEnhancer] Scanning {len(json_files)} docs "
            f"(call_threshold={self.call_threshold}, "
            f"skip_threshold={self.skip_threshold}, write={write})"
        )

        results: list[EnhancementResult] = []
        api_calls = 0

        # Pre-count how many docs will actually need a Gemini call so the
        # progress counter shows a meaningful denominator.
        call_queue  = [p for p in json_files if self._needs_gemini_call(p)]
        total_calls = len(call_queue)
        call_index  = 0
        logger.info(f"[GeminiEnhancer] {total_calls} docs will be sent to Gemini")

        for path in json_files:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)

            doc_slug   = raw.get("doc_slug", path.stem)
            doc        = raw.get("document", {})
            title_ar   = doc.get("title_ar", "")
            assignments = raw.get("topic_assignments", [])

            # ── Determine primary topic ───────────────────────────────────
            if not assignments:
                logger.warning(f"[GeminiEnhancer] {doc_slug}: no topic assignments, skipping")
                continue

            primary = max(assignments, key=lambda a: a.get("confidence", 0))
            orig_topic = primary.get("topic_id", "").replace("topic-", "")
            orig_conf  = float(primary.get("confidence", 0))

            result = EnhancementResult(
                doc_slug=doc_slug,
                title_ar=title_ar,
                original_topic=orig_topic,
                original_conf=orig_conf,
            )

            # ── Skip if already high-confidence ──────────────────────────
            if orig_conf >= self.skip_threshold:
                result.skipped = True
                results.append(result)
                continue

            # ── Only call Gemini for low-confidence docs ──────────────────
            if orig_conf >= self.call_threshold:
                # Between call_threshold and skip_threshold: skip without call
                result.skipped = True
                results.append(result)
                continue

            # orig_conf < call_threshold → call Gemini
            call_index += 1
            batch_num   = (call_index - 1) // 10 + 1
            print(
                f"Processing {call_index}/{total_calls} "
                f"(batch {batch_num}) — {doc_slug} — waiting 4s...",
                flush=True,
            )
            logger.info(
                f"[GeminiEnhancer] {doc_slug}: conf={orig_conf:.2f} "
                f"topic={orig_topic} → calling Gemini"
            )

            body_text = " ".join(
                s.get("normalized_text", "") or s.get("original_text", "")
                for s in raw.get("sections", [])
            )
            first_800 = (title_ar + " " + body_text)[:800].strip()

            gemini_result = self._call_gemini(doc_slug, title_ar, first_800)
            api_calls += 1

            if gemini_result is None:
                result.error = "Gemini call failed or returned invalid JSON"
                results.append(result)
                self._rate_limit_sleep()
                continue

            g_topic, g_conf, g_reasoning = gemini_result
            result.gemini_topic     = g_topic
            result.gemini_conf      = g_conf
            result.gemini_reasoning = g_reasoning

            # ── Accept only if Gemini is confident enough ─────────────────
            if g_conf >= self.accept_threshold and g_topic in VALID_TOPIC_IDS:
                result.accepted = True
                if write:
                    self._apply_to_json(path, raw, g_topic, g_conf, g_reasoning, primary)
                    logger.success(
                        f"[GeminiEnhancer] {doc_slug}: accepted "
                        f"{orig_topic}({orig_conf:.2f}) → "
                        f"{g_topic}({g_conf:.2f})"
                    )
            else:
                logger.info(
                    f"[GeminiEnhancer] {doc_slug}: Gemini conf={g_conf:.2f} "
                    f"topic={g_topic} — below accept threshold, keeping original"
                )

            results.append(result)

            # 4s between every call; 30s pause after every 10th call
            self._rate_limit_sleep()
            if call_index % 10 == 0 and call_index < total_calls:
                print(
                    f"Batch {batch_num} complete — pausing 30s before next batch...",
                    flush=True,
                )
                time.sleep(30)

        logger.info(
            f"[GeminiEnhancer] Done — {len(results)} processed, "
            f"{api_calls} API calls, "
            f"{sum(1 for r in results if r.accepted)} accepted"
        )
        return results

    # ── Gemini API call ───────────────────────────────────────────────────────

    def _call_gemini(
        self,
        doc_slug: str,
        title_ar: str,
        first_800: str,
    ) -> Optional[tuple[str, float, str]]:
        """
        Call Gemini and return (topic_id, confidence, reasoning) or None on failure.
        """
        prompt = _PROMPT_TEMPLATE.format(
            title_ar=title_ar,
            first_800_chars=first_800,
            topic_list=_TOPIC_LIST_AR,
        )

        max_retries = 2
        for attempt in range(1, max_retries + 2):  # 3 total attempts
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                )
                return self._parse_gemini_response(response.text, doc_slug)
            except Exception as exc:
                exc_str = str(exc)
                if "RESOURCE_EXHAUSTED" in exc_str or "429" in exc_str:
                    if attempt <= max_retries:
                        wait = 20 * attempt  # 20s on first retry, 40s on second
                        logger.warning(
                            f"[GeminiEnhancer] {doc_slug}: rate-limited "
                            f"(attempt {attempt}/{max_retries}) — retrying in {wait}s"
                        )
                        print(f"  ⚠️  Rate limited — waiting {wait}s then retrying...", flush=True)
                        time.sleep(wait)
                        continue
                logger.error(f"[GeminiEnhancer] {doc_slug}: API error — {exc}")
                return None
        logger.error(f"[GeminiEnhancer] {doc_slug}: exhausted all retries")
        return None

    def _parse_gemini_response(
        self, text: str, doc_slug: str
    ) -> Optional[tuple[str, float, str]]:
        """
        Extract topic_id, confidence, and reasoning from Gemini's text response.
        Handles responses with or without markdown code fences.
        """
        # Strip markdown fences if present
        cleaned = re.sub(r"```(?:json)?|```", "", text).strip()

        # Attempt to find JSON object in the response
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            logger.warning(f"[GeminiEnhancer] {doc_slug}: no JSON object in response")
            return None

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning(f"[GeminiEnhancer] {doc_slug}: JSON parse error — {exc}")
            return None

        topic_id   = str(data.get("topic_id", "")).strip()
        confidence = float(data.get("confidence", 0))
        reasoning  = str(data.get("reasoning", "")).strip()

        if not topic_id:
            logger.warning(f"[GeminiEnhancer] {doc_slug}: missing topic_id")
            return None

        # Normalise: Gemini sometimes returns "topic-xxx" form
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
        Saves original values and marks the document as gemini_enhanced.
        """
        assignments = raw.get("topic_assignments", [])

        for assignment in assignments:
            if assignment.get("is_primary"):
                # Preserve original values before overwriting
                assignment["original_topic_id"]   = assignment.get("topic_id", "")
                assignment["original_confidence"]  = assignment.get("confidence", 0)
                # Apply Gemini's result
                assignment["topic_id"]   = f"topic-{new_topic}"
                assignment["confidence"] = new_conf
                assignment["gemini_enhanced"]  = True
                assignment["gemini_reasoning"] = reasoning
                break

        raw["gemini_enhanced"] = True

        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _needs_gemini_call(self, path: Path) -> bool:
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

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def _rate_limit_sleep(self) -> None:
        """Fixed 4-second gap between every API call."""
        time.sleep(4)
