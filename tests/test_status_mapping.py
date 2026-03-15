"""
tests/test_status_mapping.py
-----------------------------
Tests for the _AR_TO_EN_STATUS dictionary in scripts/run_first_100.py.

Covers all 20 Arabic status values and the unknown-value fallback.
The mapping is imported directly so the test stays fast and deterministic.
"""
import importlib
import sys
from pathlib import Path

import pytest

# ── Import the mapping ─────────────────────────────────────────────────────
# run_first_100 is a script (has __main__ guard) but the dict is module-level.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_mod = importlib.import_module("scripts.run_first_100")
AR_TO_EN_STATUS: dict[str, str] = _mod._AR_TO_EN_STATUS


def map_status(ar: str, default: str = "unknown") -> str:
    """Helper that mirrors the runtime lookup: AR → EN, fallback to default."""
    return AR_TO_EN_STATUS.get(ar, default)


# ── Tests ──────────────────────────────────────────────────────────────────

class TestActiveStatuses:
    """Values that should normalise to 'active'."""

    def test_nafiz(self):
        assert map_status("نافذ") == "active"

    def test_nafiza(self):
        assert map_status("نافذة") == "active"

    def test_sari(self):
        assert map_status("ساري") == "active"

    def test_sariya(self):
        assert map_status("سارية") == "active"


class TestRepealedStatuses:
    """Values that should normalise to 'repealed'."""

    def test_mulgha_with_alef_maqsura(self):
        assert map_status("ملغى") == "repealed"

    def test_mulgi_without_alef_maqsura(self):
        # Different spelling seen in some LOB records
        assert map_status("ملغي") == "repealed"

    def test_mulgiya(self):
        assert map_status("ملغية") == "repealed"

    def test_ghair_sari(self):
        # This was the bug: previously mapped to 'active' — now must be 'repealed'
        assert map_status("غير ساري") == "repealed", (
            "'غير ساري' must map to 'repealed', not 'active'"
        )

    def test_ghair_sariya(self):
        assert map_status("غير سارية") == "repealed"

    def test_muntahi(self):
        assert map_status("منتهي") == "repealed"

    def test_muntahiya(self):
        assert map_status("منتهية") == "repealed"


class TestAmendedStatuses:
    """Values that should normalise to 'amended'."""

    def test_muaddal_with_shadda(self):
        assert map_status("معدّل") == "amended"

    def test_muaddal_without_shadda(self):
        assert map_status("معدل") == "amended"

    def test_muaddala_with_shadda(self):
        assert map_status("معدّلة") == "amended"

    def test_muaddala_without_shadda(self):
        assert map_status("معدلة") == "amended"


class TestSuspendedStatuses:
    """Values that should normalise to 'suspended'."""

    def test_mawquf(self):
        assert map_status("موقوف") == "suspended"

    def test_mawqufa(self):
        assert map_status("موقوفة") == "suspended"


class TestDraftStatuses:
    """Values that should normalise to 'draft'."""

    def test_muaqat(self):
        assert map_status("مؤقت") == "draft"

    def test_muaqata(self):
        assert map_status("مؤقتة") == "draft"


class TestUnknownFallback:
    """Unknown Arabic value should fall back to the caller-provided default."""

    def test_unknown_value_returns_unknown(self):
        assert map_status("قيد الدراسة") == "unknown"

    def test_empty_string_returns_unknown(self):
        assert map_status("") == "unknown"

    def test_english_string_returns_unknown(self):
        assert map_status("active") == "unknown"
