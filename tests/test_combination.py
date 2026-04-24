"""
tests/test_combination.py
──────────────────────────
Tests for combination_server/synergies.py
Pure Python — no credentials, no network, no LLM.
Run: python tests/test_combination.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from servers.combination_server.synergies import (
    check_organ_overlap,
    check_cumulative_presence,
    check_hazard_intersection,
)

PASS = "✅"
FAIL = "❌"
results = []

def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    msg = f"  {status} {label}"
    if detail: msg += f" — {detail}"
    print(msg)
    results.append((label, condition))


def section(title):
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


# ── ORGAN OVERLAP TESTS ────────────────────────────────────────────────────────

section("check_organ_overlap — basic cases")

# No overlap
r = check_organ_overlap([
    {"name":"A","uid":"1","target_organs":["liver"],"h_codes":[]},
    {"name":"B","uid":"2","target_organs":["kidney"],"h_codes":[]},
])
check("no overlap → has_overlap=False",       r["has_overlap"] is False)
check("no overlap → escalation=None",         r["verdict_escalation"] is None)

# 2 chemicals, same organ → MODERATE
r = check_organ_overlap([
    {"name":"SLS",    "uid":"1","target_organs":["skin","eyes"],"h_codes":[]},
    {"name":"Paraben","uid":"2","target_organs":["skin"],        "h_codes":[]},
])
check("2 chemicals on skin → has_overlap=True",      r["has_overlap"] is True)
check("2 chemicals on skin → escalation=MODERATE",   r["verdict_escalation"] == "MODERATE",
      f"got={r['verdict_escalation']}")
check("overlap count=2",
      any(o["count"] == 2 for o in r["overlapping_organs"]))

# 3 chemicals, same organ → HIGH
r = check_organ_overlap([
    {"name":"A","uid":"1","target_organs":["respiratory"],"h_codes":[]},
    {"name":"B","uid":"2","target_organs":["respiratory"],"h_codes":[]},
    {"name":"C","uid":"3","target_organs":["respiratory"],"h_codes":[]},
])
check("3 chemicals on respiratory → escalation=HIGH",
      r["verdict_escalation"] == "HIGH", f"got={r['verdict_escalation']}")
check("max_chemicals_per_organ=3",  r["max_chemicals_per_organ"] == 3)

section("check_organ_overlap — H370-H373 fallback")

# Chemical with H372 (organ damage) but no explicit organs
r = check_organ_overlap([
    {"name":"SLS","uid":"1","target_organs":[],"h_codes":["H372","H315"]},
    {"name":"ChemB","uid":"2","target_organs":[],"h_codes":["H372"]},
])
check("H372 chemicals → unspecified_organ_damage flagged",
      len(r["unspecified_organ_damage"]) > 0,
      f"unspecified={r['unspecified_organ_damage']}")
check("H372 overlap → has_overlap=True (via unspecified organ)",
      r["has_overlap"] is True,
      f"has_overlap={r['has_overlap']}")

# Mix of explicit organ and H372 fallback
r = check_organ_overlap([
    {"name":"A","uid":"1","target_organs":["liver"],"h_codes":[]},
    {"name":"B","uid":"2","target_organs":[],"h_codes":["H370"]},
    {"name":"C","uid":"3","target_organs":[],"h_codes":["H372"]},
])
check("explicit organ + H370/H372 fallbacks both tracked",
      r["has_overlap"] is True)

section("check_organ_overlap — edge cases")

r = check_organ_overlap([])
check("empty list → has_overlap=False",  r["has_overlap"] is False)

r = check_organ_overlap([
    {"name":"A","uid":"1","target_organs":[],"h_codes":[]},
])
check("single chemical, no organs → no overlap", r["has_overlap"] is False)

# ── CUMULATIVE PRESENCE TESTS ──────────────────────────────────────────────────

section("check_cumulative_presence")

r = check_cumulative_presence("PARFUM", [
    {"product_id":"1","product_name":"Cream"},
    {"product_id":"2","product_name":"Perfume"},
])
check("2 products → is_cumulative=True",    r["is_cumulative"] is True)
check("frequency=2",                         r["frequency"] == 2)
check("risk_note contains product count",    "2" in r["risk_note"])
check("products list preserved",             len(r["products"]) == 2)

r = check_cumulative_presence("Vitamin C", [
    {"product_id":"1","product_name":"Cream"},
])
check("1 product → is_cumulative=False",    r["is_cumulative"] is False)
check("frequency=1",                         r["frequency"] == 1)

r = check_cumulative_presence("X", [])
check("empty products → frequency=0",       r["frequency"] == 0)
check("empty products → is_cumulative=False", r["is_cumulative"] is False)

# ── HAZARD INTERSECTION TESTS ──────────────────────────────────────────────────

section("check_hazard_intersection")

r = check_hazard_intersection([
    {"name":"SLS",     "h_codes":["H315","H317","H319"]},
    {"name":"Limonene","h_codes":["H315","H317","H410"]},
])
check("H315 shared → in shared_h_codes",
      "H315" in r["shared_h_codes"])
check("H317 shared → in shared_h_codes",
      "H317" in r["shared_h_codes"])
check("H410 not shared → not in shared_h_codes",
      "H410" not in r["shared_h_codes"])
check("details dict present",
      isinstance(r["details"], dict))

# Critical H-code overlap
r = check_hazard_intersection([
    {"name":"A","h_codes":["H350","H315"]},
    {"name":"B","h_codes":["H350","H317"]},
])
check("shared H350 → has_critical_overlap=True",
      r["has_critical_overlap"] is True)
check("shared H350 → severity_escalation=True",
      r["severity_escalation"] is True)
check("H350 in shared_critical_codes",
      "H350" in r["shared_critical_codes"])

# No shared codes
r = check_hazard_intersection([
    {"name":"A","h_codes":["H315"]},
    {"name":"B","h_codes":["H319"]},
])
check("no shared codes → shared_h_codes=[]",
      r["shared_h_codes"] == [])
check("no shared codes → severity_escalation=False",
      r["severity_escalation"] is False)

# Edge cases
r = check_hazard_intersection([])
check("empty list → no crash",    r["shared_h_codes"] == [])

r = check_hazard_intersection([{"name":"A","h_codes":[]}])
check("one chemical no codes → no crash", r["shared_h_codes"] == [])

# ── SUMMARY ───────────────────────────────────────────────────────────────────

passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)

print(f"\n{'='*55}")
print(f"  RESULTS: {passed}/{len(results)} passed  |  {failed} failed")
print(f"{'='*55}")

if failed == 0:
    print("  ✅ Combination server logic: ALL TESTS PASSED")
    print("  ✅ Safe to proceed to Phase 5 integration tests")
else:
    print("  ❌ FAILURES:")
    for label, ok in results:
        if not ok:
            print(f"    ❌ {label}")

import sys
sys.exit(0 if failed == 0 else 1)