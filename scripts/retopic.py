"""
scripts/retopic.py
------------------
Re-runs topic classification on all existing structured docs
using the current config/topics.yaml, without re-fetching or re-parsing.

Reads:  data/clean/{slug}_clean.json      (for normalized_text)
        data/structured/docs/{slug}.json  (for title_ar, doc_id)
Writes: data/structured/docs/{slug}.json  (updates topic_assignments in-place)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from utils.arabic_utils import ArabicTextUtils as ATU
from utils.id_generator import IDGenerator as IDG
from config.settings import Settings

settings = Settings()
THRESHOLD = settings.TOPIC_CONFIDENCE_THRESHOLD  # 0.40

# ── Load topics ───────────────────────────────────────────────────────────────
with open("config/topics.yaml", encoding="utf-8") as f:
    topics: list[dict] = yaml.safe_load(f)["topics"]

clean_dir      = Path("data/clean")
structured_dir = Path("data/structured/docs")

covered = 0
total   = 0
changed = 0

for sf in sorted(structured_dir.glob("*.json")):
    slug = sf.stem
    cf   = clean_dir / f"{slug}_clean.json"
    if not cf.exists():
        continue

    s_data = json.loads(sf.read_text("utf-8"))
    c_data = json.loads(cf.read_text("utf-8"))

    title_ar       = s_data["document"].get("title_ar") or ""
    normalized_text = c_data.get("normalized_text") or ""
    doc_id         = s_data["document"]["doc_id"]

    title_norm = ATU.normalize(title_ar)
    early_norm = ATU.normalize(normalized_text[:800])
    body_norm  = ATU.normalize(normalized_text)

    scored: list[tuple[float, dict]] = []
    for td in topics:
        keywords: list[str] = td.get("keywords_ar", [])
        if not keywords:
            continue
        confidence = 0.0
        for kw in keywords:
            nkw = ATU.normalize(kw)
            if nkw in title_norm:
                confidence += 0.40
            elif nkw in early_norm:
                confidence += 0.20
            elif nkw in body_norm:
                confidence += 0.05
        confidence = round(min(1.0, confidence), 3)
        if confidence >= THRESHOLD:
            scored.append((confidence, td))

    scored.sort(key=lambda x: x[0], reverse=True)

    assignments = []
    for i, (conf, td) in enumerate(scored):
        t_slug = td["id"]
        t_id   = IDG.topic_id(t_slug)
        assignments.append({
            "assignment_id":   IDG.topic_assignment_id(doc_id, t_id),
            "doc_id":          doc_id,
            "topic_id":        t_id,
            "topic_slug":      t_slug,
            "topic_name_ar":   td.get("name_ar", ""),
            "topic_name_en":   td.get("name_en", ""),
            "is_primary":      (i == 0),
            "confidence":      conf,
            "extraction_method": "keyword",
            "matched_keywords": [
                kw for kw in td.get("keywords_ar", [])
                if ATU.normalize(kw) in body_norm
            ],
        })

    # Also update the primary topic fields inside document
    primary = assignments[0] if assignments else None
    s_data["document"]["primary_topic"]            = primary["topic_id"]    if primary else None
    s_data["document"]["primary_topic_ar"]         = primary["topic_name_ar"] if primary else None
    s_data["document"]["primary_topic_confidence"] = primary["confidence"]  if primary else None

    old_count = len(s_data.get("topic_assignments", []))
    s_data["topic_assignments"] = assignments
    sf.write_text(json.dumps(s_data, ensure_ascii=False, indent=2), encoding="utf-8")

    total += 1
    if assignments:
        covered += 1
    if len(assignments) != old_count:
        changed += 1

print(f"Processed: {total} laws")
print(f"Covered:   {covered} / {total}  ({covered/total*100:.1f}%)")
print(f"Changed:   {changed} (topic_assignments count differs from before)")
print()

# Show distribution
from collections import Counter
topic_dist: Counter = Counter()
for sf in sorted(structured_dir.glob("*.json")):
    s = json.loads(sf.read_text("utf-8"))
    primary = next((a for a in s.get("topic_assignments", []) if a.get("is_primary")), None)
    if primary:
        topic_dist[primary["topic_slug"]] += 1
    else:
        topic_dist["(none)"] += 1

print("Primary topic distribution:")
for slug, cnt in topic_dist.most_common():
    print(f"  {cnt:3d}  {slug}")
