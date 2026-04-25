"""
Streamlit Interface for Biological Agent - PRODUCTION VERSION
Outputs FULL schema compliant with output_schema.py
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
from functools import lru_cache

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from models.output_schema import (
    FinalReport, ProductOutput, IngredientsSection, ChemicalEvaluation,
    ResolutionInfo, IdentityInfo, HazardInfo, BodyEffectsInfo, DoseEvaluationInfo,
    ChemicalVerdict, SafeSkipped, UnverifiedChemical, CombinationRisks,
    OrganOverlap, CumulativePresence, ProductSummary, GlobalSummary,
    ExposureEffects, OrganGlobalAnalysis,
    create_resolution_info, create_identity_info, create_hazard_info,
    create_body_effects, create_dose_evaluation, create_verdict
)


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
        self.max_token_calls = 10  # Dynamic budget
        self.resolution_cache = {}  # Deduplication cache
    
    @lru_cache(maxsize=200)
    def _cached_resolve(self, name: str) -> dict:
        """Cached resolve to avoid duplicate KG queries"""
        if "kg" not in self.clients:
            return {"unresolved": True, "error": "KG server not available"}
        return self.clients["kg"].call("resolve_ingredient", {"ingredient_name": name})
    
    def _can_call_llm(self, estimated_tokens=300) -> bool:
        if self.token_calls >= self.max_token_calls:
            self.logger.log_step("Token Limit", f"Reached max LLM calls ({self.max_token_calls}) - using fallback", icon="⚠️")
            return False
        return True
    
    def _record_llm_call(self):
        self.token_calls += 1
        self.logger.log_step("Token Budget", f"LLM call {self.token_calls}/{self.max_token_calls}", icon="💰")
    
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
    
    def _map_h_codes_to_risk(self, h_codes: list, signal: str = "None") -> str:
        """
        Map H-codes and GHS signal to risk level.
        
        Rules:
        - Danger + Critical H-codes (H340/H350/H360/H370/H372) → CRITICAL
        - Danger → HIGH
        - Warning → MODERATE
        - H-codes present (no signal) → LOW
        - No hazards → SAFE
        - Unresolved → UNKNOWN
        """
        # Critical H-codes that indicate severe toxicity
        critical_codes = {
            "H340", "H341",  # Mutagenicity
            "H350", "H351",  # Carcinogenicity
            "H360", "H361", "H362",  # Reproductive toxicity
            "H370", "H371", "H372", "H373"  # STOT (organ damage)
        }
        
        # Danger signal with critical codes = CRITICAL
        if signal == "Danger":
            if any(code in critical_codes for code in h_codes):
                return "CRITICAL"
            return "HIGH"
        
        # Warning signal = MODERATE
        if signal == "Warning":
            return "MODERATE"
        
        # No signal but has H-codes = LOW
        if h_codes:
            return "LOW"
        
        # No hazards = SAFE
        return "SAFE"
    def evaluate(self, products_list: List[Dict]) -> Dict:
        """Run real evaluation with full MCP tool calls"""
        
        self.token_calls = 0
        self.resolution_cache = {}
        
        self.logger.log_step("Input Products", f"Analyzing {len(products_list)} product(s)")
        
        # Phase 1: Product context analysis
        product_count = len(products_list)
        needs_cumulative = product_count >= 2
        self.logger.log_step("Product Context", f"Products: {product_count} | Needs cumulative: {needs_cumulative}")
        
        # Step 1: Extract all ingredients
        all_ingredients = []
        for product in products_list:
            for ing in product.get("ingredient_list", []):
                name = ing.get("name", "").strip()
                if name:
                    all_ingredients.append(name)
        
        self.logger.log_step("Extract Ingredients", f"Found {len(all_ingredients)} total ingredients")
        
        # Step 2: Deduplicate before filter
        unique_ingredients = list(dict.fromkeys(all_ingredients))
        ingredients_list = [{"name": ing} for ing in unique_ingredients]
        
        self.logger.log_step("Filtering Ingredients", "Calling filter_server.classify_ingredients...")
        
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
        
        self.logger.log_step("Filter Results", f"🔬 Chemicals: {len(chemicals)} | ✅ Safe skipped: {len(safe_skipped)}")
        
        # Step 3: KG Investigation per chemical with deduplication
        self.logger.log_step("Chemical Analysis", f"Processing {len(chemicals)} chemical(s)...")
        
        chemical_findings = []
        chemical_product_map = defaultdict(list)
        
        # Build product map
        for product in products_list:
            pid = product.get("product_id", "unknown")
            for ing in product.get("ingredient_list", []):
                name = ing.get("name", "").strip()
                if name in chemicals:
                    chemical_product_map[name].append(pid)
        
        for chem in chemicals:
            self.logger.log_step(f"Analyzing: {chem}", "", icon="🔬")
            
            if "kg" not in self.clients:
                chemical_findings.append({
                    "name": chem, "risk_level": "UNKNOWN", "unresolved": True,
                    "source": "ERROR", "confidence": 0.0, "product_ids": chemical_product_map.get(chem, [])
                })
                continue
            
            # Use cached resolve
            resolve_result = self._cached_resolve(chem)
            
            if resolve_result.get("unresolved", False):
                self.logger.log_decision(chem, "UNRESOLVED", f"Not found in KG.", "moderate")
                
                llm_estimate = {}
                if self._can_call_llm() and "evaluation" in self.clients:
                    try:
                        llm_estimate = self.clients["evaluation"].call("estimate_missing_hazards", {
                            "chemical_name": chem, "reason": "Not found in KG"
                        })
                        self._record_llm_call()
                    except Exception as e:
                        self.logger.log_step(f"LLM Error", str(e), icon="❌")
                
                risk_level = self._map_h_codes_to_risk(llm_estimate.get("estimated_h_codes", []))
                confidence = llm_estimate.get("confidence", 0.3)
                
                chemical_findings.append({
                    "name": chem, "risk_level": risk_level, "unresolved": True,
                    "source": "LLM_ESTIMATE", "confidence": confidence,
                    "llm_reasoning": llm_estimate.get("reasoning", ""),
                    "estimated_h_codes": llm_estimate.get("estimated_h_codes", []),
                    "product_ids": chemical_product_map.get(chem, []),
                    "resolution": resolve_result
                })
                continue
            
            uid = resolve_result.get("uid")
            match_strategy = resolve_result.get("match_strategy", "unknown")
            confidence = resolve_result.get("confidence", 0.7)
            
            self.logger.log_decision(chem, f"RESOLVED", f"UID: {uid[:20] if uid else 'N/A'}... ({match_strategy})", "low")
            
                        # Get hazard data
            hazard_result = self.clients["kg"].call("get_hazard_profile", {
                "chemical_uid": uid
            })

            h_codes = hazard_result.get("h_codes", [])
            signal = hazard_result.get("highest_signal", "None")
            has_critical = hazard_result.get("has_critical_hazard", False)

            # CRITICAL H-codes for cross-check
            critical_h_codes = {"H340", "H350", "H360", "H370", "H372"}

            # CORRECTED RISK MAPPING
            if signal == "Danger":
                if has_critical or any(code in critical_h_codes for code in h_codes):
                    risk_level = "CRITICAL"
                else:
                    risk_level = "HIGH"
            elif signal == "Warning":
                risk_level = "MODERATE"
            elif h_codes:
                risk_level = "LOW"
            else:
                risk_level = "SAFE"

            # Log the decision
            self.logger.log_decision(chem, f"RISK: {risk_level}", 
                                    f"H-codes: {h_codes[:3]}{'...' if len(h_codes) > 3 else ''} | Signal: {signal}",
                                    risk_level.lower())

            
            target_organs = []
            full_profile = {}
            
            if risk_level in ["HIGH", "CRITICAL"]:
                self.logger.log_step(f"Deep Investigation: {chem}", "Getting full profile...", icon="🔍")
                full_profile = self.clients["kg"].call("get_full_profile", {"chemical_uid": uid})
                target_organs = full_profile.get("target_organs", [])
                if target_organs:
                    self.logger.log_step(f"Target Organs", f"{chem} affects: {', '.join(target_organs)}", icon="🧠")
            else:
                organs_result = self.clients["kg"].call("get_target_organs", {"chemical_uid": uid})
                target_organs = organs_result.get("organs", [])
            
            chemical_findings.append({
                "name": chem, "risk_level": risk_level, "h_codes": h_codes, "signal": signal,
                "uid": uid, "unresolved": False, "source": "KG", "confidence": confidence,
                "target_organs": target_organs, "product_ids": chemical_product_map.get(chem, []),
                "resolution": resolve_result, "hazard": hazard_result, "full_profile": full_profile
            })
        
        # Step 4: Combination analysis (global mode)
        global_organ_analysis = {}
        cumulative_list = []
        
        if needs_cumulative and len(chemical_findings) >= 2 and "combination" in self.clients:
            self.logger.log_step("Combination Analysis", "Running global organ overlap analysis...", icon="🔄")
            
            all_chemicals = []
            for finding in chemical_findings:
                for pid in finding.get("product_ids", []):
                    all_chemicals.append({
                        "name": finding["name"], "uid": finding.get("uid"),
                        "target_organs": finding.get("target_organs", []),
                        "h_codes": finding.get("h_codes", []), "product_id": pid
                    })
            
            overlap_result = self.clients["combination"].call("check_organ_overlap", {
                "chemicals": all_chemicals, "global_mode": True
            })
            
            global_organ_analysis = overlap_result.get("global_organ_analysis", {})
            escalation = overlap_result.get("verdict_escalation")
            
            if global_organ_analysis:
                self.logger.log_step("Global Organ Analysis", "", icon="🧠")
                for organ, data in global_organ_analysis.items():
                    self.logger.log_step(f"Organ: {organ}", 
                                        f"Unique chemicals: {data.get('total_unique_count', 0)}", icon="📊")
            
            if escalation == "HIGH":
                for finding in chemical_findings:
                    if finding.get("risk_level") in ["HIGH", "MODERATE"]:
                        finding["risk_level"] = "HIGH"
            
            # Cumulative presence
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
                        "chemical_name": name, "frequency": count, "products": products_with_chem
                    })
            
            if cumulative_list:
                for cum in cumulative_list:
                    self.logger.log_step(f"Cumulative: {cum['chemical_name']}", 
                                        f"Appears in {cum['frequency']} products", icon="⚠️")
        
        # ============================================================
        # STEP 5: BUILD FINAL REPORT USING output_schema.py STRUCTURE
        # ============================================================
        self.logger.log_step("Synthesizing Report", "Building schema-compliant report...", icon="📝")
        
        report_id = f"rpt_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        analyzed_at = datetime.now().isoformat()
        
        # Convert global_organ_analysis to OrganGlobalAnalysis format
        schema_global_analysis = {}
        for organ, data in global_organ_analysis.items():
            schema_global_analysis[organ] = OrganGlobalAnalysis(
                unique_chemicals=data.get("unique_chemicals", []),
                total_unique_count=data.get("total_unique_count", 0),
                chemical_frequency=data.get("chemical_frequency", {}),
                products_per_chemical=data.get("products_per_chemical", {})
            )
        
        products_output = []
        
        for product in products_list:
            pid = product.get("product_id", "unknown")
            pname = product.get("product_name", "Unknown Product")
            usage = product.get("product_usage", "unknown")
            exposure_type = [product.get("exposure_type", "unknown")] if product.get("exposure_type") else []
            
            product_ingredients = {ing.get("name", "").upper() for ing in product.get("ingredient_list", [])}
            
            chemicals_evaluated = []
            safe_skipped_list = []
            unverified_list = []
            drivers = []
            
            for f in chemical_findings:
                if f.get("name", "").upper() not in product_ingredients:
                    continue
                
                chem_name = f.get("name", "")
                
                if f.get("risk_level") in ["CRITICAL", "HIGH"]:
                    drivers.append(chem_name)
                
                # Use builder functions from output_schema.py
                resolution_info = create_resolution_info(
                    f.get("resolution", {}),
                    f.get("confidence", 0.5)
                )
                
                identity_info = create_identity_info(f.get("full_profile", {}))
                
                hazard_info = create_hazard_info(f.get("hazard", {}))
                
                body_effects = create_body_effects(f.get("full_profile", {}))
                
                dose_eval = create_dose_evaluation(f.get("exposure_limits", {}))
                
                justifications = []
                if f.get("llm_reasoning"):
                    justifications.append(f.get("llm_reasoning"))
                if f.get("reasoning"):
                    justifications.append(f.get("reasoning"))
                if not justifications:
                    justifications.append(f"Risk level: {f.get('risk_level', 'UNKNOWN')}")
                
                verdict = create_verdict(f.get("risk_level", "UNKNOWN"), justifications)
                
                chemicals_evaluated.append(ChemicalEvaluation(
                    name=chem_name,
                    uid=f.get("uid"),
                    cas=f.get("resolution", {}).get("cas") if f.get("resolution") else None,
                    resolution=resolution_info,
                    identity=identity_info,
                    hazard=hazard_info,
                    body_effects=body_effects,
                    dose_evaluation=dose_eval,
                    verdict=verdict
                ))
            
            # Safe skipped
            for safe in safe_skipped:
                safe_name = safe
                if safe_name.upper() in product_ingredients:
                    safe_skipped_list.append(SafeSkipped(
                        name=safe_name,
                        reason="Classified as non-chemical"
                    ))
            
            # Unverified
            for f in chemical_findings:
                if f.get("unresolved") and f.get("name", "").upper() in product_ingredients:
                    unverified_list.append(UnverifiedChemical(
                        name=f.get("name", ""),
                        reason=f.get("llm_reasoning", "Not found in Knowledge Graph"),
                        flag="unverified_chemical"
                    ))
            
            # Per-product organ overlaps
            product_specific_overlaps = []
            for organ, data in global_organ_analysis.items():
                chem_list = []
                for chem, prods in data.get("products_per_chemical", {}).items():
                    if pid in prods:
                        chem_list.append(chem)
                if chem_list:
                    product_specific_overlaps.append({
                        "organ": organ,
                        "chemicals": chem_list,
                        "count": len(chem_list)
                    })
            
            organ_overlap_obj = OrganOverlap(
                fetch_status="done",
                has_overlap=len(product_specific_overlaps) > 0,
                verdict_escalation=overlap_result.get("verdict_escalation") if 'overlap_result' in dir() else None,
                overlapping_organs=product_specific_overlaps if product_specific_overlaps else None,
                note=overlap_result.get("summary") if 'overlap_result' in dir() else None,
                error_message=None
            )
            
            cumulative_obj = CumulativePresence(
                fetch_status="done" if cumulative_list else "skipped",
                checked=len(cumulative_list) > 0,
                note=f"{len(cumulative_list)} chemical(s) in multiple products" if cumulative_list else "No cumulative concerns"
            )
            
            combination_risks = CombinationRisks(
                organ_overlap=organ_overlap_obj,
                cumulative_presence=cumulative_obj
            )
            
            risk_counts = {"CRITICAL": 0, "HIGH": 0, "MODERATE": 0, "LOW": 0, "SAFE": 0, "UNKNOWN": 0}
            for c in chemicals_evaluated:
                risk = c.verdict.danger_level
                if risk in risk_counts:
                    risk_counts[risk] += 1
            
            summary = ProductSummary(
                total_ingredients=len(product.get("ingredient_list", [])),
                chemicals_evaluated=len(chemicals_evaluated),
                safe_skipped=len(safe_skipped_list),
                unverified=len(unverified_list),
                critical=risk_counts["CRITICAL"],
                high=risk_counts["HIGH"],
                moderate=risk_counts["MODERATE"],
                low=risk_counts["LOW"],
                safe=risk_counts["SAFE"],
                unknown=risk_counts["UNKNOWN"],
                organ_overlap_flags=1 if product_specific_overlaps else 0
            )
            
            products_output.append(ProductOutput(
                product_id=pid,
                product_name=pname,
                usage=usage,
                exposure_type=exposure_type,
                drivers=drivers[:5],
                ingredients=IngredientsSection(
                    chemicals_evaluated=chemicals_evaluated,
                    safe_skipped=safe_skipped_list,
                    unverified_chemicals=unverified_list
                ),
                combination_risks=combination_risks,
                summary=summary
            ))
        
        # Build global summary
        all_critical = [f.get("name") for f in chemical_findings if f.get("risk_level") == "CRITICAL"]
        all_high = [f.get("name") for f in chemical_findings if f.get("risk_level") == "HIGH"]
        all_organs = set()
        for f in chemical_findings:
            for organ in f.get("target_organs", []):
                all_organs.add(organ)
        
        global_summary = GlobalSummary(
            total_products=len(products_list),
            products_to_avoid=sum(1 for p in products_output if p.summary.critical > 0 or p.summary.high > 0),
            products_to_reduce=sum(1 for p in products_output if p.summary.moderate > 0),
            products_safe=sum(1 for p in products_output if p.summary.critical == 0 and p.summary.high == 0 and p.summary.moderate == 0),
            products_unknown=sum(1 for p in products_output if p.summary.unknown > 0),
            unique_chemicals_found=len(set(f.get("name") for f in chemical_findings)),
            critical_chemicals=all_critical[:10],
            high_chemicals=all_high[:10],
            organs_under_pressure=list(all_organs)[:10] if all_organs else None,
            depth_used="full",
            organ_global_analysis=schema_global_analysis
        )
        
        final_report = FinalReport(
            report_id=report_id,
            analyzed_at=analyzed_at,
            agent_version="2.0.0",
            no_dose_data=True,
            depth="full",
            products=products_output,
            global_summary=global_summary
        )
        
        # Convert to dict for JSON serialization
        def to_dict(obj):
            if hasattr(obj, '__dataclass_fields__'):
                return {k: to_dict(v) for k, v in obj.__dict__.items()}
            elif isinstance(obj, list):
                return [to_dict(i) for i in obj]
            elif isinstance(obj, dict):
                return {k: to_dict(v) for k, v in obj.items()}
            else:
                return obj
        
        result = to_dict(final_report)
        result["llm_calls_used"] = self.token_calls
        
        return result


class StreamlitLogger:
    # ... (keep your existing StreamlitLogger class)
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
            "critical": "#ffebee", "high": "#fff3e0", "moderate": "#fff8e1",
            "low": "#e8f5e9", "blue": "#e3f2fd"
        }
        bg = colors.get(risk_color, "#f5f5f5")
        border = {"critical": "#f44336", "high": "#ff9800", "moderate": "#ffc107", 
                  "low": "#4caf50", "blue": "#2196f3"}.get(risk_color, "#2196f3")
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
    import re
    raw_text = raw_text.strip()
    if raw_text.startswith("[PRODUCTS_LIST]"):
        raw_text = raw_text.replace("[PRODUCTS_LIST]", "").strip()
    json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    if json_match:
        return json_match.group()
    return raw_text


def parse_input(raw_text: str) -> tuple:
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
    st.subheader("📊 Analysis Results")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Products Analyzed", len(report.get("products", [])))
    with col2:
        st.metric("Chemicals Analyzed", sum(len(p.get("ingredients", {}).get("chemicals_evaluated", [])) for p in report.get("products", [])))
    with col3:
        high_count = len(report.get("global_summary", {}).get("high_chemicals", []))
        st.metric("High Risk Chemicals", high_count)
    
    st.caption(f"LLM calls used: {report.get('llm_calls_used', 0)}")
    
    # Show product verdicts
    st.subheader("📦 Product Verdicts")
    for product in report.get("products", []):
        risk = "UNKNOWN"
        for c in product.get("ingredients", {}).get("chemicals_evaluated", []):
            if c.get("verdict", {}).get("danger_level") in ["CRITICAL", "HIGH"]:
                risk = "HIGH"
                break
            elif c.get("verdict", {}).get("danger_level") == "MODERATE":
                risk = "MODERATE"
            else:
                risk = "LOW"
        
        if risk == "HIGH":
            st.error(f"**{product.get('product_name', 'Unknown')}** → 🔴 HIGH RISK")
        elif risk == "MODERATE":
            st.warning(f"**{product.get('product_name', 'Unknown')}** → 🟡 MODERATE RISK")
        else:
            st.success(f"**{product.get('product_name', 'Unknown')}** → 🟢 LOW RISK")
    
    # Global organ analysis
    global_analysis = report.get("global_summary", {}).get("organ_global_analysis", {})
    if global_analysis:
        st.subheader("🧠 Global Organ Analysis")
        for organ, data in global_analysis.items():
            st.info(f"**{organ.capitalize()}**: {data.get('total_unique_count', 0)} unique chemicals")
            with st.expander(f"View details"):
                st.json(data)


def run_agent_sync(products_list: List[Dict], logger: StreamlitLogger) -> Dict:
    agent = StreamlitAgent(logger)
    try:
        agent.initialize()
        report = agent.evaluate(products_list)
        return report
    finally:
        agent.shutdown()


def main():
    st.set_page_config(page_title="Biological Agent", page_icon="🧪", layout="wide")
    
    st.title("🧪 Biological Agent")
    st.markdown("*AI-powered chemical safety analysis for consumer products*")
    st.caption("Using real MCP servers (KG, Filter, Combination, Evaluation)")
    
    with st.sidebar:
        st.header("📤 Input")
        
        input_method = st.radio("Choose input method:", ["📋 Example Product", "📁 Upload JSON", "✏️ Paste JSON"])
        
        products_list = None
        
        if input_method == "📋 Example Product":
            example = {
                "products_list": [
                    {"product_id": "1", "product_name": "Moisturizing Cream",
                     "product_usage": "cosmetics", "exposure_type": "skin",
                     "ingredient_list": [
                         {"name": "AQUA"}, {"name": "SODIUM LAURETH SULFATE"},
                         {"name": "PARFUM"}, {"name": "GLYCERIN"}
                     ]},
                    {"product_id": "2", "product_name": "Perfume Spray",
                     "product_usage": "cosmetics", "exposure_type": "skin",
                     "ingredient_list": [
                         {"name": "ALCOHOL DENAT."}, {"name": "PARFUM"},
                         {"name": "LIMONENE"}, {"name": "LINALOOL"}
                     ]}
                ]
            }
            products_list = example["products_list"]
            st.success("Using example with 2 products")
        
        elif input_method == "📁 Upload JSON":
            uploaded_file = st.file_uploader("Upload JSON", type=["json"])
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
        
        else:
            json_text = st.text_area("Paste JSON:", height=250)
            if json_text:
                success, data, error = parse_input(json_text)
                if success:
                    products_list = data
                    st.success(f"Loaded {len(products_list)} product(s)")
                else:
                    st.error(error)
        
        analyze_button = st.button("🚀 Analyze Products", type="primary", use_container_width=True)
    
    if analyze_button and products_list:
        logger = StreamlitLogger()
        logger.start()
        
        with st.spinner("Agent is analyzing products..."):
            try:
                report = run_agent_sync(products_list, logger)
                elapsed = logger.finish(report.get("elapsed_s", 0))
                st.session_state.report = report
                st.session_state.elapsed = elapsed
            except Exception as e:
                st.error(f"Agent failed: {e}")
                import traceback
                st.code(traceback.format_exc())
    
    if st.session_state.get("report"):
        format_report_display(st.session_state.report)
        
        st.download_button(
            label="📥 Download Report (JSON)",
            data=json.dumps(st.session_state.report, indent=2),
            file_name=f"biological_agent_report.json",
            mime="application/json"
        )


if __name__ == "__main__":
    main()