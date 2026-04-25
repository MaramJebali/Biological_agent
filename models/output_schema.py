"""
Output Schema for Biological Agent
Contract between agent and scoring/personalisation server.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime


@dataclass
class ResolutionInfo:
    fetch_status: str
    method: Optional[str]
    confidence: Optional[float]
    matched_name: Optional[str]
    flag: Optional[str]
    error_message: Optional[str]


@dataclass
class IdentityInfo:
    fetch_status: str
    preferred_name: Optional[str]
    molecular_formula: Optional[str]
    chemical_classes: List[str]
    use_categories: List[str]
    error_message: Optional[str]


@dataclass
class HazardInfo:
    fetch_status: str
    worst_signal: Optional[str]
    h_codes: List[str]
    categories: List[str]
    ghs_pictograms: List[str]
    toxicity_measures: List[Dict[str, Any]]
    error_message: Optional[str]


@dataclass
class ExposureEffects:
    skin: Dict[str, Any]
    eyes: Dict[str, Any]
    inhalation: Dict[str, Any]
    ingestion: Dict[str, Any]


@dataclass
class BodyEffectsInfo:
    fetch_status: str
    target_organs: List[str]
    excretion_routes: List[str]
    exposure_effects: ExposureEffects
    error_message: Optional[str]


@dataclass
class StrictestLimit:
    standard: Optional[str]
    type: Optional[str]
    value: Optional[str]
    unit: Optional[str]


@dataclass
class DoseEvaluationInfo:
    fetch_status: str
    available: bool
    note: str
    exposure_limits_exist: Optional[bool]
    limits_count: Optional[int]
    strictest_limit: Optional[StrictestLimit]
    error_message: Optional[str]


@dataclass
class ChemicalVerdict:
    danger_level: str
    justification: List[str]


@dataclass
class ChemicalEvaluation:
    name: str
    uid: Optional[str]
    cas: Optional[str]
    resolution: ResolutionInfo
    identity: IdentityInfo
    hazard: HazardInfo
    body_effects: BodyEffectsInfo
    dose_evaluation: DoseEvaluationInfo
    verdict: ChemicalVerdict


@dataclass
class SafeSkipped:
    name: str
    reason: str


@dataclass
class UnverifiedChemical:
    name: str
    reason: str
    flag: str


@dataclass
class IngredientsSection:
    chemicals_evaluated: List[ChemicalEvaluation]
    safe_skipped: List[SafeSkipped]
    unverified_chemicals: List[UnverifiedChemical]


@dataclass
class OrganOverlap:
    fetch_status: str
    has_overlap: Optional[bool]
    verdict_escalation: Optional[str]
    overlapping_organs: Optional[List[Dict[str, Any]]]
    note: Optional[str]
    error_message: Optional[str]


@dataclass
class CumulativePresence:
    fetch_status: str
    checked: bool
    note: str


@dataclass
class CombinationRisks:
    organ_overlap: OrganOverlap
    cumulative_presence: CumulativePresence


@dataclass
class ProductSummary:
    total_ingredients: int
    chemicals_evaluated: int
    safe_skipped: int
    unverified: int
    critical: int
    high: int
    moderate: int
    low: int
    safe: int
    unknown: int
    organ_overlap_flags: Optional[int]


@dataclass
class ProductOutput:
    product_id: str
    product_name: str
    usage: str
    exposure_type: List[str]
    drivers: List[str]
    ingredients: IngredientsSection
    combination_risks: CombinationRisks
    summary: ProductSummary


@dataclass
class OrganGlobalAnalysis:
    unique_chemicals: List[str]
    total_unique_count: int
    chemical_frequency: Dict[str, int]
    products_per_chemical: Dict[str, List[str]]


@dataclass
class GlobalSummary:
    total_products: int
    products_to_avoid: int
    products_to_reduce: int
    products_safe: int
    products_unknown: int
    unique_chemicals_found: int
    critical_chemicals: List[str]
    high_chemicals: List[str]
    organs_under_pressure: Optional[List[str]]
    depth_used: str
    organ_global_analysis: Dict[str, OrganGlobalAnalysis]


@dataclass
class FinalReport:
    report_id: str
    analyzed_at: str
    agent_version: str
    no_dose_data: bool
    depth: str
    products: List[ProductOutput]
    global_summary: GlobalSummary


# ============================================================
# Builder Functions
# ============================================================

def create_resolution_info(resolution_result: dict, kg_confidence: float) -> ResolutionInfo:
    unresolved = resolution_result.get("unresolved", False)
    match_strat = resolution_result.get("match_strategy", "not_found")
    
    method_map = {
        "exact_match": "name_exact",
        "cas_match": "cas_exact",
        "synonym_match": "synonym",
        "partial_match": "fuzzy",
        "not_found": "unresolved"
    }
    
    if unresolved:
        confidence = 0.0
        flag = "unverified_chemical"
    elif match_strat == "partial_match":
        confidence = kg_confidence * 0.5
        flag = "low_confidence_match"
    else:
        confidence = kg_confidence
        flag = None
    
    return ResolutionInfo(
        fetch_status="done" if not unresolved else "error",
        method=method_map.get(match_strat, "unresolved"),
        confidence=confidence,
        matched_name=resolution_result.get("preferred_name") or resolution_result.get("name"),
        flag=flag,
        error_message=resolution_result.get("error")
    )


def create_identity_info(kg_data: dict) -> IdentityInfo:
    return IdentityInfo(
        fetch_status="done" if kg_data else "not_called",
        preferred_name=kg_data.get("preferred_name") if kg_data else None,
        molecular_formula=kg_data.get("molecular_formula") if kg_data else None,
        chemical_classes=kg_data.get("chemical_classes", []) if kg_data else [],
        use_categories=kg_data.get("use_categories", []) if kg_data else [],
        error_message=None
    )


def create_hazard_info(hazard_result: dict) -> HazardInfo:
    if not hazard_result:
        return HazardInfo(
            fetch_status="not_called",
            worst_signal=None,
            h_codes=[],
            categories=[],
            ghs_pictograms=[],
            toxicity_measures=[],
            error_message=None
        )
    
    return HazardInfo(
        fetch_status="done",
        worst_signal=hazard_result.get("highest_signal"),
        h_codes=hazard_result.get("h_codes", []),
        categories=[],
        ghs_pictograms=[],
        toxicity_measures=hazard_result.get("toxicity", []),
        error_message=None
    )


def create_body_effects(full_profile: dict) -> BodyEffectsInfo:
    if not full_profile:
        return BodyEffectsInfo(
            fetch_status="not_called",
            target_organs=[],
            excretion_routes=[],
            exposure_effects=ExposureEffects(
                skin={"name": None, "relevant": None},
                eyes={"name": None, "relevant": None},
                inhalation={"name": None, "relevant": None},
                ingestion={"name": None, "relevant": None}
            ),
            error_message=None
        )
    
    return BodyEffectsInfo(
        fetch_status="done",
        target_organs=full_profile.get("target_organs", []),
        excretion_routes=full_profile.get("excretion_routes", []),
        exposure_effects=ExposureEffects(
            skin={"name": next(iter(full_profile.get("skin_effects", [])), None), "relevant": bool(full_profile.get("skin_effects"))},
            eyes={"name": next(iter(full_profile.get("eye_effects", [])), None), "relevant": bool(full_profile.get("eye_effects"))},
            inhalation={"name": next(iter(full_profile.get("inhalation_effects", [])), None), "relevant": bool(full_profile.get("inhalation_effects"))},
            ingestion={"name": next(iter(full_profile.get("ingestion_effects", [])), None), "relevant": bool(full_profile.get("ingestion_effects"))}
        ),
        error_message=None
    )


def create_dose_evaluation(limits_result: dict) -> DoseEvaluationInfo:
    limits = limits_result.get("exposure_limits", []) if limits_result else []
    strictest = None
    if limits:
        strictest = StrictestLimit(
            standard=limits[0].get("standard"),
            type=limits[0].get("type"),
            value=str(limits[0].get("value")) if limits[0].get("value") else None,
            unit=limits[0].get("unit")
        )
    
    return DoseEvaluationInfo(
        fetch_status="done" if limits_result else "not_called",
        available=False,
        note="No dose data provided - qualitative assessment only",
        exposure_limits_exist=len(limits) > 0 if limits_result else None,
        limits_count=len(limits) if limits_result else None,
        strictest_limit=strictest,
        error_message=None
    )


def create_verdict(risk_level: str, justifications: List[str]) -> ChemicalVerdict:
    return ChemicalVerdict(danger_level=risk_level, justification=justifications)