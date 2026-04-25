#!/usr/bin/env python3
"""
MCP HOST - Biological Agent Entry Point
─────────────────────────────────────────
Usage:
    python main.py                          # runs with built-in example
    python main.py --input products.json    # runs with your JSON file
    python main.py --output report.json     # saves report to file
    python main.py --verbose                # shows chain of thought

This is the MCP HOST - it:
- Loads configuration
- Creates and runs the MCP Client (BiologicalAgent)
- Displays chain of thought
- Outputs final report
"""

import asyncio
import json
import sys
import os
import argparse
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from agent.agent import BiologicalAgent, run_evaluation


# ============================================================
# CHAIN OF THOUGHT LOGGER (for MCP Host)
# ============================================================

class ChainOfThoughtLogger:
    """
    Displays the agent's chain of thought in the console.
    This runs in the HOST, not in the CLIENT.
    """
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.step_num = 0
        self.start_time = None
        self.indent = 0
    
    def start(self):
        self.start_time = time.time()
        self._print_section("🧠 BIOLOGICAL AGENT STARTED")
        self._print(f"Agent version: 2.0.0")
        self._print(f"Mode: {'Verbose' if self.verbose else 'Quiet'}")
        self._print(f"Started at: {datetime.now().isoformat()}")
        self._print("")
    
    def _print(self, msg: str, level: str = "info"):
        if not self.verbose and level == "debug":
            return
        print(msg)
        sys.stdout.flush()
    
    def _print_section(self, title: str):
        print("\n" + "=" * 70)
        print(f"  {title}")
        print("=" * 70)
    
    def _print_subsection(self, title: str):
        print(f"\n  📌 {title}")
        print("  " + "-" * 50)
    
    def log_input_parsing(self, products_list: List[Dict]):
        self._print_subsection("INPUT PARSING")
        self._print(f"  📦 Received {len(products_list)} product(s)")
        for i, p in enumerate(products_list):
            pid = p.get("product_id", "?")
            name = p.get("product_name", "Unknown")
            usage = p.get("product_usage", "unknown")
            exposure = p.get("exposure_type", "unknown")
            ingredients = len(p.get("ingredient_list", []))
            self._print(f"     Product {i+1}: {name} (ID: {pid})")
            self._print(f"        Usage: {usage} | Exposure: {exposure} | {ingredients} ingredients")
    
    def log_product_context(self, context: dict):
        self._print_subsection("PRODUCT CONTEXT ANALYSIS")
        self._print(f"  📊 Product count: {context.get('product_count')}")
        self._print(f"  🔄 Needs cumulative analysis: {context.get('needs_cumulative')}")
        self._print(f"  🔀 Mixed usage types: {context.get('has_mixed_usage')}")
        self._print(f"  🎯 Strategy: {context.get('strategy')}")
    
    def log_server_connection(self, server_name: str, tools_count: int):
        if self.verbose:
            self._print(f"  ✅ {server_name} server: {tools_count} tools", "debug")
    
    def log_filter_result(self, chemicals: list, safe_skipped: list):
        self._print_subsection("FILTER RESULTS")
        self._print(f"  🔬 Chemicals to analyze: {len(chemicals)}")
        self._print(f"  ✅ Safe to skip: {len(safe_skipped)}")
        if self.verbose and chemicals:
            self._print(f"     Chemicals: {', '.join(chemicals[:10])}{'...' if len(chemicals) > 10 else ''}")
    
    def log_chemical_investigation_start(self, total: int):
        self._print_subsection("CHEMICAL INVESTIGATION")
        self._print(f"  🔍 Processing {total} chemical(s)...")
    
    def log_chemical_resolution(self, name: str, uid: Optional[str], match_strategy: str):
        if uid:
            self._print(f"     🔬 {name}: RESOLVED → {uid[:20]}... ({match_strategy})")
        else:
            self._print(f"     🔬 {name}: UNRESOLVED → using LLM fallback")
    
    def log_chemical_hazard(self, name: str, risk_level: str, h_codes: list, signal: str):
        risk_emoji = {
            "CRITICAL": "🔴", "HIGH": "🟠", "MODERATE": "🟡", "LOW": "🟢", "SAFE": "✅", "UNKNOWN": "❓"
        }.get(risk_level, "⚪")
        h_preview = ', '.join(h_codes[:3])
        self._print(f"        {risk_emoji} Risk: {risk_level} | Signal: {signal} | H-codes: {h_preview}{'...' if len(h_codes) > 3 else ''}")
    
    def log_deep_investigation(self, name: str, organs: list):
        self._print(f"        🔍 Deep investigation: target organs = {organs}")
    
    def log_confidence(self, name: str, confidence: float, source: str):
        if self.verbose:
            self._print(f"        📊 Confidence: {confidence:.2f} ({source})", "debug")
    
    def log_combination_start(self):
        self._print_subsection("COMBINATION ANALYSIS")
    
    def log_organ_overlap(self, overlaps: dict):
        if overlaps:
            self._print(f"  🧠 Organ overlaps detected:")
            for organ, data in overlaps.items():
                if isinstance(data, dict):
                    chemicals = data.get("chemicals", [])
                    count = data.get("total_unique_count", len(chemicals))
                    self._print(f"     • {organ}: {count} unique chemical(s)")
                    if self.verbose:
                        self._print(f"       Chemicals: {', '.join(chemicals[:5])}{'...' if len(chemicals) > 5 else ''}")
                elif isinstance(data, list):
                    for o in data:
                        self._print(f"     • {o.get('organ')}: {o.get('count')} chemical(s)")
        else:
            self._print(f"  ✅ No significant organ overlaps detected")
    
    def log_cumulative_presence(self, cumulative: list):
        if cumulative:
            self._print(f"  📦 Cumulative exposure detected:")
            for c in cumulative:
                self._print(f"     • {c.get('chemical_name')} appears in {c.get('frequency')} products")
    
    def log_escalation_enforcement(self, escalation: str):
        if escalation == "HIGH":
            self._print(f"\n  ⚠️ ESCALATION ENFORCED: verdict_escalation = HIGH")
            self._print(f"     → Product risk levels forced to HIGH")
    
    def log_completion(self, elapsed: float, report_keys: list):
        self._print_section("AGENT COMPLETE")
        self._print(f"  ✅ Analysis completed in {elapsed:.1f} seconds")
        self._print(f"  📄 Report contains: {', '.join(report_keys)}")
    
    def finish(self, elapsed: float):
        print(f"\n✅ Analysis completed in {elapsed:.1f} seconds")
        return elapsed


