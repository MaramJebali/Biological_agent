"""
servers/combination_server/synergies.py
Pure Python logic for multi-chemical risk analysis.
Supports TWO modes:
  1. Per-product mode (global_mode=False)
  2. Global mode (global_mode=True) - cross-product aggregation
"""

from __future__ import annotations
from collections import defaultdict

ORGAN_DAMAGE_H_CODES = {"H370", "H371", "H372", "H373"}
CRITICAL_H_CODES = {
    "H340", "H341", "H350", "H351", "H360", "H361", "H362", "H370", "H371", "H372", "H373"
}
MODERATE_THRESHOLD = 2
HIGH_THRESHOLD = 3


def check_organ_overlap(chemicals: list[dict], global_mode: bool = False) -> dict:
    """Main entry point - supports both per-product and global modes"""
    if not chemicals:
        return _empty_overlap(global_mode)
    
    if global_mode:
        return _check_organ_overlap_global(chemicals)
    else:
        return _check_organ_overlap_per_product(chemicals)


def _check_organ_overlap_per_product(chemicals: list[dict]) -> dict:
    """Original per-product organ overlap logic"""
    organ_map: dict[str, list[str]] = defaultdict(list)
    unspecified_damage: list[str] = []

    for chem in chemicals:
        name = chem.get("name", "unknown")
        organs = [o for o in (chem.get("target_organs") or []) if o]
        h_codes = set(chem.get("h_codes") or [])
        
        if organs:
            for organ in organs:
                organ_map[organ.lower().strip()].append(name)
        elif h_codes & ORGAN_DAMAGE_H_CODES:
            unspecified_damage.append(name)
            organ_map["organ_damage_unspecified"].append(name)
    
    overlapping = []
    max_count = 0
    
    for organ, chems in organ_map.items():
        count = len(chems)
        if count >= MODERATE_THRESHOLD:
            max_count = max(max_count, count)
            flag = "HIGH" if count >= HIGH_THRESHOLD else "MODERATE"
            overlapping.append({
                "organ": organ,
                "chemicals": chems,
                "count": count,
                "risk_flag": flag,
                "message": f"{count} chemicals affect {organ} — {'high' if flag == 'HIGH' else 'moderate'} combined organ pressure"
            })
    
    overlapping.sort(key=lambda x: x["count"], reverse=True)
    
    escalation = None
    if max_count >= HIGH_THRESHOLD:
        escalation = "HIGH"
    elif max_count >= MODERATE_THRESHOLD:
        escalation = "MODERATE"
    
    return {
        "has_overlap": len(overlapping) > 0,
        "overlapping_organs": overlapping,
        "unspecified_organ_damage": unspecified_damage,
        "max_chemicals_per_organ": max_count,
        "verdict_escalation": escalation,
        "summary": f"{len(overlapping)} organ(s) targeted by multiple chemicals"
    }


def _check_organ_overlap_global(chemicals: list[dict]) -> dict:
    """
    Global organ overlap across multiple products.
    Each chemical MUST include 'product_id' field.
    Returns unique chemicals per organ with frequency across products.
    """
    organ_data: dict = defaultdict(lambda: {
        "chemicals": set(),
        "frequency": defaultdict(int),
        "products": defaultdict(set)
    })
    
    for chem in chemicals:
        name = chem.get("name", "unknown")
        product_id = chem.get("product_id")
        organs = [o for o in (chem.get("target_organs") or []) if o]
        h_codes = set(chem.get("h_codes") or [])
        
        if not organs and (h_codes & ORGAN_DAMAGE_H_CODES):
            organs = ["unspecified_organ_damage"]
        
        for organ in organs:
            organ_key = organ.lower().strip()
            organ_data[organ_key]["chemicals"].add(name)
            if product_id:
                organ_data[organ_key]["frequency"][name] += 1
                organ_data[organ_key]["products"][name].add(product_id)
    
    global_organ_analysis = {}
    max_chemicals = 0
    
    for organ, data in organ_data.items():
        unique_chemicals = list(data["chemicals"])
        chem_count = len(unique_chemicals)
        max_chemicals = max(max_chemicals, chem_count)
        
        global_organ_analysis[organ] = {
            "unique_chemicals": unique_chemicals,
            "total_unique_count": chem_count,
            "chemical_frequency": dict(data["frequency"]),
            "products_per_chemical": {chem: list(prods) for chem, prods in data["products"].items()}
        }
    
    escalation = None
    if max_chemicals >= HIGH_THRESHOLD:
        escalation = "HIGH"
    elif max_chemicals >= MODERATE_THRESHOLD:
        escalation = "MODERATE"
    
    return {
        "has_overlap": len(global_organ_analysis) > 0,
        "global_organ_analysis": global_organ_analysis,
        "max_chemicals_per_organ": max_chemicals,
        "verdict_escalation": escalation,
        "summary": f"{len(global_organ_analysis)} organ(s) targeted across products. Max {max_chemicals} chemicals on a single organ."
    }


def _empty_overlap(global_mode: bool = False) -> dict:
    if global_mode:
        return {
            "has_overlap": False,
            "global_organ_analysis": {},
            "max_chemicals_per_organ": 0,
            "verdict_escalation": None,
            "summary": "No chemicals provided"
        }
    else:
        return {
            "has_overlap": False,
            "overlapping_organs": [],
            "unspecified_organ_damage": [],
            "max_chemicals_per_organ": 0,
            "verdict_escalation": None,
            "summary": "No chemicals provided"
        }


def check_cumulative_presence(chemical_name: str, products: list[dict]) -> dict:
    frequency = len(products)
    is_cumulative = frequency >= 2
    return {
        "chemical_name": chemical_name,
        "frequency": frequency,
        "products": products,
        "is_cumulative": is_cumulative,
        "risk_note": f"{chemical_name} appears in {frequency} product(s)",
        "recommendation": "Review multi-product exposure" if is_cumulative else "No cumulative concern"
    }


def check_hazard_intersection(chemicals: list[dict]) -> dict:
    if not chemicals:
        return {"shared_h_codes": [], "shared_critical_codes": [], "has_critical_overlap": False, "details": {}, "severity_escalation": False, "summary": "No chemicals"}
    
    code_map: dict[str, list[str]] = defaultdict(list)
    for chem in chemicals:
        name = chem.get("name", "unknown")
        for code in (chem.get("h_codes") or []):
            if code:
                code_map[code].append(name)
    
    shared = {code: chems for code, chems in code_map.items() if len(chems) >= 2}
    shared_codes = sorted(shared.keys())
    critical_shared = [c for c in shared_codes if c in CRITICAL_H_CODES]
    
    return {
        "shared_h_codes": shared_codes,
        "shared_critical_codes": critical_shared,
        "has_critical_overlap": len(critical_shared) > 0,
        "details": shared,
        "severity_escalation": len(critical_shared) > 0,
        "summary": f"{len(shared_codes)} shared H-codes"
    }