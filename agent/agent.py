"""
agent/agent.py — True MCP Agent (Phase 5 Complete)
────────────────────────────────────────────────────
FIXES APPLIED:
  1. AgentState now actively used — deduplicates chemical investigation
  2. Synthesis evidence trimmed — prevents token overflow for 8+ chemicals
  3. PHASE 5 ADDED: LLM cross-check for unresolved chemicals
  4. PHASE 5 ADDED: Fusion logic combining KG + LLM confidence
  5. PHASE 5 ADDED: Proper output schema with nested structure
  6. PHASE 5 ADDED: Confidence scores in output
  7. PHASE 1 ADDED: Product context analysis
  8. PHASE 4 ADDED: Hard escalation enforcement
  9. PHASE 6 ADDED: Output schema validation
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from groq import Groq
import config
from agent.prompts import LLM_FALLBACK_SYSTEM
from agent.state import AgentState
from models.output_schema import (
    FinalReport, ProductOutput, IngredientsSection, ChemicalEvaluation,
    ResolutionInfo, IdentityInfo, HazardInfo, BodyEffectsInfo, DoseEvaluationInfo,
    ChemicalVerdict, SafeSkipped, UnverifiedChemical, CombinationRisks,
    OrganOverlap, CumulativePresence, ProductSummary, GlobalSummary,
    ExposureEffects, OrganGlobalAnalysis,
    create_resolution_info, create_identity_info, create_hazard_info,
    create_body_effects, create_dose_evaluation, create_verdict
)

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVER_PATHS = {
    "kg":          os.path.join(_ROOT, "servers", "kg_server",          "server.py"),
    "filter":      os.path.join(_ROOT, "servers", "filter_server",      "server.py"),
    "combination": os.path.join(_ROOT, "servers", "combination_server", "server.py"),
    "evaluation":  os.path.join(_ROOT, "servers", "evaluation_server",  "server.py"),
}
class TokenBudgetManager:
    """Manages Groq token budget to stay within free tier limits"""
    
    def __init__(self, max_tokens_per_minute=6000, max_llm_calls=5):
        self.max_tokens = max_tokens_per_minute
        self.max_calls = max_llm_calls
        self.used_tokens = 0
        self.call_count = 0
        self.call_history = []  # Track what was called
    
    def can_call_llm(self, estimated_tokens=300, priority="LOW") -> bool:
        """Check if we can make another LLM call"""
        # Hard limit on number of calls
        if self.call_count >= self.max_calls:
            print(f"⚠️ Token budget: Max LLM calls ({self.max_calls}) reached")
            return False
        
        # Token limit check
        if self.used_tokens + estimated_tokens > self.max_tokens:
            print(f"⚠️ Token budget: Would exceed {self.max_tokens} TPM")
            return False
        
        return True
    
    def record_call(self, chemical_name: str, tokens_used=300, purpose=""):
        """Record an LLM call for tracking"""
        self.call_count += 1
        self.used_tokens += tokens_used
        self.call_history.append({
            "chemical": chemical_name,
            "tokens": tokens_used,
            "purpose": purpose,
            "call_number": self.call_count
        })
    
    def get_remaining_calls(self) -> int:
        return self.max_calls - self.call_count
    
    def get_used_tokens(self) -> int:
        return self.used_tokens
    
    def summary(self) -> dict:
        return {
            "calls_used": self.call_count,
            "calls_remaining": self.max_calls - self.call_count,
            "tokens_used": self.used_tokens,
            "tokens_remaining": self.max_tokens - self.used_tokens
        }

class MCPClient:
    def __init__(self, server_name: str, server_path: str):
        self.name = server_name
        self.path = server_path
        self.process = None
        self._id = 0

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, self.path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self._send({"jsonrpc":"2.0","id":self._next_id(),"method":"initialize","params":{}})
        await self._recv()

    async def stop(self):
        if self.process:
            try:
                self.process.stdin.close()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except Exception:
                self.process.kill()

    def _next_id(self):
        self._id += 1
        return self._id

    async def _send(self, payload):
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()

    async def _recv(self):
        line = await self.process.stdout.readline()
        return json.loads(line.decode().strip())

    async def list_tools(self):
        await self._send({"jsonrpc":"2.0","id":self._next_id(),"method":"tools/list","params":{}})
        resp = await self._recv()
        return resp.get("result",{}).get("tools",[])

    async def call(self, tool_name, arguments):
        await self._send({"jsonrpc":"2.0","id":self._next_id(),"method":"tools/call",
                          "params":{"name":tool_name,"arguments":arguments}})
        resp = await self._recv()
        if "error" in resp:
            raise RuntimeError(f"MCP error [{self.name}/{tool_name}]: {resp['error']}")
        content = resp.get("result",{}).get("content",[])
        text = content[0].get("text","{}") if content else "{}"
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}


class GroqCaller:
    def __init__(self):
        self._client = Groq(api_key=config.GROQ_API_KEY)

    def call(self, system, user, max_tokens=2000):
        resp = self._client.chat.completions.create(
            model=config.GROQ_MODEL, temperature=0, max_tokens=max_tokens,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
        )
        return resp.choices[0].message.content.strip()

    def call_json(self, system, user, max_tokens=2000):
        raw = self.call(system, user + "\nReturn only valid JSON, no markdown.", max_tokens)
        clean = re.sub(r"^```(?:json)?\s*|\s*```$","",raw,flags=re.MULTILINE).strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            m = re.search(r"(\{.*\}|\[.*\])", clean, re.DOTALL)
            if m: return json.loads(m.group(1))
            return {"raw": raw}


class BiologicalAgent:

    
    def __init__(self):
        self.groq = GroqCaller()
        self.clients: dict[str, MCPClient] = {}
        self.state = AgentState()
        self.token_budget = TokenBudgetManager()  # ADD THIS

    async def _start_servers(self):
        for name, path in SERVER_PATHS.items():
            c = MCPClient(name, path)
            await c.start()
            self.clients[name] = c

    async def _stop_servers(self):
        for c in self.clients.values():
            await c.stop()

    async def _kg(self, tool, **kwargs):
        return await self.clients["kg"].call(tool, kwargs)

    async def _eval(self, tool, **kwargs):
        return await self.clients["evaluation"].call(tool, kwargs)

    async def _combo(self, tool, **kwargs):
        return await self.clients["combination"].call(tool, kwargs)

    async def _filter_call(self, **kwargs):
        return await self.clients["filter"].call("classify_ingredients", kwargs)

    # ============================================================
    # PHASE 1: Product Context Analysis
    # ============================================================

    def _analyze_product_context(self, products_list: list) -> dict:
        """Analyze product count and determine investigation strategy"""
        product_count = len(products_list)
        needs_cumulative = product_count >= 2
        
        exposure_types = []
        for p in products_list:
            exp = p.get("exposure_type")
            if exp:
                exposure_types.append(exp)
        
        has_mixed_usage = len(set(exposure_types)) > 1 if exposure_types else False
        
        return {
            "product_count": product_count,
            "needs_cumulative": needs_cumulative,
            "has_mixed_usage": has_mixed_usage,
            "strategy": "multiple" if needs_cumulative else "single"
        }

    # ============================================================
    # PHASE 5: LLM Cross-Check & Fusion
    # ============================================================

    def _map_llm_risk_to_level(self, h_codes: list) -> str:
        """Map LLM estimated H-codes to risk level"""
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

    async def _llm_cross_check(self, chemical_name: str, reason: str = "") -> dict:
        """Get LLM estimate for chemical not in KG - respects token budget"""
        
        # Check token budget FIRST
        if not self.token_budget.can_call_llm(estimated_tokens=300, priority=reason):
            return {
                "risk": "UNKNOWN",
                "confidence": 0.2,
                "reasoning": f"Skipped due to token budget - {len(self.token_budget.call_history)} calls used",
                "source": "SKIPPED"
            }
        
        try:
            result = await self._eval("estimate_missing_hazards", {
                "chemical_name": chemical_name,
                "reason": reason
            })
            
            # Record the call
            self.token_budget.record_call(chemical_name, tokens_used=300, purpose=reason)
            
            return {
                "risk": self._map_llm_risk_to_level(result.get("estimated_h_codes", [])),
                "confidence": result.get("confidence", 0.3),
                "reasoning": result.get("reasoning", f"LLM estimate for {chemical_name}"),
                "source": "LLM_ESTIMATE",
                "estimated_h_codes": result.get("estimated_h_codes", [])
            }
        except Exception as e:
            return {
                "risk": "UNKNOWN",
                "confidence": 0.2,
                "reasoning": f"LLM estimate failed: {str(e)[:100]}",
                "source": "ERROR"
            }

    def _fuse_risks(self, kg_risk: str, kg_confidence: float, llm_result: dict) -> tuple:
        """Fuse KG and LLM risk assessments"""
        llm_risk = llm_result.get("risk", "UNKNOWN")
        llm_confidence = llm_result.get("confidence", 0.3)
        
        if kg_confidence >= 0.7:
            return kg_risk, kg_confidence, f"High confidence KG data ({kg_confidence:.2f})"
        if kg_confidence >= 0.5 and llm_confidence >= 0.5:
            return kg_risk, (kg_confidence + llm_confidence) / 2, f"KG ({kg_confidence:.2f}) and LLM ({llm_confidence:.2f}) agree"
        if llm_confidence > kg_confidence + 0.2:
            return llm_risk, llm_confidence, f"LLM estimate ({llm_confidence:.2f}) higher confidence than KG ({kg_confidence:.2f})"
        if kg_confidence < 0.4 and llm_confidence < 0.4:
            return "UNKNOWN", max(kg_confidence, llm_confidence), f"Both low confidence - treat as UNKNOWN"
        return kg_risk, kg_confidence, f"Using KG result (confidence: {kg_confidence:.2f})"

    # ============================================================
    # PHASE 4: Hard Escalation Enforcement
    # ============================================================

    def _enforce_escalation(self, products_list: list, combination: dict) -> None:
        """Hard rule: if verdict_escalation is HIGH, force product risk to HIGH"""
        escalation = combination.get("organ_overlap", {}).get("verdict_escalation")
        
        if escalation != "HIGH":
            return
        
        # Mark for enforcement - will be used in final report
        for product in products_list:
            product["_enforced_risk"] = "HIGH"

    # ============================================================
    # Phase A: Filter
    # ============================================================

    async def _phase_filter(self, products_list):
        seen, unique = set(), []
        for p in products_list:
            for ing in p.get("ingredient_list", []):
                name = ing.get("name", "").strip()
                if name and name.upper() not in seen:
                    seen.add(name.upper())
                    unique.append({"name": name})
        result = await self._filter_call(
            ingredients=unique,
            usage=products_list[0].get("product_usage", "cosmetic") if products_list else "cosmetic"
        )
        logger.info(f"Filter: {len(result.get('chemicals',[]))} chemicals, "
                    f"{len(result.get('safe_skipped',[]))} safe")
        return result

    # ============================================================
    # Phase B: Investigate each chemical
    # ============================================================

    async def _investigate_chemical(self, name):
        """TRUE MCP AGENT — each tool result drives the next call."""
        if self.state.is_investigated(name):
            for f in self.state.findings:
                if f.get("name") == name:
                    return f
            return {"name": name, "skipped": True}

        finding = {"name": name}

        resolution = await self._kg("resolve_ingredient", ingredient_name=name)
        finding["resolution"] = resolution

        # PHASE 5: LLM Fallback for unknown chemicals
        if resolution.get("unresolved"):
            llm_estimate = await self._llm_cross_check(name, "Chemical not found in KG")
            finding.update({
                "preliminary_risk": llm_estimate.get("risk", "UNKNOWN"),
                "confidence": llm_estimate.get("confidence", 0.3),
                "kg_confidence": 0.0,
                "llm_estimate": llm_estimate,
                "recommended_depth": "basic",
                "reasoning": llm_estimate.get("reasoning", f"{name} not found in KG - LLM estimate"),
                "h_codes": llm_estimate.get("estimated_h_codes", []),
                "target_organs": [],
                "source": "LLM_ESTIMATE"
            })
            self.state.mark_unresolved(name)
            self.state.add_finding(finding)
            return finding

        uid = resolution["uid"]
        finding["uid"] = uid
        self.state.mark_resolved(name, uid)

        hazard = await self._kg("get_hazard_profile", chemical_uid=uid)
        finding["hazard"] = hazard

        metrics = await self._eval("get_investigation_metrics", {
            "chemical_name": name,
            "resolution_result": resolution,
            "hazard_result": hazard
        })
        finding["metrics"] = metrics
        finding["preliminary_risk"] = metrics.get("preliminary_risk", "UNKNOWN")
        finding["recommended_depth"] = metrics.get("recommended_depth", "basic")
        finding["reasoning"] = metrics.get("reasoning", "")
        finding["h_codes"] = hazard.get("h_codes", [])
        
        kg_confidence = metrics.get("confidence", metrics.get("kg_confidence", 0.5))
        finding["kg_confidence"] = kg_confidence
        finding["confidence"] = kg_confidence
        self.state.set_confidence(name, kg_confidence)

        depth = metrics.get("recommended_depth", "basic")

        # PHASE 5: Low confidence trigger LLM cross-check
        if kg_confidence < 0.4 and depth != "skip":
            llm = await self._llm_cross_check(name, f"Low KG confidence ({kg_confidence})")
            finding["llm_estimate"] = llm
            fused_risk, fused_confidence, fusion_reason = self._fuse_risks(
                finding["preliminary_risk"], kg_confidence, llm
            )
            finding["preliminary_risk"] = fused_risk
            finding["confidence"] = fused_confidence
            finding["fusion_reasoning"] = fusion_reason
            finding["source"] = "FUSED"

        if depth == "full":
            full = await self._kg("get_full_profile", chemical_uid=uid)
            finding["full_profile"] = full
            finding["target_organs"] = full.get("target_organs", [])
        elif depth == "basic":
            organs = await self._kg("get_target_organs", chemical_uid=uid)
            finding["target_organs"] = organs.get("organs", [])
        else:
            finding["target_organs"] = []

        if depth != "skip":
            limits = await self._kg("get_exposure_limits", chemical_uid=uid)
            finding["exposure_limits"] = limits

        self.state.add_finding(finding)
        return finding

    # ============================================================
    # Phase C: Combination analysis
    # ============================================================

    async def _phase_combination(self, findings, products_list):
        """Phase 4: Combination Analysis with global_mode support"""
        
        # Build list of all chemicals with product_id for global analysis
        all_chemicals = []
        
        # Create product ingredient sets for matching
        product_ingredients = {}
        for product in products_list:
            pid = product.get("product_id", "unknown")
            product_ingredients[pid] = {ing.get("name", "").upper() for ing in product.get("ingredient_list", [])}
        
        for finding in findings:
            if finding.get("skipped"):
                continue
            
            chem_name = finding.get("name", "")
            chem_name_upper = chem_name.upper()
            
            # Find which products contain this chemical
            for product in products_list:
                pid = product.get("product_id", "unknown")
                if chem_name_upper in product_ingredients.get(pid, set()):
                    all_chemicals.append({
                        "name": chem_name,
                        "uid": finding.get("uid"),
                        "target_organs": finding.get("target_organs", []),
                        "h_codes": finding.get("h_codes", []),
                        "product_id": pid
                    })
        
        # Remove duplicates (same chemical from same product)
        seen = set()
        unique_chemicals = []
        for chem in all_chemicals:
            key = (chem["name"], chem["product_id"])
            if key not in seen:
                seen.add(key)
                unique_chemicals.append(chem)
        
        # SINGLE CALL to combination server with global_mode=True
        organ_result = await self._combo("check_organ_overlap", {
            "chemicals": unique_chemicals,
            "global_mode": True
        })
        
        # Cumulative presence (same chemical in multiple products)
        freq: dict = {}
        for p in products_list:
            pid = p.get("product_id", "?")
            pname = p.get("product_name", "?")
            exp_type = p.get("exposure_type", "unknown")
            for ing in p.get("ingredient_list", []):
                n = ing.get("name", "").strip()
                if not n:
                    continue
                key = n.upper()
                freq.setdefault(key, []).append({
                    "product_id": pid,
                    "product_name": pname,
                    "original_name": n,
                    "exposure_type": exp_type
                })
        
        cumulative_flags = []
        for key, prod_list in freq.items():
            if len(prod_list) >= 2:
                cf = await self._combo("check_cumulative_presence",
                    chemical_name=prod_list[0]["original_name"],
                    products=prod_list)
                if cf.get("is_cumulative"):
                    cumulative_flags.append(cf)
        
        # Hazard intersection
        hazard_profiles = [
            {"name": f.get("name", ""), "h_codes": f.get("h_codes", [])}
            for f in findings if not f.get("skipped")
        ]
        hazard_intersection = await self._combo("check_hazard_intersection",
                                                chemicals=hazard_profiles)
        
        return {
            "organ_overlap": organ_result,
            "cumulative_flags": cumulative_flags,
            "hazard_intersection": hazard_intersection
        }

    # ============================================================
    # Phase D: Build Final Report
    # ============================================================

    def _build_final_report(self, products_list, filter_result, findings, combination) -> dict:
        """Build final report using global_organ_analysis from combination server."""
        
        report_id = f"rpt_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Extract global organ analysis from combination result
        organ_overlap_result = combination.get("organ_overlap", {})
        global_organ_analysis_raw = organ_overlap_result.get("global_organ_analysis", {})
        
        # Convert to OrganGlobalAnalysis dataclass
        global_organ_analysis = {}
        for organ, data in global_organ_analysis_raw.items():
            global_organ_analysis[organ] = OrganGlobalAnalysis(
                unique_chemicals=data.get("unique_chemicals", []),
                total_unique_count=data.get("total_unique_count", 0),
                chemical_frequency=data.get("chemical_frequency", {}),
                products_per_chemical=data.get("products_per_chemical", {})
            )
        
        # Build products output
        products_output = []
        
        for product in products_list:
            product_id = product.get("product_id", "unknown")
            product_name = product.get("product_name", "Unknown Product")
            usage = product.get("product_usage", "unknown")
            exposure_type = [product.get("exposure_type", "unknown")] if product.get("exposure_type") else []
            
            # Apply escalation enforcement
            enforced_risk = product.get("_enforced_risk")
            
            # Build drivers
            drivers = []
            product_ingredients = {ing.get("name", "").upper() for ing in product.get("ingredient_list", [])}
            
            for f in findings:
                if f.get("skipped"):
                    continue
                if enforced_risk == "HIGH" or f.get("preliminary_risk") in ["CRITICAL", "HIGH"]:
                    chem_name_upper = f.get("name", "").upper()
                    if chem_name_upper in product_ingredients:
                        drivers.append(f.get("name", ""))
            
            chemicals_evaluated = []
            safe_skipped_list = []
            unverified_list = []
            
            for f in findings:
                if f.get("skipped"):
                    continue
                
                chem_name = f.get("name", "")
                chem_name_upper = chem_name.upper()
                
                if chem_name_upper not in product_ingredients:
                    continue
                
                uid = f.get("uid")
                cas = f.get("resolution", {}).get("cas") if f.get("resolution") else None
                
                resolution_info = create_resolution_info(
                    f.get("resolution", {}),
                    f.get("kg_confidence", 0.5)
                )
                identity_info = create_identity_info(f.get("full_profile", {}))
                hazard_info = create_hazard_info(f.get("hazard", {}))
                body_effects = create_body_effects(f.get("full_profile", {}))
                dose_eval = create_dose_evaluation(f.get("exposure_limits", {}))
                
                justifications = []
                if f.get("reasoning"):
                    justifications.append(f.get("reasoning"))
                if f.get("fusion_reasoning"):
                    justifications.append(f.get("fusion_reasoning"))
                if not justifications:
                    justifications.append(f"Risk level: {f.get('preliminary_risk', 'UNKNOWN')}")
                
                # Apply enforced risk
                final_risk = enforced_risk if enforced_risk == "HIGH" else f.get("preliminary_risk", "UNKNOWN")
                verdict = create_verdict(final_risk, justifications)
                
                chemicals_evaluated.append(ChemicalEvaluation(
                    name=chem_name,
                    uid=uid,
                    cas=cas,
                    resolution=resolution_info,
                    identity=identity_info,
                    hazard=hazard_info,
                    body_effects=body_effects,
                    dose_evaluation=dose_eval,
                    verdict=verdict
                ))
            
            for safe in filter_result.get("safe_skipped", []):
                safe_name = safe.get("name", "")
                safe_name_upper = safe_name.upper()
                if safe_name_upper in product_ingredients:
                    safe_skipped_list.append(SafeSkipped(
                        name=safe_name,
                        reason=safe.get("reason", "Classified as non-chemical")
                    ))
            
            for f in findings:
                if f.get("resolution", {}).get("unresolved"):
                    chem_name = f.get("name", "").upper()
                    if chem_name in product_ingredients:
                        unverified_list.append(UnverifiedChemical(
                            name=f.get("name", ""),
                            reason=f.get("reasoning", "Not found in Knowledge Graph"),
                            flag="unverified_chemical"
                        ))
            
            # Per-product organ overlaps from global analysis
            product_specific_overlaps = []
            for organ, data in global_organ_analysis_raw.items():
                chem_list = []
                for chem, products_for_chem in data.get("products_per_chemical", {}).items():
                    if product_id in products_for_chem:
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
                verdict_escalation=organ_overlap_result.get("verdict_escalation"),
                overlapping_organs=product_specific_overlaps if product_specific_overlaps else None,
                note=organ_overlap_result.get("summary"),
                error_message=None
            )
            
            cumulative_flags_for_product = []
            for cf in combination.get("cumulative_flags", []):
                if cf.get("chemical_name", "").upper() in product_ingredients:
                    cumulative_flags_for_product.append(cf)
            
            cumulative_obj = CumulativePresence(
                fetch_status="done" if cumulative_flags_for_product else "skipped",
                checked=len(cumulative_flags_for_product) > 0,
                note=f"{len(cumulative_flags_for_product)} chemical(s) appear in multiple products" if cumulative_flags_for_product else "No cumulative concerns detected"
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
                product_id=product_id,
                product_name=product_name,
                usage=usage,
                exposure_type=exposure_type,
                drivers=list(set(drivers))[:5],
                ingredients=IngredientsSection(
                    chemicals_evaluated=chemicals_evaluated,
                    safe_skipped=safe_skipped_list,
                    unverified_chemicals=unverified_list
                ),
                combination_risks=combination_risks,
                summary=summary
            ))
        
        # Global summary
        all_critical = []
        all_high = []
        all_organs = set()
        
        for f in findings:
            if f.get("skipped"):
                continue
            risk = f.get("preliminary_risk", "UNKNOWN")
            if risk == "CRITICAL":
                all_critical.append(f.get("name", ""))
            elif risk == "HIGH":
                all_high.append(f.get("name", ""))
            for organ in f.get("target_organs", []):
                all_organs.add(organ)
        
        global_summary = GlobalSummary(
            total_products=len(products_list),
            products_to_avoid=sum(1 for p in products_output if p.summary.critical > 0 or p.summary.high > 0),
            products_to_reduce=sum(1 for p in products_output if p.summary.moderate > 0),
            products_safe=sum(1 for p in products_output if p.summary.critical == 0 and p.summary.high == 0 and p.summary.moderate == 0),
            products_unknown=sum(1 for p in products_output if p.summary.unknown > 0),
            unique_chemicals_found=len(set(f.get("name") for f in findings if not f.get("skipped"))),
            critical_chemicals=all_critical[:10],
            high_chemicals=all_high[:10],
            organs_under_pressure=list(all_organs)[:10] if all_organs else None,
            depth_used="full",
            organ_global_analysis=global_organ_analysis
        )
        
        report = FinalReport(
            report_id=report_id,
            analyzed_at=datetime.now().isoformat(),
            agent_version="2.0.0",
            no_dose_data=True,
            depth="full",
            products=products_output,
            global_summary=global_summary
        )
        
        # Convert to dict
        def to_dict(obj):
            if hasattr(obj, '__dataclass_fields__'):
                return {k: to_dict(v) for k, v in obj.__dict__.items()}
            elif isinstance(obj, list):
                return [to_dict(i) for i in obj]
            elif isinstance(obj, dict):
                return {k: to_dict(v) for k, v in obj.items()}
            else:
                return obj
        
        report_dict = to_dict(report)
        
        # Backward compatibility
        backward_compatible = {
            "product_verdicts": [
                {
                    "product_id": p["product_id"],
                    "product_name": p["product_name"],
                    "risk_level": "HIGH" if p["summary"]["critical"] > 0 or p["summary"]["high"] > 0 
                                 else "MODERATE" if p["summary"]["moderate"] > 0 
                                 else "LOW",
                    "recommendation": "avoid" if p["summary"]["critical"] > 0 or p["summary"]["high"] > 0 
                                     else "reduce_use" if p["summary"]["moderate"] > 0 
                                     else "keep",
                    "recommendation_reason": f"Based on {p['summary']['critical']} critical, {p['summary']['high']} high risk chemicals",
                    "risk_drivers": p.get("drivers", [])
                }
                for p in report_dict["products"]
            ],
            "chemicals_summary": [
                {
                    "name": c["name"],
                    "risk_level": c["verdict"]["danger_level"],
                    "confidence": c["resolution"].get("confidence", 0.5) if c["resolution"].get("confidence") else 0.5,
                    "key_hazards": c["hazard"]["h_codes"][:5],
                    "target_organs": c["body_effects"]["target_organs"],
                    "is_unresolved": c["resolution"]["fetch_status"] == "error",
                    "source": "KG" if (c["resolution"].get("confidence") or 0) >= 0.7 
                             else "LLM_ESTIMATE" if (c["resolution"].get("confidence") or 0) < 0.4 
                             else "FUSED"
                }
                for p in report_dict["products"]
                for c in p["ingredients"]["chemicals_evaluated"]
            ],
            "combination_risks": {
                "organ_overlap_summary": next(
                    (p["combination_risks"]["organ_overlap"].get("note", "No overlap") 
                     for p in report_dict["products"] 
                     if p["combination_risks"]["organ_overlap"].get("has_overlap")), 
                    "No organ overlap detected"
                ),
                "cumulative_chemicals": [],
                "verdict_escalation": next(
                    (p["combination_risks"]["organ_overlap"].get("verdict_escalation") 
                     for p in report_dict["products"] 
                     if p["combination_risks"]["organ_overlap"].get("verdict_escalation")), 
                    None
                )
            },
            "overall_assessment": f"Analysis of {len(products_list)} product(s) completed. {report_dict['global_summary']['critical_chemicals']} critical, {report_dict['global_summary']['high_chemicals']} high risk.",
            "safe_ingredients": [s["name"] for p in report_dict["products"] for s in p["ingredients"]["safe_skipped"]],
            "unverified_chemicals": [u["name"] for p in report_dict["products"] for u in p["ingredients"]["unverified_chemicals"]]
        }
        
        report_dict.update(backward_compatible)
        
        return report_dict

    # ============================================================
    # Phase E: Public API
    # ============================================================

    async def run(self, products_list):
        start = datetime.now(timezone.utc)
        config.validate()
        
        # PHASE 1: Product context analysis
        context = self._analyze_product_context(products_list)
        logger.info(f"Product context: {context}")
        
        await self._start_servers()
        try:
            filter_result = await self._phase_filter(products_list)
            chemicals = filter_result.get("chemicals", [])
            findings = []
            for chem in chemicals:
                name = chem.get("name", "").strip()
                if name:
                    findings.append(await self._investigate_chemical(name))
            
            combination = await self._phase_combination(findings, products_list)
            
            # PHASE 4: Enforce escalation (hard rule)
            self._enforce_escalation(products_list, combination)
            
            await asyncio.sleep(2.0)
            report = self._build_final_report(products_list, filter_result, findings, combination)
        finally:
            await self._stop_servers()
        
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        return {
            "analyzed_at": start.isoformat(),
            "elapsed_s": round(elapsed, 1),
            "agent_stats": self.state.summary(),
            "report": report
        }


async def run_evaluation(products_list):
    return await BiologicalAgent().run(products_list)