#!/usr/bin/env python3
"""Quick smoke test for run_first_100 module-level logic."""
import sys
sys.path.insert(0, ".")

import importlib.util
spec = importlib.util.spec_from_file_location("run_first_100", "scripts/run_first_100.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# _make_slug
item1 = {"doc_type_en": "law",        "issue_year": 2025, "doc_number": "5",  "legislation_id": "3452"}
item2 = {"doc_type_en": "",           "issue_year": None, "doc_number": None, "legislation_id": "3452"}
item3 = {"doc_type_en": "regulation", "issue_year": 2020, "doc_number": "45", "legislation_id": "999"}
assert mod._make_slug(item1) == "law-2025-5",         repr(mod._make_slug(item1))
assert mod._make_slug(item2) == "legislation-3452",   repr(mod._make_slug(item2))
assert mod._make_slug(item3) == "regulation-2020-45", repr(mod._make_slug(item3))
print("_make_slug OK:", mod._make_slug(item1), mod._make_slug(item2), mod._make_slug(item3))

# _item_from_href
href  = "#!/LegislationDetails?LegislationID=3452&LegislationType=2&isMod=false"
title = "قانون صندوق التكافل للحد من المخاطر الزراعية رقم 5 لسنة 2025"
item  = mod._item_from_href(href, title)
assert item is not None
assert item["legislation_id"]      == "3452"
assert item["legislation_type_id"] == 2
assert item["doc_type_en"]         == "law"
assert item["issue_year"]          == 2025
assert item["doc_number"]          == "5"
assert item["doc_slug"]            == "law-2025-5"
print("_item_from_href OK:", item["doc_slug"], item["doc_type_en"], item["issue_year"])

# Settings paths
from config.settings import Settings
s = Settings()
assert str(s.INDEXES_DIR).endswith("data/indexes")
assert str(s.REPORTS_DIR).endswith("data/reports")
print("Settings OK:", s.INDEXES_DIR, s.REPORTS_DIR)

# _pct
assert mod._pct(1, 4)   == 25.0
assert mod._pct(0, 0)   == 0.0
assert mod._pct(3, 3)   == 100.0
print("_pct OK")

print("\nAll smoke tests PASSED")
