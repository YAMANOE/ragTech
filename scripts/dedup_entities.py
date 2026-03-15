"""
Entity deduplication — safe merges only.

Strategy:
  - SAFE_MERGES: explicit old_name → canonical_name mapping.
    Only includes clusters where all members are demonstrably the
    same entity (hamza/alef spelling variants, clear OCR typos,
    grammatical case differences on the same word).
  - SKIPPED clusters: any group where members are distinct entities
    (e.g., وزارة الداخلية ≠ وزارة المالية, محكمة البداية ≠ محكمة البلدية).

For each changed entity the script recomputes entity_slug and entity_id
(both derived from entity_name_ar via IDGenerator) and patches:
  - doc["entities"]          entity_id / entity_slug / entity_name_ar
  - doc["entity_roles"]      entity_id / entity_name_ar / role_id
  - doc["document"]          issuing_entity_id / issuing_entity_name_ar
Within-document deduplication is applied when both old and canonical
already appear in the same JSON.
"""
import sys, json, copy
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parent.parent   # project root
sys.path.insert(0, str(ROOT))

from utils.id_generator import IDGenerator as IDG

STRUCT_DIR = ROOT / "data" / "structured" / "docs"

# ── Safe merge table ────────────────────────────────────────────────────────
# key   = entity_name_ar to replace
# value = canonical entity_name_ar to keep
SAFE_MERGES: dict[str, str] = {
    # ── Cluster 4: مجلس الإدارة variants ──────────────────────────────────
    "مجلس ادارة":   "مجلس الإدارة",
    "مجلس إدارة":   "مجلس الإدارة",
    "مجلس الادارة": "مجلس الإدارة",
    "مجلس الاداره": "مجلس الإدارة",

    # ── Cluster 7 (partial): hamza fix for التأديب only ───────────────────
    "مجلس التاديب": "مجلس التأديب",

    # ── Cluster 12: دائرة أخرى ──────────────────────────────────────────
    "دائرة اخرى":   "دائرة أخرى",

    # ── Cluster 13: دائرة الأحوال المدنية والجوازات ───────────────────────
    "دائرة الاحوال المدنية والجوازات": "دائرة الأحوال المدنية والجوازات",

    # ── Cluster 14: دائرة الأراضي والمساحة ────────────────────────────────
    "دائرة الاراضي والمساحة": "دائرة الأراضي والمساحة",

    # ── Cluster 16: سلطة المصادر الطبيعية (ة vs ه) ────────────────────────
    "سلطة المصادر الطبيعيه": "سلطة المصادر الطبيعية",

    # ── Cluster 17: مجلس إدارة الفرع ─────────────────────────────────────
    "مجلس ادارة الفرع": "مجلس إدارة الفرع",

    # ── Cluster 19: مجلس الأعيان ──────────────────────────────────────────
    "مجلس الاعيان": "مجلس الأعيان",

    # ── Cluster 20: مجلس الأمناء ──────────────────────────────────────────
    "مجلس الامناء": "مجلس الأمناء",
    "مجلس امناء":   "مجلس الأمناء",

    # ── Cluster 21: مجلس التأديب الأعلى ──────────────────────────────────
    "مجلس التأديب الاعلى": "مجلس التأديب الأعلى",

    # ── Cluster 25: مجلس الوزراء typo (وزارء) ────────────────────────────
    "مجلس الوزارء": "مجلس الوزراء",

    # ── Cluster 26: محكمة أخرى ────────────────────────────────────────────
    "محكمة اخرى":   "محكمة أخرى",

    # ── Cluster 27: محكمة الاستئناف (generic) ─────────────────────────────
    "محكمة استئناف": "محكمة الاستئناف",

    # ── Cluster 29: محكمة الجمارك ─────────────────────────────────────────
    "محكمة جمارك":  "محكمة الجمارك",

    # ── Cluster 30: محكمة الجمارك البدائية ────────────────────────────────
    "محكمة جمارك البدائية": "محكمة الجمارك البدائية",

    # ── Cluster 31: هيئة أهلية ────────────────────────────────────────────
    "هيئة اهلية":   "هيئة أهلية",

    # ── Cluster 32: هيئة الأوراق المالية ──────────────────────────────────
    "هيئة الاوراق المالية": "هيئة الأوراق المالية",

    # ── Cluster 33: هيئة التدريس ──────────────────────────────────────────
    "هيئة تدريس":   "هيئة التدريس",

    # ── Cluster 34: grammatical case (ون vs ين) ───────────────────────────
    "هيئة التدريس والمحاضرين": "هيئة التدريس والمحاضرون",

    # ── Cluster 35: هيئة النيابة ──────────────────────────────────────────
    "هيئة نيابة":   "هيئة النيابة",

    # ── Cluster 36: وزارة الأشغال العامة والإسكان ─────────────────────────
    "وزارة الاشغال العامة والاسكان": "وزارة الأشغال العامة والإسكان",

    # ── Cluster 37: وزارة الأوقاف والشؤون والمقدسات ──────────────────────
    "وزارة الاوقاف والشؤون والمقدسات": "وزارة الأوقاف والشؤون والمقدسات",

    # ── Cluster 2 (partial): OCR typo ادرة → ادارة ──────────────────────
    "مجلس ادرة الشركة المساهمة": "مجلس ادارة الشركة المساهمة",
}

