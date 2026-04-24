"""
tests/test_kg_server.py
────────────────────────
Full test suite for the refactored KG server (5-tool version).

Tests:
  1. Connection
  2. resolve_ingredient  — all 4 strategies + unresolved case
  3. get_hazard_profile  — merged tool (was get_hazard_statements + has_critical_hazard)
  4. get_full_profile    — single query, speed check
  5. get_target_organs   — standalone organ fetch
  6. get_exposure_limits — regulatory limits
  7. MCP protocol        — tools/list returns exactly 5 tools
  8. Edge cases          — empty string, numbers, special chars

Run:
  python tests/test_kg_server.py
  or
  pytest tests/test_kg_server.py -v
"""

import sys
import os
import time
import json
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from servers.kg_server.kg_client import KGClient

# ── test chemicals — known to exist in KG ─────────────────────────────────────
# Verified from previous test runs
KNOWN_CHEMICALS = {
    "Sodium Lauryl Sulfate": {
        "expect_uid":      True,
        "expect_danger":   True,
        "expect_organs":   True,
        "strategy":        "exact_match",
    },
    "limonene": {
        "expect_uid":      True,
        "expect_danger":   True,
        "expect_organs":   False,   # may or may not have organs
        "strategy":        "exact_match",
    },
    "vitamin c": {
        "expect_uid":      True,
        "expect_danger":   False,
        "expect_organs":   False,
        "strategy":        "exact_match",
    },
}

UNKNOWN_CHEMICALS = [
    "XNOTACHEMICALX",
    "water",          # non-chemical, should not resolve
    "aqua",           # same
    "",               # empty string edge case
]

# ── helpers ───────────────────────────────────────────────────────────────────

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

results = []

def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    msg = f"  {status} {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append((label, condition))
    return condition


def section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ── main test ─────────────────────────────────────────────────────────────────

