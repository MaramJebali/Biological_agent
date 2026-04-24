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
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

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
    ExposureEffects,
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
        self.groq    = GroqCaller()
        self.clients: dict[str, MCPClient] = {}
        self.state   = AgentState()

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

    async def _eval(self, **kwargs):
        return await self.clients["evaluation"].call("get_investigation_metrics", kwargs)

    async def _combo(self, tool, **kwargs):
        return await self.clients["combination"].call(tool, kwargs)

    # ============================================================
    # PHASE 5: LLM Cross-Check for Unknown Chemicals
    # ============================================================
    
    async def _llm_cross_check(self, chemical_name: str) -> dict:
        """Get LLM estimate for chemical not found in KG."""
        prompt = f"Chemical: {chemical_name}\n\nEstimate the safety risk based only on the chemical name."
        
        try:
            result = self.groq.call_json(LLM_FALLBACK_SYSTEM, prompt, max_tokens=300)
            return {
                "risk": result.get("risk", "UNKNOWN"),
                "confidence": result.get("confidence", 0.3),
                "reasoning": result.get("reasoning", f"LLM estimate for {chemical_name}"),
                "source": "LLM_ESTIMATE"
            }
        except Exception as e:
            logger.error(f"LLM cross-check failed for {chemical_name}: {e}")
            return {
                "risk": "UNKNOWN",
                "confidence": 0.2,
                "reasoning": f"LLM estimate failed: {str(e)[:100]}",
                "source": "ERROR"
            }
    
    def _fuse_risks(self, kg_risk: str, kg_confidence: float, llm_risk: str, llm_confidence: float) -> tuple:
        """Fuse KG and LLM risk assessments."""
        if kg_confidence >= 0.7:
            return kg_risk, kg_confidence, f"High confidence KG data ({kg_confidence:.2f})"
        if kg_confidence >= 0.5 and llm_confidence >= 0.5:
            return kg_risk, (kg_confidence + llm_confidence) / 2, f"KG ({kg_confidence:.2f}) and LLM ({llm_confidence:.2f}) agree"
        if llm_confidence > kg_confidence + 0.2:
            return llm_risk, llm_confidence, f"LLM estimate ({llm_confidence:.2f}) higher confidence than KG ({kg_confidence:.2f})"
        if kg_confidence < 0.4 and llm_confidence < 0.4:
            return "UNKNOWN", max(kg_confidence, llm_confidence), f"Both low confidence - treat as UNKNOWN"
        return kg_risk, kg_confidence, f"Using KG result (confidence: {kg_confidence:.2f})"

    # ── Phase A: Filter ───────────────────────────────────────────────────────

    async def _phase_filter(self, products_list):
        seen, unique = set(), []
        for p in products_list:
            for ing in p.get("ingredient_list", []):
                name = ing.get("name","").strip()
                if name and name.upper() not in seen:
                    seen.add(name.upper())
                    unique.append({"name": name})
        result = await self.clients["filter"].call("classify_ingredients", {
            "ingredients": unique,
            "usage": products_list[0].get("product_usage","cosmetic"),
        })
        logger.info(f"Filter: {len(result.get('chemicals',[]))} chemicals, "
                    f"{len(result.get('safe_skipped',[]))} safe")
        return result

    # ── Phase B: Investigate each chemical ─────────────────────────────────────

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

        if resolution.get("unresolved"):
            llm = await self._llm_cross_check(name)
            finding.update({
                "preliminary_risk": llm.get("risk", "UNKNOWN"),
                "confidence": llm.get("confidence", 0.3),
                "kg_confidence": 0.0,
                "llm_estimate": llm,
                "recommended_depth": "basic",
                "reasoning": llm.get("reasoning", f"{name} not found in KG - LLM estimate"),
                "h_codes": [],
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

        metrics = await self._eval(
            chemical_name=name,
            resolution_result=resolution,
            hazard_result=hazard,
        )
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

        if kg_confidence < 0.4 and depth != "skip":
            llm = await self._llm_cross_check(name)
            finding["llm_estimate"] = llm
            fused_risk, fused_confidence, fusion_reason = self._fuse_risks(
                finding["preliminary_risk"], kg_confidence,
                llm.get("risk", "UNKNOWN"), llm.get("confidence", 0.3)
            )
            finding["preliminary_risk"] = fused_risk
            finding["confidence"] = fused_confidence
            finding["fusion_reasoning"] = fusion_reason
            finding["source"] = "FUSED"

        self.state.add_finding(finding)
        return finding

    # ── Phase C: Combination analysis ─────────────────────────────────────────

    async def _phase_combination(self, findings, products_list):
        profiles = [
            {"name": f.get("name",""), "uid": f.get("uid"),
             "target_organs": f.get("target_organs",[]), "h_codes": f.get("h_codes",[])}
            for f in findings if not f.get("skipped")
        ]
        organ_result = await self._combo("check_organ_overlap", chemicals=profiles)

        freq: dict = {}
        for p in products_list:
            pid, pname = p.get("product_id","?"), p.get("product_name","?")
            for ing in p.get("ingredient_list",[]):
                n = ing.get("name","").strip()
                if not n: continue
                key = n.upper()
                freq.setdefault(key, []).append({
                    "product_id": pid, "product_name": pname, "original_name": n
                })

        cumulative_flags = []
        for key, prod_list in freq.items():
            if len(prod_list) >= 2:
                cf = await self._combo("check_cumulative_presence",
                    chemical_name=prod_list[0]["original_name"], products=prod_list)
                if cf.get("is_cumulative"):
                    cumulative_flags.append(cf)

        hazard_profiles = [
            {"name": f.get("name",""), "h_codes": f.get("h_codes",[])}
            for f in findings if not f.get("skipped")
        ]
        hazard_intersection = await self._combo("check_hazard_intersection",
                                                chemicals=hazard_profiles)

        return {"organ_overlap": organ_result, "cumulative_flags": cumulative_flags,
                "hazard_intersection": hazard_intersection}

    # ── Phase D: Build Final Report (Structured, No LLM) ───────────────────────

    d# Convert dataclass to dict
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

# ============================================================
# Backward compatibility for tests
# ============================================================
backward_compatible = {
    "product_verdicts": [
        {
            "product_id": p["product_id"],
            "product_name": p["product_name"],
            "risk_level": "HIGH" if p["summary"]["critical"] > 0 or p["summary"]["high"] > 0 else "MODERATE" if p["summary"]["moderate"] > 0 else "LOW",
            "recommendation": "avoid" if p["summary"]["critical"] > 0 or p["summary"]["high"] > 0 else "reduce_use" if p["summary"]["moderate"] > 0 else "keep",
            "recommendation_reason": f"Based on {p['summary']['critical']} critical, {p['summary']['high']} high risk chemicals",
            "risk_drivers": p.get("drivers", [])
        }
        for p in report_dict["products"]
    ],
    "chemicals_summary": [
        {
            "name": c["name"],
            "risk_level": c["verdict"]["danger_level"],
            "confidence": c["resolution"]["confidence"] if c["resolution"]["confidence"] else 0.5,
            "key_hazards": c["hazard"]["h_codes"][:5],
            "target_organs": c["body_effects"]["target_organs"],
            "is_unresolved": c["resolution"]["fetch_status"] == "error",
            "source": "KG" if (c["resolution"]["confidence"] or 0) >= 0.7 else "LLM_ESTIMATE" if (c["resolution"]["confidence"] or 0) < 0.4 else "FUSED"
        }
        for p in report_dict["products"]
        for c in p["ingredients"]["chemicals_evaluated"]
    ],
    "combination_risks": {
        "organ_overlap_summary": next((p["combination_risks"]["organ_overlap"].get("note", "No overlap") for p in report_dict["products"] if p["combination_risks"]["organ_overlap"].get("has_overlap")), "No organ overlap detected"),
        "cumulative_chemicals": [],
        "verdict_escalation": next((p["combination_risks"]["organ_overlap"].get("verdict_escalation") for p in report_dict["products"] if p["combination_risks"]["organ_overlap"].get("verdict_escalation")), None)
    },
    "overall_assessment": f"Analysis of {len(products_list)} product(s) completed. {report_dict['global_summary']['critical_chemicals']} critical, {report_dict['global_summary']['high_chemicals']} high risk.",
    "safe_ingredients": [s["name"] for p in report_dict["products"] for s in p["ingredients"]["safe_skipped"]],
    "unverified_chemicals": [u["name"] for p in report_dict["products"] for u in p["ingredients"]["unverified_chemicals"]]
}

report_dict.update(backward_compatible)

return report_dict
    # ── Phase E: Synthesis (uses structured builder) ─────────────────────────

    def _phase_synthesis(self, products_list, filter_result, findings, combination):
        """Build final report using structured data assembly."""
        return self._build_final_report(products_list, filter_result, findings, combination)

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, products_list):
        start = datetime.now(timezone.utc)
        config.validate()
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
            await asyncio.sleep(2.0)
            report = self._phase_synthesis(products_list, filter_result, findings, combination)
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