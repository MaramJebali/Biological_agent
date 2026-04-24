"""
tests/test_integration.py
──────────────────────────
Full integration test — requires real Neo4j + Groq credentials.
Tests the complete agent pipeline end-to-end.

Run: python tests/test_integration.py

Tests:
  1. All 4 MCP servers start and respond correctly
  2. Filter server classifies ingredients (Groq call)
  3. KG server resolves chemicals (Neo4j)
  4. Evaluation server returns metrics (pure Python)
  5. Combination server detects overlap (pure Python)
  6. Full agent run on minimal input (2 chemicals)
"""

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from agent.agent import MCPClient, BiologicalAgent, run_evaluation

SERVER_PATHS = {
    "kg":          os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "servers", "kg_server", "server.py"),
    "filter":      os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "servers", "filter_server", "server.py"),
    "combination": os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "servers", "combination_server", "server.py"),
    "evaluation":  os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "servers", "evaluation_server", "server.py"),
}

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "
results = []


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    msg = f"  {status} {label}"
    if detail: msg += f" — {detail}"
    print(msg)
    results.append((label, condition))
    return condition


def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ── Test 1: Server startup ────────────────────────────────────────────────────

async def test_server_startup():
    section("TEST 1: All 4 MCP servers start and respond")

    for name, path in SERVER_PATHS.items():
        if not os.path.exists(path):
            check(f"{name} server file exists", False, f"missing: {path}")
            continue

        try:
            client = MCPClient(name, path)
            await client.start()

            tools = await client.list_tools()
            check(f"{name} server starts",       True,  f"{len(tools)} tools")
            check(f"{name} returns tools list",  len(tools) > 0,
                  f"tools: {[t['name'] for t in tools]}")

            await client.stop()
        except Exception as e:
            check(f"{name} server starts", False, str(e)[:80])


# ── Test 2: Filter server ─────────────────────────────────────────────────────

async def test_filter_server():
    section("TEST 2: Filter server — classify_ingredients")

    client = MCPClient("filter", SERVER_PATHS["filter"])
    await client.start()

    try:
        result = await client.call("classify_ingredients", {
            "ingredients": [
                {"name": "AQUA"},
                {"name": "SODIUM LAURYL SULFATE"},
                {"name": "PARFUM"},
                {"name": "GLYCERIN"},
                {"name": "PHENOXYETHANOL"},
            ],
            "usage": "cosmetic"
        })

        check("returns chemicals list",    isinstance(result.get("chemicals"), list))
        check("returns safe_skipped list", isinstance(result.get("safe_skipped"), list))

        chem_names = [c.get("name","").upper() for c in result.get("chemicals", [])]
        safe_names = [s.get("name","").upper() for s in result.get("safe_skipped", [])]

        check("AQUA classified as safe",
              any("AQUA" in n for n in safe_names),
              f"safe={safe_names}")
        check("SODIUM LAURYL SULFATE classified as chemical",
              any("SODIUM" in n for n in chem_names),
              f"chemicals={chem_names}")
        check("PHENOXYETHANOL classified as chemical",
              any("PHENOXY" in n for n in chem_names),
              f"chemicals={chem_names}")

        total = len(result.get("chemicals",[])) + len(result.get("safe_skipped",[]))
        check("all 5 ingredients classified",
              total == 5,
              f"total classified={total}")

    finally:
        await client.stop()


# ── Test 3: KG server ─────────────────────────────────────────────────────────

async def test_kg_server():
    section("TEST 3: KG server — resolve + hazard")

    client = MCPClient("kg", SERVER_PATHS["kg"])
    await client.start()

    try:
        # Resolve
        r = await client.call("resolve_ingredient", {"ingredient_name": "Sodium Lauryl Sulfate"})
        check("SLS resolves",          r.get("uid") is not None,
              f"uid={r.get('uid')}")
        check("unresolved=False",      r.get("unresolved") is False)

        uid = r.get("uid")
        if uid:
            # Hazard profile
            h = await client.call("get_hazard_profile", {"chemical_uid": uid})
            check("SLS has hazards",        len(h.get("h_codes",[])) > 0,
                  f"count={len(h.get('h_codes',[]))}")
            check("SLS has Danger signal",  h.get("has_danger") is True,
                  f"signal={h.get('highest_signal')}")

            # Full profile — speed check
            start = time.time()
            p = await client.call("get_full_profile", {"chemical_uid": uid})
            elapsed = time.time() - start
            check("full profile < 2s",      elapsed < 2.0, f"{elapsed:.2f}s")
            check("full profile has h_codes", p.get("h_codes") is not None)

        # Unresolved chemical
        nr = await client.call("resolve_ingredient", {"ingredient_name": "ZZZNOMATCH999"})
        check("unknown → unresolved=True",  nr.get("unresolved") is True)

    finally:
        await client.stop()


# ── Test 4: Evaluation server ─────────────────────────────────────────────────