def run_tests():
    print("=" * 60)
    print("  KG SERVER TEST SUITE — 5-tool version")
    print("=" * 60)

    client = KGClient()

    # ── TEST 1: Connection ────────────────────────────────────────────────────
    section("TEST 1: Neo4j Connection")
    try:
        client.connect()
        with client.driver.session() as s:
            r = s.run("RETURN 1 AS n").single()
            check("Neo4j Aura reachable", r["n"] == 1)
    except Exception as e:
        check("Neo4j Aura reachable", False, str(e))
        print("\n  FATAL: Cannot connect to Neo4j. Check .env credentials.")
        print(f"  NEO4J_URI={os.getenv('NEO4J_URI', 'NOT SET')}")
        sys.exit(1)

    # ── TEST 2: resolve_ingredient ────────────────────────────────────────────
    section("TEST 2: resolve_ingredient")

    for name, expected in KNOWN_CHEMICALS.items():
        r = client.resolve_ingredient(name)
        check(
            f"resolve '{name}'",
            r.get("uid") is not None,
            f"strategy={r.get('match_strategy')} uid={r.get('uid','NONE')[:20] if r.get('uid') else 'NONE'}"
        )
        check(
            f"  unresolved=False for '{name}'",
            r.get("unresolved") is False,
            f"unresolved={r.get('unresolved')}"
        )

    for name in UNKNOWN_CHEMICALS:
        r = client.resolve_ingredient(name)
        if name == "":
            # Empty string — should not crash
            check(
                "empty string does not crash",
                True,
                f"returned: {r.get('match_strategy')}"
            )
        else:
            check(
                f"unresolved '{name}' → uid=None",
                r.get("uid") is None,
                f"strategy={r.get('match_strategy')}"
            )
            check(
                f"unresolved '{name}' → unresolved=True",
                r.get("unresolved") is True,
            )

    # Store uid for SLS for subsequent tests
    sls = client.resolve_ingredient("Sodium Lauryl Sulfate")
    sls_uid = sls.get("uid")
    if not sls_uid:
        print("\n  FATAL: SLS uid required for remaining tests. Stopping.")
        sys.exit(1)

    # ── TEST 3: get_hazard_profile ────────────────────────────────────────────
    section("TEST 3: get_hazard_profile (merged tool)")

    h = client.get_hazard_profile(sls_uid)

    check("returns h_codes list",        isinstance(h.get("h_codes"), list))
    check("h_codes not empty for SLS",   len(h.get("h_codes", [])) > 0,
          f"codes={h.get('h_codes', [])[:4]}")
    check("highest_signal is string",    isinstance(h.get("highest_signal"), str),
          f"signal={h.get('highest_signal')}")
    check("has_danger is bool",          isinstance(h.get("has_danger"), bool),
          f"has_danger={h.get('has_danger')}")
    check("SLS has Danger signal",       h.get("has_danger") is True)
    check("has_critical_hazard is bool", isinstance(h.get("has_critical_hazard"), bool))
    check("critical_hazards is list",    isinstance(h.get("critical_hazards"), list))
    check("hazard_count > 0",            h.get("hazard_count", 0) > 0,
          f"count={h.get('hazard_count')}")
    check("hazards list present",        isinstance(h.get("hazards"), list))

    # Each hazard must have code and signal
    if h.get("hazards"):
        first = h["hazards"][0]
        check("hazard has code field",   "code" in first,   f"keys={list(first.keys())}")
        check("hazard has signal field", "signal" in first, f"keys={list(first.keys())}")

    # Test with known non-dangerous chemical
    vit_c = client.resolve_ingredient("vitamin c")
    if vit_c.get("uid"):
        hvc = client.get_hazard_profile(vit_c["uid"])
        check("vitamin C has_danger=False",
              hvc.get("has_danger") is False,
              f"signal={hvc.get('highest_signal')}")

    # ── TEST 4: get_full_profile ──────────────────────────────────────────────
    section("TEST 4: get_full_profile (single query, speed check)")

    start = time.time()
    p = client.get_full_profile(sls_uid)
    elapsed = time.time() - start

    check("completes in under 3 seconds",  elapsed < 3.0,  f"{elapsed:.2f}s")
    check("completes in under 2 seconds",  elapsed < 2.0,  f"{elapsed:.2f}s — target")
    check("returns uid",                   p.get("uid") == sls_uid)
    check("returns name",                  bool(p.get("name") or p.get("preferred_name")))
    check("target_organs is list",         isinstance(p.get("target_organs"), list))
    check("chemical_classes is list",      isinstance(p.get("chemical_classes"), list))
    check("hazards is list",               isinstance(p.get("hazards"), list))
    check("toxicity is list",              isinstance(p.get("toxicity"), list))
    check("exposure_limits is list",       isinstance(p.get("exposure_limits"), list))
    check("h_codes derived correctly",     isinstance(p.get("h_codes"), list))
    check("has_danger derived correctly",  isinstance(p.get("has_danger"), bool))
    check("has_critical_hazard derived",   isinstance(p.get("has_critical_hazard"), bool))
    check("SLS target_organs not empty",   len(p.get("target_organs", [])) > 0,
          f"organs={p.get('target_organs')}")

    # Verify full profile hazards match hazard_profile hazards
    fp_codes = set(p.get("h_codes", []))
    hp_codes = set(h.get("h_codes", []))
    check("full_profile h_codes match hazard_profile",
          fp_codes == hp_codes,
          f"diff={fp_codes.symmetric_difference(hp_codes)}")

    # Test with unknown uid
    bad = client.get_full_profile("uid_does_not_exist_xyz")
    check("unknown uid returns error dict not crash",
          "error" in bad or bad.get("unresolved") is True)

    # ── TEST 5: get_target_organs ─────────────────────────────────────────────
    section("TEST 5: get_target_organs")

    o = client.get_target_organs(sls_uid)
    check("returns uid",            o.get("uid") == sls_uid)
    check("organs is list",         isinstance(o.get("organs"), list))
    check("count is integer",       isinstance(o.get("count"), int))
    check("count matches list len", o.get("count") == len(o.get("organs", [])))
    check("SLS has organs",         o.get("count", 0) > 0,
          f"organs={o.get('organs')}")
    check("no None in organs list",
          all(x is not None for x in o.get("organs", [])))

    # ── TEST 6: get_exposure_limits ───────────────────────────────────────────
    section("TEST 6: get_exposure_limits")

    l = client.get_exposure_limits(sls_uid)
    check("returns uid",               l.get("uid") == sls_uid)
    check("exposure_limits is list",   isinstance(l.get("exposure_limits"), list))
    check("count is integer",          isinstance(l.get("count"), int))
    check("has_limits is bool",        isinstance(l.get("has_limits"), bool))
    check("count matches list len",
          l.get("count") == len(l.get("exposure_limits", [])))

    if l.get("exposure_limits"):
        first = l["exposure_limits"][0]
        check("limit has standard field", "standard" in first, f"keys={list(first.keys())}")
        check("limit has value field",    "value" in first)
        check("limit has unit field",     "unit" in first)

    # ── TEST 7: MCP protocol ──────────────────────────────────────────────────
    section("TEST 7: MCP protocol — tools/list returns exactly 5 tools")

    # Import the server's TOOLS list directly
    try:
        from servers.kg_server.server import TOOLS
        check("TOOLS list importable",       True)
        check("exactly 5 tools",             len(TOOLS) == 5,
              f"got {len(TOOLS)}: {[t['name'] for t in TOOLS]}")

        expected_tools = {
            "resolve_ingredient",
            "get_hazard_profile",
            "get_full_profile",
            "get_target_organs",
            "get_exposure_limits",
        }
        actual_tools = {t["name"] for t in TOOLS}
        check("correct tool names",          actual_tools == expected_tools,
              f"missing={expected_tools - actual_tools}, extra={actual_tools - expected_tools}")

        # Each tool must have name + description + inputSchema
        for tool in TOOLS:
            check(f"tool '{tool['name']}' has description",
                  len(tool.get("description", "")) > 20,
                  f"len={len(tool.get('description',''))}")
            check(f"tool '{tool['name']}' has inputSchema",
                  "inputSchema" in tool)

    except ImportError as e:
        check("TOOLS list importable", False, str(e))

    # ── TEST 8: Edge cases ────────────────────────────────────────────────────
    section("TEST 8: Edge cases")

    # Uppercase ingredient name
    r_upper = client.resolve_ingredient("SODIUM LAURYL SULFATE")
    check("case insensitive — uppercase resolves",
          r_upper.get("uid") is not None,
          f"strategy={r_upper.get('match_strategy')}")

    # Mixed case
    r_mixed = client.resolve_ingredient("sodium LAURYL sulfate")
    check("case insensitive — mixed case resolves",
          r_mixed.get("uid") is not None)

    # CAS number resolution (if SLS has known CAS)
    sls_cas = sls.get("cas")
    if sls_cas:
        r_cas = client.resolve_ingredient(sls_cas)
        check(f"CAS resolution works for {sls_cas}",
              r_cas.get("uid") is not None,
              f"strategy={r_cas.get('match_strategy')}")
    else:
        print(f"  {WARN} SLS CAS not in KG — skipping CAS test")

    # get_hazard_profile on unresolved uid should not crash
    try:
        h_bad = client.get_hazard_profile("nonexistent_uid_xyz")
        check("get_hazard_profile with bad uid does not crash",
              True, f"h_codes={h_bad.get('h_codes')}")
    except Exception as e:
        check("get_hazard_profile with bad uid does not crash", False, str(e))

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    client.close()

    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    total  = len(results)

    print("\n" + "=" * 60)
    print(f"  RESULTS: {passed}/{total} passed  |  {failed} failed")
    print("=" * 60)

    if failed == 0:
        print("  ✅ KG SERVER READY — all tests passed")
        print("  ✅ Safe to move to Phase 4: Combination Server")
    else:
        print("  ❌ FIX FAILURES BEFORE MOVING TO PHASE 4")
        print("\n  Failed tests:")
        for label, ok in results:
            if not ok:
                print(f"    ❌ {label}")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)