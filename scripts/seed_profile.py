"""One-shot Profile seeding script.  Run once to populate the initial entities and
expressions from known retrieval failure patterns.  Not idempotent (applies patches
via ON CONFLICT upsert behaviour)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.core.gtm_profile import GTMProfileRepository

repo = GTMProfileRepository()

ENTITIES: list[dict] = [
    {"name": "Hypershell", "kind": "brand"},
    {"name": "nubia", "kind": "brand"},
    {"name": "REDMAGIC", "kind": "brand"},
    {"name": "Z80 Ultra", "kind": "product"},
    {"name": "Hypershell X Pro", "kind": "product"},
    {"name": "Hypershell X Ultra S", "kind": "product"},
]

ALIASES: list[tuple[str, str]] = [
    ("Hypershell", "HYPERSHELL"),
    ("Hypershell", "hypershell"),
    ("nubia", "Nubia"),
    ("REDMAGIC", "RedMagic"),
    ("REDMAGIC", "redmagic"),
    ("Z80 Ultra", "Z80U"),
    ("Z80 Ultra", "Z80 U"),
    ("Hypershell X Pro", "X Pro"),
    ("Hypershell X Pro", "HypershellXPro"),
    ("Hypershell X Ultra S", "Ultra S"),
    ("Hypershell X Ultra S", "X Ultra S"),
]

EXPRESSIONS: list[dict] = [
    # Known-failure patterns
    {"name": "库存", "variants": "inventory|stock|DOS"},
    {"name": "品牌词", "variants": "brand keyword|brand term|branded keyword"},
    {"name": "转化漏斗", "variants": "conversion funnel|funnel"},
    {"name": "联盟营销", "variants": "affiliate|Affiliate|affiliate marketing"},
    {"name": "加购", "variants": "add to cart|ATC|add-to-cart"},
    # General-purpose cross-language pairs
    {"name": "折扣", "variants": "discount|promotion|coupon"},
    {"name": "流量", "variants": "traffic|session|visit"},
    {"name": "销量", "variants": "sales|sales volume|revenue"},
    {"name": "ROAS", "variants": "return on ad spend|广告支出回报率"},
    {"name": "SEM", "variants": "search engine marketing|付费搜索|paid search"},
    {"name": "落地页", "variants": "landing page|LP|PDP"},
]

entity_ids: dict[str, int] = {}

print("=== Seeding entities ===")
for ent in ENTITIES:
    ok = repo.apply_patch(
        {"action": "add_entity", "type": "entity", "name": ent["name"], "kind": ent["kind"]},
        trace_supported=True,
    )
    print(f"  {'OK' if ok else 'FAIL'} entity: {ent['name']}  ({ent['kind']})")

# Resolve entity IDs
with repo._connect() as db:
    for row in db.execute("SELECT id, normalized FROM entity WHERE status='active'"):
        for ent in ENTITIES:
            from tools.core.gtm_profile import normalize
            if normalize(ent["name"]) == row[1]:
                entity_ids[ent["name"]] = row[0]
                break

print()
print("=== Seeding aliases ===")
for entity_name, alias in ALIASES:
    eid = entity_ids.get(entity_name)
    if eid is None:
        print(f"  SKIP  alias: {alias}  (entity '{entity_name}' not found)")
        continue
    ok = repo.apply_patch(
        {"action": "add_alias", "type": "alias", "entity_id": eid, "alias": alias},
        trace_supported=True,
    )
    print(f"  {'OK' if ok else 'FAIL'} alias: {alias}  →  {entity_name}")

print()
print("=== Seeding expressions ===")
for expr in EXPRESSIONS:
    ok = repo.apply_patch(
        {"action": "add_expression", "type": "expression",
         "name": expr["name"], "variants": expr["variants"]},
        trace_supported=True,
    )
    print(f"  {'OK' if ok else 'FAIL'} expr: {expr['name']}  variants={expr['variants']}")

# ── Verification ──────────────────────────────────────────────────────
from tools.core.models import ProfileRerankConfig
cfg = ProfileRerankConfig()

print()
print("=== Verification ===")
for test_query in ["Z80 Ultra Meta budget", "Hypershell 库存 DOS", "Hypershell X Pro inventory"]:
    resolution = repo.resolve(test_query, cfg)
    entities = resolution.exact_entity_matches
    expressions = resolution.exact_expression_matches
    print(f"  query='{test_query}': entities={len(entities)} expressions={len(expressions)}")
    if entities:
        print(f"    entities: {[e['name'] for e in entities]}")
    if expressions:
        print(f"    expressions: {[e['name'] for e in expressions]}")

print()
print("Done. Profile seeded.")
