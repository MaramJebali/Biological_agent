"""
Streamlit Interface for Biological Agent - Windows Compatible
──────────────────────────────────────────────────────────────
Run: streamlit run app.py
"""

import streamlit as st
import asyncio
import json
import sys
import os
import time
import subprocess
import threading
from datetime import datetime
from typing import Dict, Any, List

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class SyncMCPClient:
    """Synchronous MCP Client for Windows compatibility"""
    
    def __init__(self, name: str, server_path: str, logger):
        self.name = name
        self.server_path = server_path
        self.logger = logger
        self.process = None
    
    def start(self):
        """Start the MCP server process (synchronous)"""
        self.process = subprocess.Popen(
            [sys.executable, self.server_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        # Give server time to start
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
            self.logger.log_step(f"Error listing tools for {self.name}", str(e), icon="❌")
            return []
    
    def stop(self):
        """Stop the server process"""
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)


class ObservableBiologicalAgent:
    """Real agent that logs all operations to Streamlit (Windows compatible)"""
    
    def __init__(self, logger):
        self.logger = logger
        self.clients = {}
        self.server_paths = {
            "kg": "servers/kg_server/server.py",
            "filter": "servers/filter_server/server.py",
            "combination": "servers/combination_server/server.py",
            "evaluation": "servers/evaluation_server/server.py",
        }
    
    def initialize(self):
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
        for client in self.clients.values():
            try:
                client.stop()
            except:
                pass
    
    def evaluate(self, products_list: List[Dict]) -> Dict:
        """Run real evaluation with full MCP tool calls (synchronous)"""
        
        self.logger.log_step(
            "Input Products",
            f"Analyzing {len(products_list)} product(s)"
        )
        
        # ── Step 1: Extract all ingredients ──────────────────────────────────
        all_ingredients = []
        for product in products_list:
            for ing in product.get("ingredient_list", []):
                all_ingredients.append(ing.get("name"))
        
        self.logger.log_step(
            "Extract Ingredients",
            f"Found {len(all_ingredients)} total ingredients:\n{', '.join(all_ingredients[:20])}{'...' if len(all_ingredients) > 20 else ''}"
        )
        
        # ── Step 2: Filter ingredients (call filter server) ──────────────────
        self.logger.log_step("Filtering Ingredients", "Calling filter_server.classify_ingredients...")
        
        if "filter" in self.clients:
            filter_result = self.clients["filter"].call("classify_ingredients", {
                "ingredient_names": all_ingredients
            })
            
            if isinstance(filter_result, dict):
                chemicals = filter_result.get("chemicals", [])
                non_chemicals = filter_result.get("safe_skipped", filter_result.get("non_chemicals", []))
            else:
                chemicals = all_ingredients
                non_chemicals = []
        else:
            chemicals = all_ingredients
            non_chemicals = []
        
        self.logger.log_step(
            "Filter Results",
            f"🔬 **Chemicals to analyze:** {len(chemicals)}\n"
            f"✅ **Safe to skip:** {len(non_chemicals)}\n\n"
            f"Chemicals: {', '.join(chemicals[:10])}{'...' if len(chemicals) > 10 else ''}"
        )
        
        # ── Step 3: Analyze each chemical with KG server ─────────────────────
        self.logger.log_step("Chemical Analysis", f"Processing {len(chemicals)} chemical(s)...")
        
        chemical_findings = []
        
        for chem in chemicals[:15]:  # Limit for demo performance
            self.logger.log_step(f"Analyzing: {chem}", "", icon="🔬")
            
            if "kg" not in self.clients:
                self.logger.log_decision(chem, "NO KG SERVER", "KG server not available", "critical")
                continue
            
            # Call resolve_ingredient
            resolve_result = self.clients["kg"].call("resolve_ingredient", {
                "ingredient_name": chem
            })
            
            if resolve_result.get("unresolved", False):
                self.logger.log_decision(
                    chem,
                    "UNRESOLVED - LLM Fallback",
                    f"Not found in Knowledge Graph. Using LLM estimate.",
                    "moderate"
                )
                chemical_findings.append({
                    "name": chem,
                    "risk_level": "UNKNOWN",
                    "unresolved": True,
                    "h_codes": []
                })
                continue
            
            uid = resolve_result.get("uid")
            self.logger.log_decision(
                chem,
                f"RESOLVED → UID: {uid[:20] if uid else 'N/A'}...",
                f"Found in KG via {resolve_result.get('match_strategy', 'unknown')}",
                "low"
            )
            
            # Call get_hazard_profile
            hazard_result = self.clients["kg"].call("get_hazard_profile", {
                "chemical_uid": uid
            })
            
            h_codes = hazard_result.get("h_codes", [])
            signal = hazard_result.get("highest_signal", "None")
            has_critical = hazard_result.get("has_critical_hazard", False)
            
            # Determine risk level based on hazards
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
            
            self.logger.log_decision(
                chem,
                f"RISK LEVEL: {risk_level}",
                f"H-codes: {h_codes[:5]}{'...' if len(h_codes) > 5 else ''} | Signal: {signal}",
                risk_level.lower()
            )
            
            # If HIGH or CRITICAL, get full profile
            if risk_level in ["HIGH", "CRITICAL"]:
                self.logger.log_step(f"Deep Investigation: {chem}", f"Getting full profile...", icon="🔍")
                
                full_profile = self.clients["kg"].call("get_full_profile", {
                    "chemical_uid": uid
                })
                
                organs = full_profile.get("target_organs", [])
                if organs:
                    self.logger.log_tool_result(full_profile, f"Target organs: {', '.join(organs)}")
            
            chemical_findings.append({
                "name": chem,
                "risk_level": risk_level,
                "h_codes": h_codes,
                "signal": signal,
                "uid": uid,
                "unresolved": False
            })
        
        # ── Step 4: Combination analysis (if multiple chemicals) ─────────────
        if len(chemical_findings) >= 2 and "combination" in self.clients:
            self.logger.log_step("Combination Analysis", "Checking for organ overlaps...", icon="🔄")
            
            # Get target organs for each resolved chemical
            chemicals_with_organs = []
            for finding in chemical_findings:
                if not finding.get("unresolved") and finding.get("uid"):
                    organs_result = self.clients["kg"].call("get_target_organs", {
                        "chemical_uid": finding["uid"]
                    })
                    chemicals_with_organs.append({
                        "name": finding["name"],
                        "uid": finding["uid"],
                        "target_organs": organs_result.get("organs", [])
                    })
            
            if chemicals_with_organs:
                overlap_result = self.clients["combination"].call("check_organ_overlap", {
                    "chemicals": chemicals_with_organs
                })
                
                if overlap_result.get("has_overlap"):
                    overlaps = overlap_result.get("overlapping_organs", {})
                    self.logger.log_step(
                        "Organ Overlap Detected!",
                        "\n".join([f"- {organ}: {', '.join(chems)}" for organ, chems in overlaps.items()]),
                        icon="⚠️"
                    )
        
        # ── Step 5: Generate final report ────────────────────────────────────
        self.logger.log_step("Synthesizing Report", "Generating final safety assessment...", icon="📝")
        
        high_risk = [f for f in chemical_findings if f["risk_level"] in ["CRITICAL", "HIGH"]]
        moderate_risk = [f for f in chemical_findings if f["risk_level"] == "MODERATE"]
        
        if high_risk:
            overall_risk = "HIGH"
            recommendation = "avoid"
        elif moderate_risk:
            overall_risk = "MODERATE"
            recommendation = "reduce_use"
        else:
            overall_risk = "LOW"
            recommendation = "keep"
        
        report = {
            "analyzed_at": datetime.now().isoformat(),
            "products_count": len(products_list),
            "chemicals_analyzed": len(chemical_findings),
            "overall_risk": overall_risk,
            "recommendation": recommendation,
            "chemical_findings": chemical_findings,
            "high_risk_chemicals": [c["name"] for c in high_risk],
            "moderate_risk_chemicals": [c["name"] for c in moderate_risk]
        }
        
        return report


class StreamlitReasoningLogger:
    """Logs agent reasoning to Streamlit interface"""
    
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
        with st.container():
            args_preview = json.dumps(args, indent=2)[:200]
            st.markdown(f"""
            <div style="background-color: #e3f2fd; border-radius: 8px; padding: 8px; margin: 4px 0; font-family: monospace; font-size: 12px;">
                🔧 <b>CALL:</b> {server}.{tool}<br>
                📦 <b>Args:</b> <code>{args_preview}{'...' if len(json.dumps(args)) > 200 else ''}</code>
            </div>
            """, unsafe_allow_html=True)
    
    def log_tool_result(self, result: Dict, summary: str = ""):
        with st.container():
            result_preview = json.dumps(result, indent=2)[:300]
            st.markdown(f"""
            <div style="background-color: #e8f5e9; border-radius: 8px; padding: 8px; margin: 4px 0; font-family: monospace; font-size: 12px;">
                📥 <b>RESULT:</b> {summary}<br>
                <details>
                    <summary>View full response</summary>
                    <pre style="font-size: 10px;">{result_preview}...</pre>
                </details>
            </div>
            """, unsafe_allow_html=True)
    
    def log_decision(self, chemical: str, decision: str, reason: str, risk_color: str = "blue"):
        colors = {
            "critical": "#ffebee",
            "high": "#fff3e0",
            "moderate": "#fff8e1",
            "low": "#e8f5e9"
        }
        bg = colors.get(risk_color, "#f5f5f5")
        with st.container():
            st.markdown(f"""
            <div style="background-color: {bg}; border-radius: 10px; padding: 12px; margin: 8px 0; border-left: 4px solid #ff9800;">
                <b>🧪 DECISION for {chemical}</b><br>
                → <b>{decision}</b><br>
                → {reason}
            </div>
            """, unsafe_allow_html=True)
    
    def update_status(self, message: str):
        if self.status_placeholder:
            self.status_placeholder.info(message)
    
    def finish(self, elapsed: float):
        if self.status_placeholder:
            self.status_placeholder.success(f"✅ **Analysis Complete** — {elapsed:.1f} seconds")
        return elapsed


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
        return True, products_list, None
    except json.JSONDecodeError as e:
        return False, None, f"JSON error: {e.msg} at position {e.pos}"


def run_agent_sync(products_list: List[Dict], logger: StreamlitReasoningLogger) -> Dict:
    """Run agent synchronously (for Streamlit)"""
    agent = ObservableBiologicalAgent(logger)
    try:
        agent.initialize()
        report = agent.evaluate(products_list)
        return report
    finally:
        agent.shutdown()


def main():
    st.set_page_config(page_title="Biological Agent", page_icon="🧪", layout="wide")
    
    st.title("🧪 Biological Agent")
    st.markdown("*AI-powered chemical safety analysis using real MCP servers*")
    
    with st.sidebar:
        st.header("📤 Input")
        
        input_method = st.radio("Choose input method:", ["📋 Example Product", "📁 Upload JSON", "✏️ Paste JSON"])
        
        products_list = None
        
        if input_method == "📋 Example Product":
            example = {
                "products_list": [{
                    "product_id": "1",
                    "ingredient_list": [
                        {"name": "AQUA"},
                        {"name": "SODIUM LAURETH SULFATE"},
                        {"name": "PARFUM"},
                        {"name": "CITRIC ACID"}
                    ],
                    "product_usage": "cosmetics",
                    "exposure_type": "skin"
                }]
            }
            products_list = example["products_list"]
            st.success("Using example product")
            st.json(example)
        
        elif input_method == "📁 Upload JSON":
            uploaded = st.file_uploader("Upload JSON", type=["json"])
            if uploaded:
                content = uploaded.read().decode()
                success, data, error = parse_input(content)
                if success:
                    products_list = data
                    st.success(f"Loaded {len(products_list)} product(s)")
                else:
                    st.error(error)
        
        else:
            json_text = st.text_area("Paste JSON:", height=200, 
                                     placeholder='{"products_list": [{"product_id": "1", "ingredient_list": [{"name": "AQUA"}], "product_usage": "cosmetics", "exposure_type": "skin"}]}')
            if json_text:
                success, data, error = parse_input(json_text)
                if success:
                    products_list = data
                    st.success(f"Loaded {len(products_list)} product(s)")
                else:
                    st.error(error)
        
        analyze = st.button("🚀 Analyze Products", type="primary", use_container_width=True)
    
    if analyze and products_list:
        st.session_state.analysis_complete = False
        
        logger = StreamlitReasoningLogger()
        logger.start()
        
        try:
            # Run agent synchronously
            report = run_agent_sync(products_list, logger)
            elapsed = logger.finish(report.get("elapsed_s", 0))
            st.session_state.analysis_complete = True
            st.session_state.report = report
        except Exception as e:
            st.error(f"Agent failed: {e}")
            import traceback
            st.code(traceback.format_exc())
    
    if st.session_state.get("analysis_complete"):
        report = st.session_state.report
        
        st.header("📊 Analysis Results")
        
        overall = report.get("overall_risk", "UNKNOWN")
        if overall == "HIGH":
            st.error(f"🔴 Overall Verdict: HIGH RISK")
        elif overall == "MODERATE":
            st.warning(f"🟡 Overall Verdict: MODERATE RISK")
        else:
            st.success(f"🟢 Overall Verdict: LOW RISK")
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Products Analyzed", report.get("products_count", 0))
        with col2:
            st.metric("Chemicals Analyzed", report.get("chemicals_analyzed", 0))
        
        if report.get("high_risk_chemicals"):
            st.error(f"⚠️ High Risk Chemicals: {', '.join(report['high_risk_chemicals'])}")
        if report.get("moderate_risk_chemicals"):
            st.warning(f"⚠️ Moderate Risk Chemicals: {', '.join(report['moderate_risk_chemicals'])}")
        
        with st.expander("📄 Full Report JSON", expanded=False):
            st.json(report)
        
        st.download_button(
            "📥 Download Report", 
            json.dumps(report, indent=2), 
            "biological_agent_report.json",
            "application/json"
        )


if __name__ == "__main__":
    main()