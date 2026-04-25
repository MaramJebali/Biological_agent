"""
servers/combination_server/server.py
MCP SERVER — Combination Analysis
Transport: stdio (JSON-RPC 2.0)
3 tools with global_mode support
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
            "Find organs targeted by 2 or more chemicals.\n\n"
            "TWO MODES:\n"
            "1. Per-product mode (global_mode=false): returns organ overlap within a single product.\n"
            "2. Global mode (global_mode=true): returns cross-product aggregation with unique chemicals per organ, "
            "chemical frequency across products, and products per chemical. Input chemicals must include 'product_id' field."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemicals": {"type": "array", "items": {"type": "object"}},
                "global_mode": {"type": "boolean", "default": False}
            },
            "required": ["chemicals"]
        }
    },
    {
        "name": "check_cumulative_presence",
        "description": "Check if the same chemical appears in multiple products.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemical_name": {"type": "string"},
                "products": {"type": "array", "items": {"type": "object"}}
            },
            "required": ["chemical_name", "products"]
        }
    },
    {
        "name": "check_hazard_intersection",
        "description": "Find H-codes shared across 2 or more chemicals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chemicals": {"type": "array", "items": {"type": "object"}}
            },
            "required": ["chemicals"]
        }
    },
]


def handle(request: dict) -> dict | None:
    method = request.get("method")
    rid = request.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "0.1.0", "capabilities": {"tools": {}}, "serverInfo": {"name": "combination-server", "version": "2.0.0"}}}
    
    if method in ("notifications/initialized", "initialized"):
        return None
    
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    
    if method == "tools/call":
        tool_name = request.get("params", {}).get("name")
        args = request.get("params", {}).get("arguments", {})
        
        try:
            if tool_name == "check_organ_overlap":
                result = check_organ_overlap(
                    chemicals=args.get("chemicals", []),
                    global_mode=args.get("global_mode", False)
                )
            elif tool_name == "check_cumulative_presence":
                result = check_cumulative_presence(
                    chemical_name=args.get("chemical_name", ""),
                    products=args.get("products", [])
                )
            elif tool_name == "check_hazard_intersection":
                result = check_hazard_intersection(chemicals=args.get("chemicals", []))
            else:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
            
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(e), "data": traceback.format_exc()}}
    
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


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