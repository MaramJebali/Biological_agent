"""
kg_client.py — Neo4j Knowledge Graph Client - PRODUCTION VERSION
────────────────────────────────────────────────────────────────
CHANGES:
1. Fixed import: from neo4j import GraphDatabase
2. REMOVED partial match entirely (safety)
3. Added confidence scoring for all matches
4. Added proper error handling
5. Resolution cascade: exact → CAS → synonym → NOT FOUND
"""

import os
import re
import sys
from typing import Dict, List, Optional, Tuple

# CRITICAL FIX: Direct import from neo4j
from neo4j import GraphDatabase
from dotenv import load_dotenv

from servers.kg_server.queries import (
    RESOLVE_INGREDIENT_EXACT,
    RESOLVE_INGREDIENT_CAS,
    RESOLVE_INGREDIENT_SYNONYM,
    GET_FULL_PROFILE,
    GET_HAZARDS_LIST,
    GET_ORGANS_LIST,
    GET_EXPOSURE_LIMITS_LIST,
    HAS_CRITICAL_HAZARD,
    GET_ORGAN_FOR_MULTIPLE_CHEMICALS,
    TEST_QUERY,
)

load_dotenv()

# Critical H-codes
CRITICAL_H_CODES = {"H340", "H341", "H350", "H351", "H360", "H361", "H362", "H370", "H372"}

# Confidence scores
CONFIDENCE_SCORES = {
    "exact_match": 0.95,
    "cas_match": 0.95,
    "synonym_match": 0.85,
    "not_found": 0.0
}


