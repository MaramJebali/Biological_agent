"""
tests/test_evaluation.py
──────────────────────────
Tests for evaluation_server/evaluator.py
Pure Python — no credentials, no network.
Run: python tests/test_evaluation.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from servers.evaluation_server.evaluator import get_investigation_metrics

PASS="✅"; FAIL="❌"
results=[]

def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    msg = f"  {status} {label}"
    if detail: msg += f" — {detail}"
    print(msg)
    results.append((label, condition))

def section(title):
    print(f"\n{'─'*55}\n  {title}\n{'─'*55}")

# ── UNRESOLVED CHEMICAL ────────────────────────────────────────────────────────
section("Unresolved chemical")

r = get_investigation_metrics(
    "PARFUM",
    {"uid": None, "unresolved": True, "match_strategy": "not_found"},
    {}
)
check("unresolved → preliminary_risk=UNKNOWN",  r["preliminary_risk"] == "UNKNOWN")
check("unresolved → recommended_depth=skip",    r["recommended_depth"] == "skip")
check("unresolved → has_uid=False",             r["has_uid"] is False)
check("unresolved → is_unresolved=True",        r["is_unresolved"] is True)
check("unresolved → reasoning explains",        "not found" in r["reasoning"].lower())

# ── CRITICAL CHEMICAL ─────────────────────────────────────────────────────────
section("Critical chemical (H350 carcinogen)")

r = get_investigation_metrics(
    "Formaldehyde",
    {"uid": "chem_001", "unresolved": False, "match_strategy": "exact_match"},
    {
        "h_codes": ["H350","H317","H335"],
        "highest_signal": "Danger",
        "has_danger": True,
        "has_critical_hazard": True,
        "critical_hazards": ["H350"],
    }
)
check("H350 → preliminary_risk=CRITICAL",   r["preliminary_risk"] == "CRITICAL")
check("H350 → recommended_depth=full",      r["recommended_depth"] == "full")
check("H350 → has_critical_hazard=True",    r["has_critical_hazard"] is True)
check("H350 → has_uid=True",               r["has_uid"] is True)
check("reasoning mentions critical",        "critical" in r["reasoning"].lower()
      or "H350" in r["reasoning"])

# ── DANGER SIGNAL, NO CRITICAL ────────────────────────────────────────────────
section("Danger signal (no critical hazard)")

r = get_investigation_metrics(
    "SLS",
    {"uid": "pest_001", "unresolved": False, "match_strategy": "synonym_match"},
    {
        "h_codes": ["H315","H317","H319"],
        "highest_signal": "Danger",
        "has_danger": True,
        "has_critical_hazard": False,
        "critical_hazards": [],
    }
)
check("Danger, no critical → risk=HIGH",    r["preliminary_risk"] == "HIGH")
check("Danger, no critical → depth=full",   r["recommended_depth"] == "full")
check("has_danger_signal=True",             r["has_danger_signal"] is True)
check("has_critical_hazard=False",          r["has_critical_hazard"] is False)

# ── WARNING SIGNAL ────────────────────────────────────────────────────────────
section("Warning signal")

r = get_investigation_metrics(
    "Linalool",
    {"uid": "chem_002", "unresolved": False, "match_strategy": "exact_match"},
    {
        "h_codes": ["H317"],
        "highest_signal": "Warning",
        "has_danger": False,
        "has_critical_hazard": False,
        "critical_hazards": [],
    }
)
check("Warning → preliminary_risk=MODERATE", r["preliminary_risk"] == "MODERATE")
check("Warning → recommended_depth=basic",   r["recommended_depth"] == "basic")
check("has_warning_signal=True",             r["has_warning_signal"] is True)

# ── SAFE CHEMICAL ─────────────────────────────────────────────────────────────
section("Safe chemical (no hazards)")

r = get_investigation_metrics(
    "Vitamin C",
    {"uid": "chem_003", "unresolved": False, "match_strategy": "synonym_match"},
    {
        "h_codes": [],
        "highest_signal": "None",
        "has_danger": False,
        "has_critical_hazard": False,
        "critical_hazards": [],
    }
)
check("No hazards → preliminary_risk=SAFE", r["preliminary_risk"] == "SAFE")
check("No hazards → recommended_depth=skip",r["recommended_depth"] == "skip")
check("has_hazards=False",                  r["has_hazards"] is False)

# ── DATA COMPLETENESS ─────────────────────────────────────────────────────────
section("Data completeness scoring")

r_exact = get_investigation_metrics(
    "X",
    {"uid":"u1","unresolved":False,"match_strategy":"exact_match"},
    {"h_codes":["H315"],"highest_signal":"Warning","has_danger":False,
     "has_critical_hazard":False,"critical_hazards":[]}
)
r_partial = get_investigation_metrics(
    "X",
    {"uid":"u1","unresolved":False,"match_strategy":"partial_match"},
    {"h_codes":["H315"],"highest_signal":"Warning","has_danger":False,
     "has_critical_hazard":False,"critical_hazards":[]}
)
check("exact_match has higher completeness than partial_match",
      r_exact["data_completeness"] >= r_partial["data_completeness"],
      f"exact={r_exact['data_completeness']} partial={r_partial['data_completeness']}")
# ── PHASE 5: Confidence Scoring Tests ─────────────────────────────────────────

section("Confidence scoring and low confidence override")

# Test 1: High confidence from exact match
r_high = get_investigation_metrics(
    "SLS",
    {"uid": "u1", "unresolved": False, "match_strategy": "exact_match"},
    {"h_codes": ["H315"], "highest_signal": "Warning", "has_danger": False,
     "has_critical_hazard": False, "critical_hazards": []}
)
check("exact_match → confidence >= 0.7", 
      r_high.get("confidence", 0) >= 0.7,
      f"confidence={r_high.get('confidence')}")

# Test 2: Low confidence from partial match
r_low = get_investigation_metrics(
    "Unknown",
    {"uid": "u1", "unresolved": False, "match_strategy": "partial_match"},
    {"h_codes": [], "highest_signal": "None", "has_danger": False,
     "has_critical_hazard": False, "critical_hazards": []}
)
check("partial_match → confidence < 0.5", 
      r_low.get("confidence", 1) < 0.5,
      f"confidence={r_low.get('confidence')}")

# Test 3: Low confidence overrides risk to UNKNOWN
check("low confidence → risk becomes UNKNOWN",
      r_low.get("preliminary_risk") == "UNKNOWN",
      f"risk={r_low.get('preliminary_risk')}")

# Test 4: Unresolved chemical has confidence=0
r_unresolved = get_investigation_metrics(
    "NotFound",
    {"uid": None, "unresolved": True, "match_strategy": "not_found"},
    {}
)
check("unresolved → confidence=0",
      r_unresolved.get("confidence", 1) == 0,
      f"confidence={r_unresolved.get('confidence')}")

# ── SUMMARY ───────────────────────────────────────────────────────────────────
passed = sum(1 for _,ok in results if ok)
failed = sum(1 for _,ok in results if not ok)

print(f"\n{'='*55}")
print(f"  RESULTS: {passed}/{len(results)} passed  |  {failed} failed")
print(f"{'='*55}")
if failed == 0:
    print("  ✅ Evaluation server logic: ALL TESTS PASSED")
else:
    for label, ok in results:
        if not ok: print(f"    ❌ {label}")

import sys
sys.exit(0 if failed == 0 else 1)