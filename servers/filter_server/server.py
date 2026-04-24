"""
servers/filter_server/server.py
─────────────────────────────────
MCP SERVER — Ingredient Filter
Transport: stdio (JSON-RPC 2.0)

REWRITTEN: was FastMCP, now raw JSON-RPC.
Reason: all servers must use the same protocol so the MCPClient
in agent/agent.py can connect to them uniformly via subprocess.
FastMCP uses a different startup handshake that breaks our client.

1 tool:
  classify_ingredients — uses Groq LLM to separate chemicals from safe fillers

Why LLM and not a hardcoded list:
  OCR and barcode ingredient names are messy and inconsistent.
  "Aqua", "Water", "Eau", "Purified Water", "H2O" are all water.
  A fixed list misses thousands of variants. LLM handles this robustly.
"""

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from servers.filter_server.classifier import classify_with_groq

TOOLS = [
    {
        "name": "classify_ingredients",
        "description": (
            "Classify each ingredient as 'chemical' or 'safe' using Groq LLM. "
            "Call this FIRST before any KG queries to avoid wasting Neo4j calls "
            "on water, oils, and plant extracts. "
            "Returns chemicals (list to investigate) and safe_skipped (list to ignore). "
            "When uncertain → classifies as chemical (conservative). "
            "Unknown/unrecognised → chemical with unverified=True."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ingredients": {
                    "type": "array",
                    "description": "List of {name: str} dicts from product label",
                    "items": {"type": "object"}
                },
                "usage": {
                    "type": "string",
                    "description": "Product type: cosmetic | food | detergent | pharmaceutical",
                    "default": "cosmetic"
                }
            },
            "required": ["ingredients"]
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
                "serverInfo":      {"name": "filter-server", "version": "2.0.0"},
            }
        }

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = request.get("params", {}).get("name")
        args      = request.get("params", {}).get("arguments", {})

        if tool_name != "classify_ingredients":
            return {
                "jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }

        try:
            result = classify_with_groq(
                ingredients=args.get("ingredients", []),
                usage=args.get("usage", "cosmetic"),
            )
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