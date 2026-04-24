"""
servers/evaluation_server/server.py
──────────────────────────────────────
MCP SERVER — Evaluation / Investigation Metrics
Transport: stdio (JSON-RPC 2.0)

1 tool:
  get_investigation_metrics — structured signals for LLM depth decisions

Zero LLM. Zero Neo4j. Pure Python.
"""

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from servers.evaluation_server.evaluator import get_investigation_metrics

TOOLS = [
    {
        "name": "get_investigation_metrics",
        "description": (
            "Analyze KG results and return structured signals to guide investigation depth. "
            "Call this after resolve_ingredient + get_hazard_profile for each chemical. "
            "Returns recommended_depth (full/basic/skip), preliminary_risk level, "
            "and reasoning. The LLM uses these signals to decide whether to call "
            "get_full_profile or move on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemical_name": {
                    "type": "string",
                    "description": "Original ingredient name from product label"
                },
                "resolution_result": {
                    "type": "object",
                    "description": "Output of resolve_ingredient tool"
                },
                "hazard_result": {
                    "type": "object",
                    "description": "Output of get_hazard_profile tool (empty dict if not fetched)"
                }
            },
            "required": ["chemical_name", "resolution_result", "hazard_result"]
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
                "serverInfo":      {"name": "evaluation-server", "version": "1.0.0"},
            }
        }

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = request.get("params", {}).get("name")
        args      = request.get("params", {}).get("arguments", {})

        if tool_name != "get_investigation_metrics":
            return {
                "jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }

        try:
            result = get_investigation_metrics(
                chemical_name     = args.get("chemical_name", ""),
                resolution_result = args.get("resolution_result", {}),
                hazard_result     = args.get("hazard_result", {}),
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