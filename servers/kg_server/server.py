"""
server.py — MCP Server for Neo4j Knowledge Graph
──────────────────────────────────────────────────
WHAT CHANGED FROM ORIGINAL:
  1. Tools reduced from 14 to 5 — token budget fix
     14 tools × ~300 tokens = ~4200 tokens just for schemas
     5 tools × ~300 tokens = ~1500 tokens
     This gives the LLM room to actually reason within Groq 6000 TPM

  2. Tool names updated:
     get_complete_profile → get_full_profile (clearer intent)
     get_hazard_statements + has_critical_hazard → get_hazard_profile (merged)

  3. Removed tools (all covered by get_full_profile):
     get_chemical_classes, get_toxicity_profile, get_excretion_routes,
     get_skin_effects, get_eye_effects, get_inhalation_effects,
     get_ingestion_effects, batch_resolve, has_critical_hazard

  4. Tool descriptions made agent-readable:
     Not just "Get skin effects" but WHY the agent would call this tool.
     The LLM reads descriptions to decide which tool to call.

  5. MCP protocol: unchanged — was correct.
"""

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from kg_client import KGClient

kg = KGClient()
kg.connect()

# ── 5 tool schemas — sized for Groq free tier ─────────────────────────────────

TOOLS = [
    {
        "name": "resolve_ingredient",
        "description": (
            "Convert an ingredient name to its Neo4j chemical UID. "
            "Always call this first before any other KG tool. "
            "Returns uid=None with unresolved=true if not found — "
            "unresolved means unknown risk, not safe."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ingredient_name": {
                    "type": "string",
                    "description": "Ingredient name as it appears on the product label"
                }
            },
            "required": ["ingredient_name"]
        }
    },
    {
        "name": "get_hazard_profile",
        "description": (
            "Get GHS hazard classification for a resolved chemical. "
            "Returns H-codes, highest signal (Danger/Warning/None), "
            "and whether the chemical has critical hazards "
            "(carcinogen H350, mutagen H340, reprotoxic H360). "
            "Call this after resolve_ingredient to decide investigation depth."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemical_uid": {
                    "type": "string",
                    "description": "Chemical UID from resolve_ingredient"
                }
            },
            "required": ["chemical_uid"]
        }
    },
    {
        "name": "get_full_profile",
        "description": (
            "Get complete chemical data in one call: identity, all hazards, "
            "target organs, chemical classes, toxicity measures, exposure limits, "
            "and all exposure route effects (skin/eye/inhalation/ingestion). "
            "Use for deep investigation of HIGH or CRITICAL chemicals. "
            "Expensive — do not call for every chemical, only when justified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemical_uid": {
                    "type": "string",
                    "description": "Chemical UID from resolve_ingredient"
                }
            },
            "required": ["chemical_uid"]
        }
    },
    {
        "name": "get_target_organs",
        "description": (
            "Get the list of organs this chemical affects. "
            "Use this when you need organ data for combination analysis "
            "without fetching the full profile. "
            "Essential input for check_organ_overlap in the combination server."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemical_uid": {
                    "type": "string",
                    "description": "Chemical UID from resolve_ingredient"
                }
            },
            "required": ["chemical_uid"]
        }
    },
    {
        "name": "get_exposure_limits",
        "description": (
            "Get regulatory exposure limits (OSHA PEL, EU OEL, ACGIH TLV). "
            "Without dose data these limits cannot be compared directly, "
            "but their existence signals regulatory concern. "
            "A chemical with strict limits is inherently higher risk."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemical_uid": {
                    "type": "string",
                    "description": "Chemical UID from resolve_ingredient"
                }
            },
            "required": ["chemical_uid"]
        }
    },
]


# ── request handler ───────────────────────────────────────────────────────────

def handle(request: dict) -> dict | None:
    method = request.get("method")
    rid    = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": "0.1.0",
                "capabilities":    {"tools": {}},
                "serverInfo":      {"name": "kg-server", "version": "2.0.0"},
            }
        }

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {"tools": TOOLS}
        }

    if method == "tools/call":
        tool_name = request.get("params", {}).get("name")
        args      = request.get("params", {}).get("arguments", {})

        dispatch = {
            "resolve_ingredient":  lambda: kg.resolve_ingredient(args.get("ingredient_name")),
            "get_hazard_profile":  lambda: kg.get_hazard_profile(args.get("chemical_uid")),
            "get_full_profile":    lambda: kg.get_full_profile(args.get("chemical_uid")),
            "get_target_organs":   lambda: kg.get_target_organs(args.get("chemical_uid")),
            "get_exposure_limits": lambda: kg.get_exposure_limits(args.get("chemical_uid")),
        }

        if tool_name not in dispatch:
            return {
                "jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }

        try:
            result = dispatch[tool_name]()
            return {
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0", "id": rid,
                "error": {
                    "code": -32000,
                    "message": str(e),
                    "data": traceback.format_exc()
                }
            }

    return {
        "jsonrpc": "2.0", "id": rid,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    }


# ── stdio loop ────────────────────────────────────────────────────────────────

def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            response = handle(json.loads(line))
            if response is not None:
                print(json.dumps(response), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)
    kg.close()


if __name__ == "__main__":
    main()