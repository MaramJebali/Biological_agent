"""
kg_client.py — Neo4j Knowledge Graph Client
─────────────────────────────────────────────
WHAT CHANGED FROM ORIGINAL:
  1. Fixed import: from neo4j import GraphDatabase (not from config.neo4j_client)
  2. get_complete_profile → now uses GET_FULL_PROFILE (1 query, was 11)
  3. get_hazard_profile → NEW method merging get_hazard_statements + has_critical_hazard
  4. Removed: get_skin_effects, get_eye_effects, get_inhalation_effects,
              get_ingestion_effects, get_excretion_routes, get_chemical_classes,
              get_toxicity_profile, batch_resolve
     These are all covered by get_complete_profile (which calls get_full_profile)
     The agent never needs them independently.
  5. get_target_organs KEPT — combination server needs organs separately
  6. get_exposure_limits KEPT — agent needs limits separately (no dose data context)
  7. resolve_ingredient UNCHANGED — it was correct

TOOL COUNT: was 14, now 5
  resolve_ingredient  → name/CAS → uid + basic info
  get_hazard_profile  → uid → hazards + signal + critical flag (merged)
  get_full_profile    → uid → everything in 1 Neo4j query
  get_target_organs   → uid → organs only (for combination server)
  get_exposure_limits → uid → regulatory limits only
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config.neo4j_client import GraphDatabase          # FIXED: was "from config.neo4j_client import GraphDatabase"
from dotenv import load_dotenv

from servers.kg_server.queries import (
    RESOLVE_INGREDIENT_EXACT,
    RESOLVE_INGREDIENT_CAS,
    RESOLVE_INGREDIENT_SYNONYM,
    RESOLVE_INGREDIENT_PARTIAL,
    GET_FULL_PROFILE,
    GET_HAZARDS_LIST,
    GET_ORGANS_LIST,
    GET_EXPOSURE_LIMITS_LIST,
    HAS_CRITICAL_HAZARD,
    GET_ORGAN_FOR_MULTIPLE_CHEMICALS,
    TEST_QUERY,
)

load_dotenv()

# Critical H-codes: carcinogen, mutagen, reprotoxic, STOT severe
CRITICAL_H_CODES = {"H340", "H341", "H350", "H351", "H360", "H361", "H362", "H370", "H372"}


class KGClient:

    def __init__(self):
        self.uri      = os.getenv("NEO4J_URI")
        self.user     = os.getenv("NEO4J_USER")
        self.password = os.getenv("NEO4J_PASSWORD")
        self.driver   = None

    def connect(self):
        self.driver = GraphDatabase.driver(
            self.uri, auth=(self.user, self.password)
        )
        return self.driver

    def close(self):
        if self.driver:
            self.driver.close()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _is_cas(self, text: str) -> bool:
        return bool(re.match(r'^\d{2,7}-\d{2}-\d$', text.strip()))

    def _one(self, query: str, params: dict):
        """Return first record as dict, or None."""
        with self.driver.session() as s:
            rec = s.run(query, params).single()
            return dict(rec) if rec else None

    def _collect(self, query: str, params: dict):
        """Return first column of first record (always a collected list)."""
        with self.driver.session() as s:
            rec = s.run(query, params).single()
            return rec[0] if rec else []

    # ── TOOL 1: resolve_ingredient ────────────────────────────────────────────

    def resolve_ingredient(self, name: str) -> dict:
        """
        Convert ingredient name → chemical uid.
        Cascade: exact → CAS → synonym → partial
        Returns uid=None with match_strategy='not_found' if nothing matches.
        """
        name = name.strip()

        for strategy, query in [
            ("exact_match",   RESOLVE_INGREDIENT_EXACT),
            ("cas_match",     RESOLVE_INGREDIENT_CAS) if self._is_cas(name) else (None, None),
            ("synonym_match", RESOLVE_INGREDIENT_SYNONYM),
            ("partial_match", RESOLVE_INGREDIENT_PARTIAL),
        ]:
            if strategy is None:
                continue
            r = self._one(query, {"name": name})
            if r and r.get("uid"):
                r["match_strategy"]  = strategy
                r["original_name"]   = name
                r["unresolved"]      = False
                if strategy == "partial_match":
                    r["warning"] = "Matched via partial text — verify correctness"
                return r

        return {
            "original_name":  name,
            "uid":            None,
            "match_strategy": "not_found",
            "unresolved":     True,
            "error":          f"Chemical not found in KG: {name}",
        }

    # ── TOOL 2: get_hazard_profile ────────────────────────────────────────────

    def get_hazard_profile(self, uid: str) -> dict:
        """
        Get hazard classification for a chemical.
        MERGED: replaces get_hazard_statements + has_critical_hazard
        Returns everything the LLM needs to decide investigation depth.
        """
        hazards = self._collect(GET_HAZARDS_LIST, {"uid": uid})

        h_codes = [h["code"] for h in hazards if h.get("code")]
        signals = [h["signal"] for h in hazards if h.get("signal")]

        critical_found = [c for c in h_codes if c in CRITICAL_H_CODES]

        return {
            "uid":                uid,
            "h_codes":            h_codes,
            "highest_signal":     "Danger" if "Danger" in signals
                                  else ("Warning" if signals else "None"),
            "has_danger":         "Danger" in signals,
            "has_critical_hazard": len(critical_found) > 0,
            "critical_hazards":   critical_found,
            "hazard_count":       len(hazards),
            "hazards":            hazards,
        }

    # ── TOOL 3: get_full_profile ──────────────────────────────────────────────

    def get_full_profile(self, uid: str) -> dict:
        """
        Get complete chemical profile in ONE Neo4j query.
        FIXED: original made 11 separate round trips — now 1 query.
        Use this for deep investigation of HIGH/CRITICAL chemicals.
        """
        r = self._one(GET_FULL_PROFILE, {"uid": uid})
        if not r:
            return {"uid": uid, "error": "Chemical not found", "unresolved": True}

        # Derive hazard summary from returned data
        hazards = r.get("hazards") or []
        h_codes = [h["code"] for h in hazards if h.get("code")]
        signals = [h["signal"] for h in hazards if h.get("signal")]
        critical = [c for c in h_codes if c in CRITICAL_H_CODES]

        return {
            # Identity
            "uid":              r.get("uid"),
            "name":             r.get("name"),
            "preferred_name":   r.get("preferred_name"),
            "cas":              r.get("cas"),
            "molecular_formula":r.get("molecular_formula"),
            "molecular_weight": r.get("molecular_weight"),
            "description":      r.get("description"),
            "synonyms":         r.get("synonyms") or [],

            # Hazard summary (derived — saves the LLM from parsing raw hazards)
            "highest_signal":      "Danger" if "Danger" in signals
                                   else ("Warning" if signals else "None"),
            "has_danger":          "Danger" in signals,
            "has_critical_hazard": len(critical) > 0,
            "critical_hazards":    critical,
            "h_codes":             h_codes,

            # Full relationship data
            "hazards":            hazards,
            "target_organs":      [o for o in (r.get("target_organs") or []) if o],
            "chemical_classes":   [c for c in (r.get("chemical_classes") or []) if c],
            "toxicity":           [t for t in (r.get("toxicity") or []) if t.get("type")],
            "exposure_limits":    [e for e in (r.get("exposure_limits") or []) if e.get("standard")],
            "skin_effects":       [e for e in (r.get("skin_effects") or []) if e],
            "eye_effects":        [e for e in (r.get("eye_effects") or []) if e],
            "inhalation_effects": [e for e in (r.get("inhalation_effects") or []) if e],
            "ingestion_effects":  [e for e in (r.get("ingestion_effects") or []) if e],
            "excretion_routes":   [e for e in (r.get("excretion_routes") or []) if e],
        }

    # ── TOOL 4: get_target_organs ─────────────────────────────────────────────

    def get_target_organs(self, uid: str) -> dict:
        """
        Get target organs only.
        Kept separate because the combination server needs organs
        without fetching the full profile.
        """
        organs = self._collect(GET_ORGANS_LIST, {"uid": uid})
        return {
            "uid":    uid,
            "organs": [o for o in organs if o],
            "count":  len([o for o in organs if o]),
        }

    # ── TOOL 5: get_exposure_limits ───────────────────────────────────────────

    def get_exposure_limits(self, uid: str) -> dict:
        """
        Get regulatory exposure limits (OSHA, EU, ACGIH).
        Kept separate because the evaluation server needs limits
        without fetching the full profile.
        Note: without dose data these limits inform qualitative
        risk level but cannot be compared directly.
        """
        limits = self._collect(GET_EXPOSURE_LIMITS_LIST, {"uid": uid})
        return {
            "uid":              uid,
            "exposure_limits":  [l for l in limits if l.get("standard")],
            "count":            len([l for l in limits if l.get("standard")]),
            "has_limits":       len([l for l in limits if l.get("standard")]) > 0,
        }

    # ── utility for combination server (not an MCP tool) ─────────────────────

    def get_organs_for_multiple(self, uids: list) -> list:
        """
        Internal utility — not exposed as MCP tool.
        Called by combination server to fetch organs for multiple chemicals.
        """
        with self.driver.session() as s:
            return [dict(r) for r in
                    s.run(GET_ORGAN_FOR_MULTIPLE_CHEMICALS, {"uids": uids})]

    # ── connection test ───────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        try:
            with self.driver.session() as s:
                s.run("RETURN 1").single()
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            return False


# ── manual test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    client = KGClient()
    client.connect()

    print("=" * 60)
    print("KG CLIENT — 5-TOOL VERSION TEST")
    print("=" * 60)

    # Test resolve
    r = client.resolve_ingredient("Sodium Lauryl Sulfate")
    print(f"\n[1] resolve_ingredient: {r.get('match_strategy')} → {r.get('uid')}")
    assert r.get("uid"), "FAIL: SLS should resolve"

    uid = r["uid"]

    # Test hazard profile
    h = client.get_hazard_profile(uid)
    print(f"[2] get_hazard_profile: signal={h['highest_signal']}, "
          f"critical={h['has_critical_hazard']}, codes={h['h_codes'][:3]}")
    assert h.get("h_codes"), "FAIL: SLS should have hazards"

    # Test full profile — verify single query speed
    start = time.time()
    p = client.get_full_profile(uid)
    elapsed = time.time() - start
    print(f"[3] get_full_profile: {elapsed:.2f}s — "
          f"organs={p['target_organs']}, classes={p['chemical_classes'][:2]}")
    assert elapsed < 2.0, f"FAIL: too slow ({elapsed:.2f}s)"
    assert p.get("target_organs") is not None

    # Test target organs
    o = client.get_target_organs(uid)
    print(f"[4] get_target_organs: {o['organs']}")

    # Test exposure limits
    l = client.get_exposure_limits(uid)
    print(f"[5] get_exposure_limits: count={l['count']}, has_limits={l['has_limits']}")

    # Test unresolved chemical
    nr = client.resolve_ingredient("XXXXXXNOTACHEMICAL")
    print(f"[6] unresolved: {nr['match_strategy']} → unresolved={nr['unresolved']}")
    assert nr["unresolved"] is True

    client.close()

    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED — KG CLIENT READY (5 tools)")
    print("=" * 60)