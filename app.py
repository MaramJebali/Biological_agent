"""
Streamlit Interface for Biological Agent - PRODUCTION VERSION
───────────────────────────────────────────────────────────────
Run: streamlit run app.py

FIXES APPLIED:
1. Variable scope: global_organ_analysis initialized BEFORE conditional
2. Product tracking: chemical_id_map stores list of product IDs
3. Removed partial match dependency (handled by KG server)
4. Added token budget awareness
"""

import streamlit as st
import asyncio
import json
import sys
import os
import time
import subprocess
from datetime import datetime
from typing import Dict, Any, List, Optional
from collections import defaultdict

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config


# ============================================================
# MCP CLIENT FOR STREAMLIT (Synchronous)
# ============================================================

class SyncMCPClient:
    """Synchronous MCP Client for Windows compatibility"""
    
    def __init__(self, name: str, server_path: str, logger):
        self.name = name
        self.server_path = server_path
        self.logger = logger
        self.process = None
    
    def start(self):
        """Start the MCP server process (synchronous)"""
        full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.server_path)
        self.process = subprocess.Popen(
            [sys.executable, full_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        time.sleep(0.5)
        return self
    
    def call(self, tool_name: str, arguments: Dict) -> Dict:
        """Call a tool synchronously"""
        self.logger.log_tool_call(self.name, tool_name, arguments)
        
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
            "id": 1
        }
        
        request_json = json.dumps(request) + "\n"
        
        try:
            self.process.stdin.write(request_json)
            self.process.stdin.flush()
            
            response_line = self.process.stdout.readline()
            if response_line:
                response = json.loads(response_line)
                if "result" in response and "content" in response["result"]:
                    content = response["result"]["content"]
                    if content and len(content) > 0:
                        text = content[0].get("text", "{}")
                        try:
                            result = json.loads(text)
                            self.logger.log_tool_result(result, f"Returned {len(str(result))} bytes")
                            return result
                        except:
                            return {"raw": text}
                return response
            return {"error": "No response"}
        except Exception as e:
            return {"error": str(e)}
    
    def list_tools(self) -> List[Dict]:
        """List available tools"""
        request = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        request_json = json.dumps(request) + "\n"
        
        try:
            self.process.stdin.write(request_json)
            self.process.stdin.flush()
            
            response_line = self.process.stdout.readline()
            if response_line:
                response = json.loads(response_line)
                if "result" in response and "tools" in response["result"]:
                    return response["result"]["tools"]
            return []
        except Exception as e:
            self.logger.log_error(f"Error listing tools for {self.name}", str(e))
            return []
    
    def stop(self):
        """Stop the server process"""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except:
                self.process.kill()


class StreamlitAgent:
    """Real agent that logs all operations to Streamlit"""
    
    def __init__(self, logger):
        self.logger = logger
        self.clients = {}
        self.server_paths = {
            "kg": "servers/kg_server/server.py",
            "filter": "servers/filter_server/server.py",
            "combination": "servers/combination_server/server.py",
            "evaluation": "servers/evaluation_server/server.py",
        }
        self.token_calls = 0
        self.max_token_calls = 5  # Limit LLM calls per run
    
    def _can_call_llm(self) -> bool:
        """Check if we can make another LLM call (token budget)"""
        if self.token_calls >= self.max_token_calls:
            self.logger.log_step("Token Limit", f"Reached max LLM calls ({self.max_token_calls}) - using fallback", icon="⚠️")
            return False
        return True
    
    def _record_llm_call(self):
        """Record an LLM call"""
        self.token_calls += 1
        self.logger.log_step("Token Budget", f"LLM call {self.token_calls}/{self.max_token_calls}", icon="💰")
    
    def initialize(self):
        """Initialize all MCP servers"""
        self.logger.log_step("Initializing MCP Servers", "Connecting to all 4 MCP servers...")
        
        for name, path in self.server_paths.items():
            full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
            if os.path.exists(full_path):
                try:
                    client = SyncMCPClient(name, full_path, self.logger)
                    client.start()
                    self.clients[name] = client
                    tools = client.list_tools()
                    self.logger.log_step(f"Server: {name}", f"Connected, {len(tools)} tools available", icon="✅")
                except Exception as e:
                    self.logger.log_step(f"Server: {name}", f"FAILED to start: {str(e)[:100]}", icon="❌")
            else:
                self.logger.log_step(f"Server: {name}", f"NOT FOUND at {path}", icon="⚠️")
    
    def shutdown(self):
        """Shutdown all servers"""
        for client in self.clients.values():
            try:
                client.stop()
            except:
                pass
    
    def _map_h_codes_to_risk(self, h_codes: list) -> str:
        """Map H-codes to risk level"""
        critical = {"H340", "H350", "H360"}
        high = {"H315", "H317", "H318", "H319", "H334"}
        moderate = {"H302", "H312", "H332", "H335"}
        
        for code in h_codes:
            if code in critical:
                return "CRITICAL"
            if code in high:
                return "HIGH"
            if code in moderate:
                return "MODERATE"
        return "LOW" if h_codes else "UNKNOWN"
    
    def evaluate(self, products_list: List[Dict]) -> Dict:
        """Run real evaluation with full MCP tool calls"""
        
        # Reset token counter for this run
        self.token_calls = 0
        
        self.logger.log_step("Input Products", f"Analyzing {len(products_list)} product(s)")
        
        # Phase 1: Product context analysis
        product_count = len(products_list)
        needs_cumulative = product_count >= 2
        self.logger.log_step("Product Context", 
                            f"Products: {product_count} | Needs cumulative: {needs_cumulative}")
        
        # Step 1: Extract all ingredients
        all_ingredients = []
        for product in products_list:
            for ing in product.get("ingredient_list", []):
                name = ing.get("name", "").strip()
                if name:
                    all_ingredients.append(name)
        
        self.logger.log_step("Extract Ingredients", f"Found {len(all_ingredients)} total ingredients")
        
        # Step 2: Filter ingredients
        self.logger.log_step("Filtering Ingredients", "Calling filter_server.classify_ingredients...")
        
        # Deduplicate ingredients before sending to filter server
        unique_ingredients = list(dict.fromkeys(all_ingredients))
        ingredients_list = [{"name": ing} for ing in unique_ingredients]
        
        if "filter" in self.clients:
            filter_result = self.clients["filter"].call("classify_ingredients", {
                "ingredients": ingredients_list,
                "usage": products_list[0].get("product_usage", "cosmetic") if products_list else "cosmetic"
            })
            
            if isinstance(filter_result, dict):
                chemicals = [c.get("name") for c in filter_result.get("chemicals", [])]
                safe_skipped = [s.get("name") for s in filter_result.get("safe_skipped", [])]
            else:
                chemicals = unique_ingredients
                safe_skipped = []
        else:
            chemicals = unique_ingredients
            safe_skipped = []
        
        self.logger.log_step("Filter Results", 
                            f"🔬 Chemicals: {len(chemicals)} | ✅ Safe skipped: {len(safe_skipped)}")
        
        # Step 3: KG Investigation per chemical
        self.logger.log_step("Chemical Analysis", f"Processing {len(chemicals)} chemical(s)...")
        
        chemical_findings = []
        
        # FIX: Store list of product IDs for each chemical
        chemical_product_map = defaultdict(list)
        for product in products_list:
            pid = product.get("product_id", "unknown")
            for ing in product.get("ingredient_list", []):
                name = ing.get("name", "").strip()
                if name in chemicals:
                    chemical_product_map[name].append(pid)
        
        for chem in chemicals[:20]:  # Limit for performance
            self.logger.log_step(f"Analyzing: {chem}", "", icon="🔬")
            
            if "kg" not in self.clients:
                self.logger.log_decision(chem, "NO KG SERVER", "KG server not available", "critical")
                chemical_findings.append({
                    "name": chem,
                    "risk_level": "UNKNOWN",
                    "unresolved": True,
                    "source": "ERROR",
                    "confidence": 0.0,
                    "product_ids": chemical_product_map.get(chem, [])
                })
                continue
            
            # Resolve
            resolve_result = self.clients["kg"].call("resolve_ingredient", {
                "ingredient_name": chem
            })
            
            if resolve_result.get("unresolved", False):
                # LLM Fallback for unresolved - respect token budget
                self.logger.log_decision(chem, "UNRESOLVED", 
                                        f"Not found in KG.", "moderate")
                
                llm_estimate = {}
                if self._can_call_llm() and "evaluation" in self.clients:
                    try:
                        llm_estimate = self.clients["evaluation"].call("estimate_missing_hazards", {
                            "chemical_name": chem,
                            "reason": "Not found in KG"
                        })
                        self._record_llm_call()
                    except Exception as e:
                        self.logger.log_step(f"LLM Error", str(e), icon="❌")
                
                risk_level = self._map_h_codes_to_risk(llm_estimate.get("estimated_h_codes", []))
                confidence = llm_estimate.get("confidence", 0.3)
                
                chemical_findings.append({
                    "name": chem,
                    "risk_level": risk_level,
                    "unresolved": True,
                    "source": "LLM_ESTIMATE",
                    "confidence": confidence,
                    "llm_reasoning": llm_estimate.get("reasoning", ""),
                    "estimated_h_codes": llm_estimate.get("estimated_h_codes", []),
                    "product_ids": chemical_product_map.get(chem, [])
                })
                continue
            
            uid = resolve_result.get("uid")
            match_strategy = resolve_result.get("match_strategy", "unknown")
            confidence = resolve_result.get("confidence", 0.7)
            
            self.logger.log_decision(chem, f"RESOLVED", f"UID: {uid[:20] if uid else 'N/A'}... ({match_strategy})", "low")
            
            # Get hazard profile
            hazard_result = self.clients["kg"].call("get_hazard_profile", {
                "chemical_uid": uid
            })
            
            h_codes = hazard_result.get("h_codes", [])
            signal = hazard_result.get("highest_signal", "None")
            has_critical = hazard_result.get("has_critical_hazard", False)
            
            # Determine risk
            if has_critical:
                risk_level = "CRITICAL"
            elif signal == "Danger":
                risk_level = "HIGH"
            elif signal == "Warning":
                risk_level = "MODERATE"
            elif h_codes:
                risk_level = "LOW"
            else:
                risk_level = "LOW"
            
            # Get confidence from hazard profile if available
            hazard_confidence = hazard_result.get("confidence", confidence)
            final_confidence = hazard_confidence
            
            self.logger.log_decision(chem, f"RISK: {risk_level}", 
                                    f"H-codes: {h_codes[:3]}{'...' if len(h_codes) > 3 else ''} | Signal: {signal} | Conf: {final_confidence:.2f}",
                                    risk_level.lower())
            
            target_organs = []
            
            # Deep investigation for HIGH/CRITICAL
            if risk_level in ["HIGH", "CRITICAL"]:
                self.logger.log_step(f"Deep Investigation: {chem}", "Getting full profile...", icon="🔍")
                full_profile = self.clients["kg"].call("get_full_profile", {
                    "chemical_uid": uid
                })
                target_organs = full_profile.get("target_organs", [])
                data_confidence = full_profile.get("data_confidence", final_confidence)
                final_confidence = data_confidence
                if target_organs:
                    self.logger.log_step(f"Target Organs", f"{chem} affects: {', '.join(target_organs)}", icon="🧠")
            else:
                # Basic investigation - just get organs
                organs_result = self.clients["kg"].call("get_target_organs", {
                    "chemical_uid": uid
                })
                target_organs = organs_result.get("organs", [])
            
            chemical_findings.append({
                "name": chem,
                "risk_level": risk_level,
                "h_codes": h_codes,
                "signal": signal,
                "uid": uid,
                "unresolved": False,
                "source": "KG",
                "confidence": final_confidence,
                "target_organs": target_organs,
                "product_ids": chemical_product_map.get(chem, [])
            })
        
        # Step 4: Combination analysis (global mode)
        # FIX: Initialize variables BEFORE conditional block
        global_organ_analysis = {}
        cumulative_list = []
        
        if needs_cumulative and len(chemical_findings) >= 2 and "combination" in self.clients:
            self.logger.log_step("Combination Analysis", "Running global organ overlap analysis...", icon="🔄")
            
            # Build all chemicals with product_id for global analysis
            all_chemicals = []
            for finding in chemical_findings:
                # Use first product_id from list for global analysis
                product_ids = finding.get("product_ids", [])
                for pid in product_ids:
                    all_chemicals.append({
                        "name": finding["name"],
                        "uid": finding.get("uid"),
                        "target_organs": finding.get("target_organs", []),
                        "h_codes": finding.get("h_codes", []),
                        "product_id": pid
                    })
            
            # Global organ overlap
            overlap_result = self.clients["combination"].call("check_organ_overlap", {
                "chemicals": all_chemicals,
                "global_mode": True
            })
            
            global_organ_analysis = overlap_result.get("global_organ_analysis", {})
            escalation = overlap_result.get("verdict_escalation")
            
            if global_organ_analysis:
                self.logger.log_step("Global Organ Analysis", "", icon="🧠")
                for organ, data in global_organ_analysis.items():
                    self.logger.log_step(f"Organ: {organ}", 
                                        f"Unique chemicals: {data.get('total_unique_count', 0)} | "
                                        f"Chemicals: {', '.join(data.get('unique_chemicals', [])[:5])}",
                                        icon="📊")
            
            if escalation == "HIGH":
                self.logger.log_step("Escalation Enforced", 
                                    "verdict_escalation = HIGH - forcing product risk levels", 
                                    icon="⚠️")
                for finding in chemical_findings:
                    if finding.get("risk_level") in ["HIGH", "MODERATE"]:
                        finding["risk_level"] = "HIGH"
            
            # Cumulative presence
            self.logger.log_step("Cumulative Presence", "Checking for chemicals in multiple products...", icon="📦")
            
            # Build product frequency
            product_chemicals = {}
            for product in products_list:
                pid = product.get("product_id", "unknown")
                pname = product.get("product_name", "Unknown")
                product_chemicals[pid] = {"name": pname, "chemicals": set()}
                for ing in product.get("ingredient_list", []):
                    name = ing.get("name", "").strip()
                    product_chemicals[pid]["chemicals"].add(name)
            
            cumulative_list = []
            for finding in chemical_findings:
                name = finding["name"]
                count = 0
                products_with_chem = []
                for pid, data in product_chemicals.items():
                    if name in data["chemicals"]:
                        count += 1
                        products_with_chem.append({"product_id": pid, "product_name": data["name"]})
                if count >= 2:
                    cumulative_list.append({
                        "chemical_name": name,
                        "frequency": count,
                        "products": products_with_chem
                    })
            
            if cumulative_list:
                for cum in cumulative_list:
                    self.logger.log_step(f"Cumulative: {cum['chemical_name']}", 
                                        f"Appears in {cum['frequency']} products", icon="⚠️")
        
        # Step 5: Build final report
        self.logger.log_step("Synthesizing Report", "Generating final safety assessment...", icon="📝")
        
        # Group by product
        products_output = []
        for product in products_list:
            pid = product.get("product_id", "unknown")
            pname = product.get("product_name", "Unknown Product")
            usage = product.get("product_usage", "unknown")
            exposure = [product.get("exposure_type", "unknown")] if product.get("exposure_type") else []
            
            product_findings = [f for f in chemical_findings if pid in f.get("product_ids", [])]
            
            high_risk = [f for f in product_findings if f["risk_level"] in ["CRITICAL", "HIGH"]]
            moderate_risk = [f for f in product_findings if f["risk_level"] == "MODERATE"]
            
            if high_risk:
                overall_risk = "HIGH"
                recommendation = "avoid"
            elif moderate_risk:
                overall_risk = "MODERATE"
                recommendation = "reduce_use"
            else:
                overall_risk = "LOW"
                recommendation = "keep"
            
            products_output.append({
                "product_id": pid,
                "product_name": pname,
                "risk_level": overall_risk,
                "recommendation": recommendation,
                "chemicals": product_findings
            })
        
        # Build final report
        all_high_risk = [f["name"] for f in chemical_findings if f["risk_level"] in ["CRITICAL", "HIGH"]]
        all_moderate_risk = [f["name"] for f in chemical_findings if f["risk_level"] == "MODERATE"]
        
        report = {
            "analyzed_at": datetime.now().isoformat(),
            "products_count": len(products_list),
            "chemicals_analyzed": len(chemical_findings),
            "llm_calls_used": self.token_calls,
            "products": products_output,
            "high_risk_chemicals": all_high_risk[:10],
            "moderate_risk_chemicals": all_moderate_risk[:10],
            "global_organ_analysis": global_organ_analysis if needs_cumulative else {},
            "cumulative_chemicals": cumulative_list if needs_cumulative else []
        }
        
        return report


class StreamlitLogger:
    """Logs agent reasoning to Streamlit interface with visual elements"""
    
    def __init__(self):
        self.step_num = 0
        self.start_time = None
        self.status_placeholder = None
    
    def start(self):
        self.start_time = time.time()
        self.status_placeholder = st.empty()
        self.status_placeholder.info("🧠 **Agent Started** — Initializing MCP servers...")
    
    def log_step(self, title: str, content: str = "", icon: str = "🔍"):
        self.step_num += 1
        with st.container():
            st.markdown(f"""
            <div style="background-color: #f0f7ff; border-radius: 10px; padding: 12px; margin: 8px 0; border-left: 4px solid #2196f3;">
                <b>{icon} Step {self.step_num}: {title}</b><br>
                {content}
            </div>
            """, unsafe_allow_html=True)
    
    def log_tool_call(self, server: str, tool: str, args: Dict):
        args_preview = json.dumps(args, indent=2)[:200]
        with st.container():
            st.markdown(f"""
            <div style="background-color: #e3f2fd; border-radius: 8px; padding: 8px; margin: 4px 0; font-family: monospace; font-size: 12px;">
                🔧 <b>CALL:</b> {server}.{tool}<br>
                📦 <b>Args:</b> <code>{args_preview}{'...' if len(json.dumps(args)) > 200 else ''}</code>
            </div>
            """, unsafe_allow_html=True)
    
    def log_tool_result(self, result: Dict, summary: str = ""):
        result_preview = json.dumps(result, indent=2)[:300]
        with st.container():
            with st.expander(f"📥 RESULT: {summary}"):
                st.code(result_preview, language="json")
    
    def log_decision(self, chemical: str, decision: str, reason: str, risk_color: str = "blue"):
        colors = {
            "critical": "#ffebee",
            "high": "#fff3e0",
            "moderate": "#fff8e1",
            "low": "#e8f5e9",
            "blue": "#e3f2fd"
        }
        bg = colors.get(risk_color, "#f5f5f5")
        border = {"critical": "#f44336", "high": "#ff9800", "moderate": "#ffc107", "low": "#4caf50", "blue": "#2196f3"}.get(risk_color, "#2196f3")
        with st.container():
            st.markdown(f"""
            <div style="background-color: {bg}; border-radius: 10px; padding: 12px; margin: 8px 0; border-left: 4px solid {border};">
                <b>🧪 DECISION for {chemical}</b><br>
                → <b>{decision}</b><br>
                → {reason}
            </div>
            """, unsafe_allow_html=True)
    
    def log_error(self, context: str, error: str):
        st.error(f"❌ **{context}**: {error}")
    
    def update_status(self, message: str):
        if self.status_placeholder:
            self.status_placeholder.info(message)
    
    def finish(self, elapsed: float):
        if self.status_placeholder:
            self.status_placeholder.success(f"✅ **Analysis Complete** — {elapsed:.1f} seconds")
        return elapsed


# ============================================================
# INPUT PARSING HELPERS
# ============================================================

def clean_json_input(raw_text: str) -> str:
    """Clean and extract JSON from raw input"""
    import re
    raw_text = raw_text.strip()
    if raw_text.startswith("[PRODUCTS_LIST]"):
        raw_text = raw_text.replace("[PRODUCTS_LIST]", "").strip()
    json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    if json_match:
        return json_match.group()
    return raw_text


def parse_input(raw_text: str) -> tuple:
    """Parse input and return products_list"""
    try:
        cleaned = clean_json_input(raw_text)
        data = json.loads(cleaned)
        if "products_list" in data:
            products_list = data["products_list"]
        else:
            products_list = data
        if isinstance(products_list, dict):
            products_list = [products_list]
        if not isinstance(products_list, list):
            return False, None, "Expected array of products"
        for product in products_list:
            if "ingredient_list" not in product:
                return False, None, f"Missing 'ingredient_list' in product: {product}"
        return True, products_list, None
    except json.JSONDecodeError as e:
        return False, None, f"JSON error: {e.msg} at position {e.pos}"


def format_report_display(report: dict):
    """Display report in a readable format"""
    
    st.subheader("📊 Analysis Results")
    
    # Overall stats
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Products Analyzed", report.get("products_count", 0))
    with col2:
        st.metric("Chemicals Analyzed", report.get("chemicals_analyzed", 0))
    with col3:
        high_count = len(report.get("high_risk_chemicals", []))
        st.metric("High Risk Chemicals", high_count, delta="⚠️" if high_count > 0 else None)
    
    # Show LLM usage
    llm_calls = report.get("llm_calls_used", 0)
    st.caption(f"🤖 LLM calls used: {llm_calls}/5")
    
    # High risk chemicals
    if report.get("high_risk_chemicals"):
        st.error(f"⚠️ High Risk Chemicals: {', '.join(report['high_risk_chemicals'])}")
    
    if report.get("moderate_risk_chemicals"):
        st.warning(f"⚠️ Moderate Risk Chemicals: {', '.join(report['moderate_risk_chemicals'])}")
    
    # Product verdicts
    st.subheader("📦 Product Verdicts")
    for product in report.get("products", []):
        risk = product.get("risk_level", "UNKNOWN")
        if risk == "HIGH":
            st.error(f"**{product.get('product_name', 'Unknown')}** → 🔴 HIGH RISK - {product.get('recommendation', '').upper()}")
        elif risk == "MODERATE":
            st.warning(f"**{product.get('product_name', 'Unknown')}** → 🟡 MODERATE RISK - {product.get('recommendation', '').upper()}")
        else:
            st.success(f"**{product.get('product_name', 'Unknown')}** → 🟢 LOW RISK - {product.get('recommendation', '').upper()}")
    
    # Global organ analysis
    if report.get("global_organ_analysis"):
        st.subheader("🧠 Global Organ Analysis")
        for organ, data in report["global_organ_analysis"].items():
            st.info(f"**{organ.capitalize()}**: {data.get('total_unique_count', 0)} unique chemicals")
            with st.expander(f"View details for {organ}"):
                st.json(data)
    
    # Cumulative chemicals
    if report.get("cumulative_chemicals"):
        st.subheader("📦 Cumulative Exposure")
        for cum in report["cumulative_chemicals"]:
            st.warning(f"⚠️ **{cum.get('chemical_name')}** appears in {cum.get('frequency')} products")


# ============================================================
# MAIN STREAMLIT APP
# ============================================================

def run_agent_sync(products_list: List[Dict], logger: StreamlitLogger) -> Dict:
    """Run agent synchronously (for Streamlit)"""
    agent = StreamlitAgent(logger)
    try:
        agent.initialize()
        report = agent.evaluate(products_list)
        return report
    finally:
        agent.shutdown()


def main():
    st.set_page_config(
        page_title="Biological Agent - Chemical Safety Analysis",
        page_icon="🧪",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    st.title("🧪 Biological Agent")
    st.markdown("*AI-powered chemical safety analysis for consumer products*")
    st.caption("Using real MCP servers (KG, Filter, Combination, Evaluation) with Neo4j + Groq")
    
    # Sidebar for input
    with st.sidebar:
        st.header("📤 Input")
        
        input_method = st.radio(
            "Choose input method:",
            ["📋 Example Product", "📁 Upload JSON", "✏️ Paste JSON"],
            help="Select how to provide product data"
        )
        
        products_list = None
        
        if input_method == "📋 Example Product":
            example = {
                "products_list": [
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
                            {"name": "CITRIC ACID"}
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
                            {"name": "LINALOOL"}
                        ]
                    }
                ]
            }
            products_list = example["products_list"]
            st.success("Using example with 2 products")
            with st.expander("View example JSON"):
                st.json(example)
        
        elif input_method == "📁 Upload JSON":
            uploaded_file = st.file_uploader("Upload JSON file", type=["json"])
            if uploaded_file:
                try:
                    content = uploaded_file.read().decode()
                    success, data, error = parse_input(content)
                    if success:
                        products_list = data
                        st.success(f"Loaded {len(products_list)} product(s)")
                    else:
                        st.error(error)
                except Exception as e:
                    st.error(f"Error reading file: {e}")
        
        else:  # Paste JSON
            json_text = st.text_area(
                "Paste JSON here:",
                height=250,
                placeholder='{"products_list": [{"product_id": "1", "ingredient_list": [{"name": "AQUA"}], "product_usage": "cosmetics", "exposure_type": "skin"}]}',
                help="Paste valid JSON with products_list array"
            )
            if json_text:
                success, data, error = parse_input(json_text)
                if success:
                    products_list = data
                    st.success(f"Loaded {len(products_list)} product(s)")
                else:
                    st.error(error)
        
        st.divider()
        
        st.header("⚙️ Settings")
        show_detailed_logs = st.checkbox("Show detailed tool calls", value=True)
        
        analyze_button = st.button("🚀 Analyze Products", type="primary", use_container_width=True)
    
    # Main area
    if analyze_button and products_list:
        st.session_state.analysis_complete = False
        
        logger = StreamlitLogger()
        logger.start()
        
        with st.spinner("Agent is analyzing products... This may take 10-30 seconds..."):
            try:
                report = run_agent_sync(products_list, logger)
                elapsed = logger.finish(report.get("elapsed_s", 0))
                st.session_state.analysis_complete = True
                st.session_state.report = report
                st.session_state.elapsed = elapsed
            except Exception as e:
                st.error(f"Agent failed: {e}")
                import traceback
                st.code(traceback.format_exc())
    
    if st.session_state.get("analysis_complete"):
        report = st.session_state.report
        
        format_report_display(report)
        
        st.divider()
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption(f"Analysis completed in {st.session_state.elapsed:.1f} seconds")
        with col2:
            st.download_button(
                label="📥 Download Full Report (JSON)",
                data=json.dumps(report, indent=2),
                file_name=f"biological_agent_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json"
            )
        
        with st.expander("📄 View Raw JSON Report"):
            st.json(report)
    
    elif not analyze_button:
        st.info("👈 Select input method and click **Analyze Products** to start")
        
        with st.expander("📖 Input Format Example"):
            st.code("""
{
  "products_list": [
    {
      "product_id": "1",
      "product_name": "Moisturizing Cream (optional)",
      "product_usage": "cosmetics",
      "exposure_type": "skin",
      "ingredient_list": [
        {"name": "AQUA"},
        {"name": "SODIUM LAURETH SULFATE"},
        {"name": "PARFUM"}
      ]
    }
  ]
}
            """, language="json")


if __name__ == "__main__":
    main()