# ============================================================
# OUTPUT FORMATTERS
# ============================================================

def format_report_summary(report: dict) -> None:
    """Pretty print report summary to console"""
    print("\n" + "=" * 70)
    print("  FINAL REPORT SUMMARY")
    print("=" * 70)
    
    # Product verdicts
    verdicts = report.get("product_verdicts", [])
    if verdicts:
        print("\n  📦 PRODUCT VERDICTS:")
        for v in verdicts:
            risk = v.get("risk_level", "UNKNOWN")
            emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MODERATE": "🟡", "LOW": "🟢", "SAFE": "✅"}.get(risk, "⚪")
            print(f"     {emoji} {v.get('product_name', 'Unknown'):30s} → {risk}")
    
    # High risk chemicals
    chemicals = report.get("chemicals_summary", [])
    high_risk = [c for c in chemicals if c.get("risk_level") in ["CRITICAL", "HIGH"]]
    if high_risk:
        print("\n  ⚠️ HIGH RISK CHEMICALS:")
        for c in high_risk[:10]:
            print(f"     • {c.get('name')}: {c.get('risk_level')} (confidence: {c.get('confidence', 0):.2f})")
    
    # Organ overlaps
    combo = report.get("combination_risks", {})
    organ_summary = combo.get("organ_overlap_summary")
    if organ_summary and organ_summary != "No organ overlap detected":
        print(f"\n  🧠 ORGAN OVERLAP: {organ_summary}")
    
    # Recommendations
    print("\n  📋 RECOMMENDATIONS:")
    recommendations = []
    for v in verdicts:
        rec = v.get("recommendation", "")
        if rec == "avoid":
            recommendations.append(f"   • {v.get('product_name')}: AVOID")
        elif rec == "reduce_use":
            recommendations.append(f"   • {v.get('product_name')}: REDUCE USE")
        elif rec == "keep":
            recommendations.append(f"   • {v.get('product_name')}: SAFE TO USE")
    
    if recommendations:
        for r in recommendations:
            print(r)
    else:
        print("   • No specific recommendations")


