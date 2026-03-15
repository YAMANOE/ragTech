"""
tests/conftest.py
-----------------
Shared pytest fixtures for the legislative intelligence pipeline test suite.
"""
import sys
from pathlib import Path

import pytest

# Ensure project root is always on sys.path for imports
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings
from pipeline.cleaner import ArabicTextCleaner
from pipeline.parser import LOBParser
from pipeline.structurer import LegislationStructurer
from utils.arabic_utils import ArabicTextUtils as ATU


@pytest.fixture(scope="session")
def settings():
    return Settings()


@pytest.fixture(scope="session")
def parser(settings):
    return LOBParser(settings)


@pytest.fixture(scope="session")
def cleaner(settings):
    return ArabicTextCleaner(settings)


@pytest.fixture(scope="session")
def structurer(settings):
    return LegislationStructurer(settings)


@pytest.fixture(scope="session")
def type_map(settings):
    return settings.DOC_TYPE_MAP


# ── Reusable sample texts ──────────────────────────────────────────────────

ARTICLE_DIRECT_PAR = """\
المادة (1)
أ. البند الأول من المادة
ب. البند الثاني من المادة
ج. البند الثالث من المادة
"""

ARTICLE_LEAD_THEN_PAR = """\
المادة (2)
تطبق أحكام هذا القانون على النحو الآتي:
أ. الجهات الحكومية
ب. المؤسسات العامة
"""

ARTICLE_FOUR_PARAGRAPHS = """\
المادة (3)
أ. الفقرة الأولى تتعلق بالتعريفات
ب. الفقرة الثانية تتعلق بالاختصاص
ج. الفقرة الثالثة تتعلق بالإجراءات
د. الفقرة الرابعة تتعلق بالعقوبات
"""

PREAMBLE_THEN_ARTICLE = """\
قانون الخدمة المدنية رقم 10 لسنة 2020
صدر بمقتضى أحكام الدستور الأردني
المادة (1)
تعريفات وأحكام عامة تسري على موظفي الدولة
"""
