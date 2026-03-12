#!/usr/bin/env python3
"""Quick check of all canonical JSON outputs."""
import json
import glob

# 1. Canonical JSON
with open("data/structured/docs/legislation-3452.json") as f:
    d = json.load(f)

doc = d["document"]
print("=== DOCUMENT ===")
print("status            :", doc.get("status"))
print("status_normalized :", doc.get("status_normalized"))
print("source_status_text:", doc.get("source_status_text"))
print("sectors type      :", type(doc.get("applicability_sectors")).__name__, "->", repr(doc.get("applicability_sectors")))
print("entities type     :", type(doc.get("applicability_entities")).__name__, "->", repr(doc.get("applicability_entities")))

ta = d["topic_assignments"][0]
print()
print("=== TOPIC ASSIGNMENT ===")
print("matched_keywords type:", type(ta.get("matched_keywords")).__name__, "->", ta.get("matched_keywords"))

# 2. Summary
print()
with open("data/structured/summaries/legislation-3452_summary.json") as f:
    s = json.load(f)
print("=== SUMMARY ===")
for k in ["doc_slug", "title_ar", "status", "status_normalized", "source_status_text",
          "primary_topic", "entity_count", "section_count", "article_count",
          "relationship_count", "has_legal_basis", "json_path", "summary_path"]:
    print(f"  {k}: {s.get(k)}")

# 3. Index
print()
with open("data/structured/documents_index.json") as f:
    idx = json.load(f)
print("=== DOCUMENTS INDEX ===")
print("count       :", idx["count"])
print("generated_at:", idx["generated_at"])
print("entry keys  :", list(idx["documents"][0].keys()))
print("entry       :", {k: v for k, v in idx["documents"][0].items() if k != "doc_id"})