def save_report_to_file(report: dict, output_path: str) -> None:
    """Save report to JSON file"""
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  💾 Report saved to: {output_path}")


# ============================================================
# MCP HOST - MAIN ENTRY POINT
# ============================================================

async def run_host(products_list: List[Dict], verbose: bool = True, logger: ChainOfThoughtLogger = None) -> Dict:
    """
    Run the MCP Client and capture chain of thought.
    This is the HOST: creates client, runs it, returns report.
    """
    if logger is None:
        logger = ChainOfThoughtLogger(verbose)
    
    logger.start()
    
    # Create MCP Client
    client = BiologicalAgent()
    
    # Log client initialization
    print("\n  🚀 Initializing MCP Client...")
    
    # Run client
    result = await client.run(products_list)
    
    # Log completion
    elapsed = result.get("elapsed_s", 0)
    report = result.get("report", {})
    logger.log_completion(elapsed, list(report.keys()) if report else [])
    
    return result


def load_input(input_path: Optional[str] = None) -> List[Dict]:
    """Load products_list from file or use example"""
    if input_path:
        with open(input_path, 'r') as f:
            data = json.load(f)
            if "products_list" in data:
                return data["products_list"]
            elif isinstance(data, list):
                return data
            else:
                return [data]
    else:
        # Built-in example (Sarah's two products)
        return [
            {
                "product_id": "1",
                "product_name": "Moisturizing Cream",
                "product_usage": "cosmetics",
                "exposure_type": "skin",
                "ingredient_list": [
                    {"name": "AQUA"},
                    {"name": "SODIUM LAURETH SULFATE"},
                    {"name": "COCO-BETAINE"},
                    {"name": "SODIUM CHLORIDE"},
                    {"name": "PARFUM"},
                    {"name": "CITRIC ACID"},
                ]
            },
            {
                "product_id": "2",
                "product_name": "Perfume Spray",
                "product_usage": "cosmetics",
                "exposure_type": "skin",
                "ingredient_list": [
                    {"name": "ALCOHOL DENAT."},
                    {"name": "PARFUM"},
                    {"name": "LIMONENE"},
                    {"name": "LINALOOL"},
                ]
            }
        ]


async def main():
    """MCP HOST - Main entry point"""
    parser = argparse.ArgumentParser(
        description="Biological Agent - MCP Host for Chemical Safety Analysis",
        epilog="Example: python main.py --input products.json --output report.json --verbose"
    )
    parser.add_argument("--input", "-i", help="Path to input JSON file")
    parser.add_argument("--output", "-o", help="Path to save output JSON report")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed chain of thought")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress chain of thought output")
    args = parser.parse_args()
    
    verbose = args.verbose and not args.quiet
    
    # Validate credentials
    try:
        config.validate()
        if verbose:
            print("✅ Configuration validated")
            print(f"   Groq model: {config.GROQ_MODEL}")
            print(f"   Neo4j URI: {config.NEO4J_URI}")
    except EnvironmentError as e:
        print(f"❌ Configuration error:\n{e}")
        sys.exit(1)
    
    # Load input
    try:
        products_list = load_input(args.input)
        if verbose:
            print(f"\n📦 Loaded {len(products_list)} product(s)")
    except Exception as e:
        print(f"❌ Failed to load input: {e}")
        sys.exit(1)
    
    # Create logger
    logger = ChainOfThoughtLogger(verbose)
    
    # Run host
    try:
        start_time = time.time()
        result = await run_host(products_list, verbose, logger)
        elapsed = time.time() - start_time
        
        # Display report
        report = result.get("report", {})
        format_report_summary(report)
        
        # Save if requested
        if args.output:
            save_report_to_file(report, args.output)
        
        # Also print full JSON if verbose
        if verbose:
            print("\n" + "=" * 70)
            print("  FULL REPORT JSON")
            print("=" * 70)
            print(json.dumps(report, indent=2)[:2000] + ("\n  ... (truncated)" if len(json.dumps(report)) > 2000 else ""))
        
        print(f"\n✅ MCP Host completed in {elapsed:.1f} seconds")
        
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ MCP Host failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())