# Clusters intentionally SKIPPED (different entities, not just spelling):
# Cluster 1  – مجلس الأمة / مجلس الأمانة / مجلس الجامعة
# Cluster 2  – different company-type boards (except typo above)
# Cluster 3  – specialized appeal courts (Customs, Tax, Religious…)
# Cluster 5  – مجلس ادارة البورصة ≠ مجلس ادارة الشركة
# Cluster 6  – مجلس ادارة السلطة ≠ مجلس ادارة المؤسسة
# Cluster 7  – مجلس تأديبي treated as separate from مجلس التأديب
# Cluster 8  – مجلس الوزراء المساهمة etc. (trailing context noise)
# Cluster 9  – محكمة الصلح ≠ محكمة صلحية (different court levels)
# Cluster 10 – مديرية الأمن العام ≠ مديرية الدين العام
# Cluster 11 – هيئة المديرين ≠ هيئة مديرين (company vs group)
# Cluster 15 – دائرة vs مديرية مراقبة الشركات (formal name ambiguity)
# Cluster 18 – الاردنية ≠ الرئيس
# Cluster 22 – مجلس التربية والتعليم المؤلف (trailing noise)
# Cluster 23 – مجلس التموين ≠ مجلس المفوضين
# Cluster 24 – مجلس النقابة ≠ مجلس النواب
# Cluster 28 – محكمة البداية ≠ محكمة البلدية
# Cluster 38 – وزارة التربية والتعليم الوزير (trailing noise)
# Cluster 39 – وزارة الداخلية ≠ وزارة المالية
# Cluster 40 – وزارة العدل ≠ وزارة العمل

# ── NS for role_id recomputation ──────────────────────────────────────────
_NS_ENTITY = uuid.UUID("6ba7b813-9dad-11d1-80b4-00c04fd430c8")


def new_entity_id(canonical: str, etype: str) -> tuple[str, str]:
    """Return (entity_slug, entity_id) for the canonical name."""
    slug = IDG.entity_slug(canonical, etype)
    eid  = IDG.entity_id(slug)
    return slug, eid


def new_role_id(doc_id: str, entity_id: str, role: str) -> str:
    return IDG.entity_role_id(doc_id, entity_id, role)


# ── Process all JSON files ─────────────────────────────────────────────────

json_paths = sorted(STRUCT_DIR.glob("*.json"))
total_files  = len(json_paths)
changed_files = 0
total_entity_replacements = 0

before_names: set[str] = set()
after_names:  set[str] = set()

for jp in json_paths:
    with open(jp, encoding="utf-8") as f:
        data = json.load(f)

    # Collect before
    for ent in data.get("entities", []):
        before_names.add(ent.get("entity_name_ar", ""))

    # ── Build per-doc mapping: old_entity_id → (new_entity_id, new_entity_slug, canonical_name) ──
    old_to_new: dict[str, tuple] = {}  # old entity_id → (new_eid, new_slug, canonical_name)

    orig_entities = data.get("entities", [])
    new_entities  = []
    seen_eids: set[str] = set()   # canonical entity_ids already added in this doc

    for ent in orig_entities:
        ar_name = ent.get("entity_name_ar", "")
        canonical = SAFE_MERGES.get(ar_name)

        if canonical is not None:
            # Compute new IDs
            etype = ent.get("entity_type", "")
            new_slug, new_eid = new_entity_id(canonical, etype)

            old_to_new[ent["entity_id"]] = (new_eid, new_slug, canonical)

            # Add canonical entity if not already in this doc
            if new_eid not in seen_eids:
                seen_eids.add(new_eid)
                new_entities.append({
                    "entity_id":      new_eid,
                    "entity_slug":    new_slug,
                    "entity_name_ar": canonical,
                    "entity_type":    etype,
                })
            total_entity_replacements += 1
        else:
            # Keep as-is; deduplicate within this doc
            eid = ent["entity_id"]
            if eid not in seen_eids:
                seen_eids.add(eid)
                new_entities.append(ent)

    # ── Fix entity_roles ───────────────────────────────────────────────────
    doc_id = data.get("document", {}).get("doc_id", "")
    new_roles = []
    seen_role_ids: set[str] = set()

    for role in data.get("entity_roles", []):
        old_eid = role.get("entity_id", "")
        if old_eid in old_to_new:
            new_eid, _, canonical = old_to_new[old_eid]
            new_rid = new_role_id(doc_id, new_eid, role.get("role", ""))
            new_role = dict(role)
            new_role["entity_id"]      = new_eid
            new_role["entity_name_ar"] = canonical
            new_role["role_id"]        = new_rid
            if new_rid not in seen_role_ids:
                seen_role_ids.add(new_rid)
                new_roles.append(new_role)
        else:
            rid = role.get("role_id", "")
            if rid not in seen_role_ids:
                seen_role_ids.add(rid)
                new_roles.append(role)

    # ── Fix document.issuing_entity_id ────────────────────────────────────
    doc = data.get("document", {})
    issuing_old = doc.get("issuing_entity_id", "")
    if issuing_old in old_to_new:
        new_eid, _, canonical = old_to_new[issuing_old]
        doc["issuing_entity_id"]      = new_eid
        doc["issuing_entity_name_ar"] = canonical

    # ── Check if anything changed ─────────────────────────────────────────
    if old_to_new:
        data["entities"]     = new_entities
        data["entity_roles"] = new_roles
        data["document"]     = doc
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        changed_files += 1

    # Collect after
    for ent in data.get("entities", []):
        after_names.add(ent.get("entity_name_ar", ""))


# ── Summary ────────────────────────────────────────────────────────────────
print(f"JSON files processed : {total_files}")
print(f"JSON files changed   : {changed_files}")
print(f"Entity replacements  : {total_entity_replacements}")
print()
print(f"Before unique names  : {len(before_names)}")
print(f"After unique names   : {len(after_names)}")
print(f"Net reduction        : {len(before_names) - len(after_names)}")
print()
print("Merged (removed) names:")
for name in sorted(before_names - after_names):
    canonical = SAFE_MERGES.get(name, "?")
    print(f"  {name!r}  →  {canonical!r}")
