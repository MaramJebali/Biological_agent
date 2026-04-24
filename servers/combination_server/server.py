"""
servers/combination_server/server.py
──────────────────────────────────────
MCP SERVER — Combination Analysis
Transport: stdio (JSON-RPC 2.0)

Style: raw JSON-RPC (same as kg_server — standardized, no FastMCP)
Reason: FastMCP and raw JSON-RPC cannot both be used in the same project
        without confusion at the MCP client layer.
        Using raw JSON-RPC for ALL servers for consistency.

3 tools:
  check_organ_overlap        — organs hit by 2+ chemicals
  check_cumulative_presence  — same chemical in multiple products
  check_hazard_intersection  — H-codes shared across chemicals

Zero LLM. Zero Neo4j. Pure Python logic.
"""

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from servers.combination_server.synergies import (
    check_organ_overlap,
    check_cumulative_presence,
    check_hazard_intersection,
)

TOOLS = [
    {
        "name": "check_organ_overlap",
        "description": (
            "Find organs targeted by 2 or more chemicals in the product set. "
            "Call this after fetching target_organs for all resolved chemicals. "
            "Returns verdict_escalation: HIGH if 3+ chemicals share an organ, "
            "MODERATE if 2 chemicals share an organ. "
            "Also detects chemicals with H370-H373 (organ damage) but no explicit organ node."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemicals": {
                    "type": "array",
                    "description": (
                        "List of chemical dicts, each with: "
                        "name (str), uid (str|null), "
                        "target_organs (list[str]), h_codes (list[str])"
                    ),
                    "items": {"type": "object"}
                }
            },
            "required": ["chemicals"]
        }
    },
    {
        "name": "check_cumulative_presence",
        "description": (
            "Check if the same chemical appears in multiple products. "
            "Call this for each chemical that appears in 2+ products. "
            "Without dose data this is a presence-based cumulative risk signal — "
            "the user encounters this chemical multiple times per routine."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemical_name": {
                    "type": "string",
                    "description": "Name of the chemical to check"
                },
                "products": {
                    "type": "array",
                    "description": "List of {product_id, product_name} dicts containing this chemical",
                    "items": {"type": "object"}
                }
            },
            "required": ["chemical_name", "products"]
        }
    },
    {
        "name": "check_hazard_intersection",
        "description": (
            "Find H-codes shared across 2 or more chemicals. "
            "Shared hazards amplify total risk. "
            "Shared critical H-codes (H350, H340, H360) are especially concerning. "
            "Returns severity_escalation=true if critical H-codes are shared."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemicals": {
                    "type": "array",
                    "description": "List of {name: str, h_codes: list[str]} dicts",
                    "items": {"type": "object"}
                }
            },
            "required": ["chemicals"]
        }
    },
]


def handle(request: dict) -> dict | None:
    method = request.get("method")
    rid    = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": "0.1.0",
                "capabilities":    {"tools": {}},
                "serverInfo":      {"name": "combination-server", "version": "1.0.0"},
            }
        }

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = request.get("params", {}).get("name")
        args      = request.get("params", {}).get("arguments", {})

        dispatch = {
            "check_organ_overlap":       lambda: check_organ_overlap(
                args.get("chemicals", [])
            ),
            "check_cumulative_presence": lambda: check_cumulative_presence(
                args.get("chemical_name", ""),
                args.get("products", [])
            ),
            "check_hazard_intersection": lambda: check_hazard_intersection(
                args.get("chemicals", [])
            ),
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
                    "code":    -32000,
                    "message": str(e),
                    "data":    traceback.format_exc(),
                }
            }

    return {
        "jsonrpc": "2.0", "id": rid,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    }


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


if __name__ == "__main__":
    main()