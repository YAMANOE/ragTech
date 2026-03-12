#!/usr/bin/env python3
"""Quick check of latest structured JSON output."""
import json
import glob

files = sorted(glob.glob("data/structured/docs/*.json"))
if not files:
    print("no json files found")
    raise SystemExit(1)

with open(files[-1]) as f:
    d = json.load(f)

doc = d.get("document", {})
print("publication_date :", doc.get("publication_date"))
print("legal_basis_text :", doc.get("legal_basis_text"))
print("status           :", doc.get("status"))

print("\n--- Topics ---")
for t in d.get("topic_assignments", []):
    print(f"  {t.get('topic_id')}  conf={t.get('confidence')}  primary={t.get('is_primary')}")

print("\n--- Relationships ---")
for r in d.get("relationships", []):
    print(f"  {r.get('rel_type')}  target={r.get('target_slug')}  conf={r.get('confidence')}")

print("\n--- Entities ---")
for e in d.get("entities", []):
    print(f"  [{e.get('entity_type')}] {e.get('entity_name')}")

print("\n--- Section texts (first 120 chars) ---")
for s in d.get("sections", [])[:10]:
    txt = (s.get("original_text") or "")[:120].replace("\n", " ")
    print(f"  [{s['section_type']}] {txt}")