class KGClient:

    def __init__(self):
        self.uri = os.getenv("NEO4J_URI")
        self.user = os.getenv("NEO4J_USER")
        self.password = os.getenv("NEO4J_PASSWORD")
        self.driver = None

    def connect(self):
        """Establish Neo4j connection with error handling"""
        if not self.uri or not self.user or not self.password:
            raise ValueError("Missing Neo4j credentials")
        
        self.driver = GraphDatabase.driver(
            self.uri, auth=(self.user, self.password)
        )
        try:
            with self.driver.session() as session:
                session.run("RETURN 1").single()
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Neo4j: {e}")
        return self.driver

    def close(self):
        if self.driver:
            self.driver.close()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _is_cas(self, text: str) -> bool:
        return bool(re.match(r'^\d{2,7}-\d{2}-\d$', text.strip()))

    def _normalize_name(self, name: str) -> str:
        return name.strip().upper()

    def _one(self, query: str, params: dict) -> dict:
        with self.driver.session() as s:
            rec = s.run(query, params).single()
            return dict(rec) if rec else None

    def _collect(self, query: str, params: dict) -> list:
        with self.driver.session() as s:
            rec = s.run(query, params).single()
            return rec[0] if rec else []

    # ── TOOL 1: resolve_ingredient (NO PARTIAL MATCH) ─────────────────────────

    def resolve_ingredient(self, name: str) -> dict:
        """
        Convert ingredient name → chemical uid.
        Cascade: exact → CAS → synonym → NOT FOUND
        NO PARTIAL MATCH - safety first.
        """
        original_name = name
        
        # STRATEGY 1: Exact match (highest confidence)
        result = self._one(RESOLVE_INGREDIENT_EXACT, {"name": name})
        if result and result.get("uid"):
            result["match_strategy"] = "exact_match"
            result["confidence"] = CONFIDENCE_SCORES["exact_match"]
            result["original_name"] = original_name
            result["unresolved"] = False
            return result

        # STRATEGY 2: CAS number match
        if self._is_cas(name):
            result = self._one(RESOLVE_INGREDIENT_CAS, {"name": name})
            if result and result.get("uid"):
                result["match_strategy"] = "cas_match"
                result["confidence"] = CONFIDENCE_SCORES["cas_match"]
                result["original_name"] = original_name
                result["unresolved"] = False
                return result

        # STRATEGY 3: Synonym match (checks synonyms array)
        result = self._one(RESOLVE_INGREDIENT_SYNONYM, {"name": name})
        if result and result.get("uid"):
            result["match_strategy"] = "synonym_match"
            result["confidence"] = CONFIDENCE_SCORES["synonym_match"]
            result["original_name"] = original_name
            result["unresolved"] = False
            return result

        # NOT FOUND - NO PARTIAL MATCH (safety decision)
        return {
            "original_name": original_name,
            "uid": None,
            "match_strategy": "not_found",
            "confidence": CONFIDENCE_SCORES["not_found"],
            "unresolved": True,
            "error": f"Chemical not found in KG: {original_name}",
            "suggestion": self._get_search_suggestion(original_name)
        }

    def _get_search_suggestion(self, name: str) -> Optional[str]:
        name_upper = name.upper()
        suggestions = {
            "AQUA": "Try 'WATER' (water may not be in KG)",
            "WATER": "Try 'AQUA' (water may not be in KG)",
            "SLS": "Try 'SODIUM LAURYL SULFATE'",
            "SLES": "Try 'SODIUM LAURETH SULFATE'",
            "PARFUM": "Fragrance - may be mixture of multiple chemicals",
        }
        for key, suggestion in suggestions.items():
            if key in name_upper:
                return suggestion
        return "Chemical may not be in KG - will use LLM estimate"

    # ── TOOL 2: get_hazard_profile ────────────────────────────────────────────

    def get_hazard_profile(self, uid: str) -> dict:
        hazards = self._collect(GET_HAZARDS_LIST, {"uid": uid})
        h_codes = [h["code"] for h in hazards if h.get("code")]
        signals = [h["signal"] for h in hazards if h.get("signal")]
        critical_found = [c for c in h_codes if c in CRITICAL_H_CODES]

        return {
            "uid": uid,
            "h_codes": h_codes,
            "highest_signal": "Danger" if "Danger" in signals
                              else ("Warning" if signals else "None"),
            "has_danger": "Danger" in signals,
            "has_critical_hazard": len(critical_found) > 0,
            "critical_hazards": critical_found,
            "hazard_count": len(hazards),
            "hazards": hazards,
            "confidence": 0.9 if h_codes else (0.5 if hazards else 0.3)
        }

    # ── TOOL 3: get_full_profile ──────────────────────────────────────────────

    def get_full_profile(self, uid: str) -> dict:
        r = self._one(GET_FULL_PROFILE, {"uid": uid})
        if not r:
            return {"uid": uid, "error": "Chemical not found", "unresolved": True}

        hazards = r.get("hazards") or []
        h_codes = [h["code"] for h in hazards if h.get("code")]
        signals = [h["signal"] for h in hazards if h.get("signal")]
        critical = [c for c in h_codes if c in CRITICAL_H_CODES]

        # Calculate confidence based on data completeness
        confidence = 0.3  # base for having UID
        if h_codes:
            confidence += 0.4
        if r.get("target_organs"):
            confidence += 0.2
        if r.get("toxicity"):
            confidence += 0.1
        confidence = min(round(confidence, 2), 1.0)

        return {
            "uid": r.get("uid"),
            "name": r.get("name"),
            "preferred_name": r.get("preferred_name"),
            "cas": r.get("cas"),
            "molecular_formula": r.get("molecular_formula"),
            "molecular_weight": r.get("molecular_weight"),
            "description": r.get("description"),
            "synonyms": r.get("synonyms") or [],
            "highest_signal": "Danger" if "Danger" in signals
                              else ("Warning" if signals else "None"),
            "has_danger": "Danger" in signals,
            "has_critical_hazard": len(critical) > 0,
            "critical_hazards": critical,
            "h_codes": h_codes,
            "hazards": hazards,
            "target_organs": [o for o in (r.get("target_organs") or []) if o],
            "chemical_classes": [c for c in (r.get("chemical_classes") or []) if c],
            "toxicity": [t for t in (r.get("toxicity") or []) if t.get("type")],
            "exposure_limits": [e for e in (r.get("exposure_limits") or []) if e.get("standard")],
            "skin_effects": [e for e in (r.get("skin_effects") or []) if e],
            "eye_effects": [e for e in (r.get("eye_effects") or []) if e],
            "inhalation_effects": [e for e in (r.get("inhalation_effects") or []) if e],
            "ingestion_effects": [e for e in (r.get("ingestion_effects") or []) if e],
            "excretion_routes": [e for e in (r.get("excretion_routes") or []) if e],
            "data_confidence": confidence
        }

    # ── TOOL 4: get_target_organs ─────────────────────────────────────────────

    def get_target_organs(self, uid: str) -> dict:
        organs = self._collect(GET_ORGANS_LIST, {"uid": uid})
        return {
            "uid": uid,
            "organs": [o for o in organs if o],
            "count": len([o for o in organs if o]),
            "confidence": 0.8 if organs else 0.3
        }

    # ── TOOL 5: get_exposure_limits ───────────────────────────────────────────

    def get_exposure_limits(self, uid: str) -> dict:
        limits = self._collect(GET_EXPOSURE_LIMITS_LIST, {"uid": uid})
        valid_limits = [l for l in limits if l.get("standard")]
        return {
            "uid": uid,
            "exposure_limits": valid_limits,
            "count": len(valid_limits),
            "has_limits": len(valid_limits) > 0,
            "confidence": 0.8 if valid_limits else 0.2
        }

    # ── utility for combination server ───────────────────────────────────────

    def get_organs_for_multiple(self, uids: list) -> list:
        with self.driver.session() as s:
            return [dict(r) for r in
                    s.run(GET_ORGAN_FOR_MULTIPLE_CHEMICALS, {"uids": uids})]

    def test_connection(self) -> bool:
        try:
            with self.driver.session() as s:
                s.run("RETURN 1").single()
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            return False


if __name__ == "__main__":
    client = KGClient()
    client.connect()

    print("=" * 60)
    print("KG CLIENT — PRODUCTION VERSION (NO PARTIAL MATCH)")
    print("=" * 60)

    # Test AQUA - should NOT match anything
    r = client.resolve_ingredient("AQUA")
    print(f"\n[TEST 1] resolve_ingredient('AQUA'):")
    print(f"  Strategy: {r.get('match_strategy')}")
    print(f"  UID: {r.get('uid')}")
    print(f"  Unresolved: {r.get('unresolved')}")
    print(f"  Suggestion: {r.get('suggestion')}")

    # Test SLS - should match
    r = client.resolve_ingredient("Sodium Lauryl Sulfate")
    print(f"\n[TEST 2] resolve_ingredient('Sodium Lauryl Sulfate'):")
    print(f"  Strategy: {r.get('match_strategy')}")
    print(f"  UID: {r.get('uid')}")
    print(f"  Confidence: {r.get('confidence')}")

    client.close()
    print("\n✅ KG CLIENT READY")