async def test_evaluation_server():
    section("TEST 4: Evaluation server — get_investigation_metrics")

    client = MCPClient("evaluation", SERVER_PATHS["evaluation"])
    await client.start()

    try:
        # Unresolved chemical
        r = await client.call("get_investigation_metrics", {
            "chemical_name": "PARFUM",
            "resolution_result": {"uid": None, "unresolved": True, "match_strategy": "not_found"},
            "hazard_result": {},
        })
        check("unresolved → UNKNOWN risk",  r.get("preliminary_risk") == "UNKNOWN")
        check("unresolved → skip depth",    r.get("recommended_depth") == "skip")

        # Critical chemical
        r2 = await client.call("get_investigation_metrics", {
            "chemical_name": "Formaldehyde",
            "resolution_result": {"uid": "uid_001", "unresolved": False, "match_strategy": "exact_match"},
            "hazard_result": {
                "h_codes": ["H350","H317"],
                "highest_signal": "Danger",
                "has_danger": True,
                "has_critical_hazard": True,
                "critical_hazards": ["H350"],
            },
        })
        check("H350 → CRITICAL risk",   r2.get("preliminary_risk") == "CRITICAL")
        check("H350 → full depth",       r2.get("recommended_depth") == "full")

    finally:
        await client.stop()


# ── Test 5: Combination server ────────────────────────────────────────────────

async def test_combination_server():
    section("TEST 5: Combination server — organ overlap")

    client = MCPClient("combination", SERVER_PATHS["combination"])
    await client.start()

    try:
        r = await client.call("check_organ_overlap", {
            "chemicals": [
                {"name": "SLS",    "uid": "u1", "target_organs": ["skin","eyes"], "h_codes": ["H315"]},
                {"name": "Paraben","uid": "u2", "target_organs": ["skin"],        "h_codes": ["H317"]},
                {"name": "Fragrance","uid":"u3","target_organs": ["skin","respiratory"], "h_codes":[]},
            ]
        })
        check("3 chemicals on skin → overlap detected",
              r.get("has_overlap") is True)
        check("escalation is HIGH (3 on skin)",
              r.get("verdict_escalation") == "HIGH",
              f"escalation={r.get('verdict_escalation')}")

        # Cumulative presence
        r2 = await client.call("check_cumulative_presence", {
            "chemical_name": "PARFUM",
            "products": [
                {"product_id": "1", "product_name": "Cream"},
                {"product_id": "2", "product_name": "Perfume"},
            ]
        })
        check("PARFUM in 2 products → is_cumulative=True",
              r2.get("is_cumulative") is True)

    finally:
        await client.stop()


# ── Test 6: Full agent run ────────────────────────────────────────────────────

async def test_full_agent():
    section("TEST 6: Full agent run — minimal input (2 chemicals only)")

    # Minimal input to keep token usage low during testing
    minimal_products = [
        {
            "product_id":    "test_001",
            "product_name":  "Test Product",
            "product_usage": "cosmetic",
            "exposure_type": "skin",
            "ingredient_list": [
                {"name": "AQUA"},
                {"name": "SODIUM LAURYL SULFATE"},
                {"name": "PHENOXYETHANOL"},
            ]
        }
    ]

    print(f"\n  Running full agent on {len(minimal_products)} product(s)...")
    print(f"  Ingredients: AQUA, SODIUM LAURYL SULFATE, PHENOXYETHANOL")
    print(f"  (This will make ~3-4 Groq calls and several Neo4j queries)")
    print()

    start = time.time()
    try:
        result = await run_evaluation(minimal_products)
        elapsed = time.time() - start

        check("agent completes without error",     True,  f"{elapsed:.1f}s")
        check("result has analyzed_at",            "analyzed_at" in result)
        check("result has report",                 "report" in result)

        report = result.get("report", {})
        if "raw" in report:
            print(f"  {WARN} Report returned raw text (JSON parsing failed)")
            print(f"       Raw preview: {str(report.get('raw',''))[:200]}")
            check("report is structured JSON", False, "raw text returned")
        else:
            check("report has product_verdicts",
                  "product_verdicts" in report,
                  f"keys={list(report.keys())}")
            check("report has chemicals_summary",
                  "chemicals_summary" in report)

            verdicts = report.get("product_verdicts", [])
            if verdicts:
                v = verdicts[0]
                check("verdict has risk_level",
                      v.get("risk_level") in
                      {"CRITICAL","HIGH","MODERATE","LOW","SAFE","UNKNOWN"},
                      f"risk_level={v.get('risk_level')}")
                check("verdict has recommendation",
                      v.get("recommendation") in
                      {"avoid","reduce_use","use_with_caution","keep","unknown"},
                      f"recommendation={v.get('recommendation')}")

        print(f"\n  Final report preview:")
        print(f"  {json.dumps(report, indent=2)[:400]}...")

    except Exception as e:
        elapsed = time.time() - start
        check("agent completes without error", False, f"{type(e).__name__}: {str(e)[:100]}")
        import traceback
        print(f"\n  Full traceback:")
        traceback.print_exc()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  INTEGRATION TEST SUITE")
    print("  Requires: Neo4j Aura + Groq API credentials in .env")
    print("=" * 60)

    # Validate credentials
    try:
        config.validate()
        print(f"  ✅ Credentials validated")
        print(f"  ✅ Groq model: {config.GROQ_MODEL}")
        print(f"  ✅ Neo4j URI:  {config.NEO4J_URI}")
    except EnvironmentError as e:
        print(f"  ❌ {e}")
        sys.exit(1)

    await test_server_startup()
    await test_filter_server()
    await test_kg_server()
    await test_evaluation_server()
    await test_combination_server()
    await test_full_agent()

    # Summary
    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    total  = len(results)

    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed}/{total} passed  |  {failed} failed")
    print(f"{'='*60}")

    if failed == 0:
        print("  ✅ ALL INTEGRATION TESTS PASSED")
        print("  ✅ Agent is ready for production use")
    else:
        print("  ❌ FAILURES — fix before using agent:")
        for label, ok in results:
            if not ok:
                print(f"    ❌ {label}")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)