"""
servers/evaluation_server/evaluator.py
────────────────────────────────────────
Pure Python logic for data quality assessment and investigation metrics.
Zero LLM. Zero Neo4j.

PHASE 5 UPDATE: Added confidence scoring and low confidence → UNKNOWN override
"""

from __future__ import annotations

# H-code sets for classification
CRITICAL_H = {"H340","H341","H350","H351","H360","H361","H362","H370","H372"}
DANGER_SIGNALS  = {"Danger"}
WARNING_SIGNALS = {"Warning"}


def get_investigation_metrics(
    chemical_name: str,
    resolution_result: dict,
    hazard_result: dict,
) -> dict:
    """
    Analyze KG results and return structured investigation signals.
    
    PHASE 5: Added confidence scoring with low confidence → UNKNOWN override.
    """
    uid          = resolution_result.get("uid")
    is_unresolved = resolution_result.get("unresolved", True) or not uid
    match_strat  = resolution_result.get("match_strategy", "not_found")

    # If not resolved — unknown risk
    if is_unresolved:
        return {
            "chemical_name":      chemical_name,
            "has_uid":            False,
            "is_unresolved":      True,
            "has_hazards":        False,
            "has_danger_signal":  False,
            "has_warning_signal": False,
            "has_critical_hazard":False,
            "critical_codes":     [],
            "h_code_count":       0,
            "data_completeness":  0.0,
            "recommended_depth":  "skip",
            "preliminary_risk":   "UNKNOWN",
            "reasoning": (
                f"{chemical_name} not found in KG (strategy: {match_strat}). "
                "Cannot evaluate — treat as unknown risk, not safe."
            ),
            "fetch_status": "unresolved",
            "confidence": 0.0,
            "kg_confidence": 0.0,
        }

    # Has uid — check hazard data
    h_codes    = hazard_result.get("h_codes") or []
    signal     = hazard_result.get("highest_signal", "None")
    is_critical = hazard_result.get("has_critical_hazard", False)
    crit_codes  = hazard_result.get("critical_hazards") or []
    has_hazards = len(h_codes) > 0

    has_danger  = signal == "Danger"
    has_warning = signal == "Warning"

    # If hazard data not yet fetched
    if not hazard_result:
        return {
            "chemical_name":      chemical_name,
            "has_uid":            True,
            "is_unresolved":      False,
            "has_hazards":        False,
            "has_danger_signal":  False,
            "has_warning_signal": False,
            "has_critical_hazard":False,
            "critical_codes":     [],
            "h_code_count":       0,
            "data_completeness":  0.3,
            "recommended_depth":  "basic",
            "preliminary_risk":   "UNKNOWN",
            "reasoning": (
                f"{chemical_name} resolved (uid: {uid}) but hazard data not fetched yet."
            ),
            "fetch_status": "uid_only",
            "confidence": 0.3,
            "kg_confidence": 0.3,
        }

    # Determine recommended depth
    if is_critical:
        depth = "full"
    elif has_danger:
        depth = "full"
    elif has_warning:
        depth = "basic"
    elif has_hazards:
        depth = "basic"
    else:
        depth = "skip"

    # Determine preliminary risk level
    if is_critical:
        risk = "CRITICAL"
    elif has_danger:
        risk = "HIGH"
    elif has_warning:
        risk = "MODERATE"
    elif has_hazards:
        risk = "LOW"
    else:
        risk = "SAFE"

    # Data completeness score
    completeness = 0.3   # has uid
    if h_codes:      completeness += 0.4   # has hazard data
    if is_critical is not None: completeness += 0.1
    if match_strat in ("exact_match", "cas_match"): completeness += 0.2
    completeness = min(round(completeness, 1), 1.0)

    # ============================================================
    # PHASE 5: Adjust risk based on confidence
    # If confidence is low (<0.4), override risk to UNKNOWN
    # ============================================================
    reasoning_parts = [f"{chemical_name} resolved via {match_strat}."]

    if completeness <= 0.4:
        # Low confidence - override to UNKNOWN
        risk = "UNKNOWN"
        reasoning_parts.append(
            f"WARNING: Low data completeness ({completeness:.1f}). "
            "This assessment has low confidence. Treat as UNKNOWN risk."
        )
    elif completeness < 0.7:
        # Medium confidence - keep risk but add note
        reasoning_parts.append(
            f"Note: Moderate confidence ({completeness:.1f}) due to partial data."
        )

    if is_critical:
        reasoning_parts.append(
            f"Has CRITICAL hazard codes: {crit_codes}. "
            "Full investigation required — carcinogen/mutagen/reprotoxic."
        )
    elif has_danger:
        reasoning_parts.append(
            f"Danger signal with {len(h_codes)} H-codes. "
            "Full investigation recommended."
        )
    elif has_warning:
        reasoning_parts.append(
            f"Warning signal with {len(h_codes)} H-codes. "
            "Basic investigation sufficient."
        )
    elif has_hazards:
        reasoning_parts.append(
            f"{len(h_codes)} H-codes but no Danger/Warning signal. "
            "Basic investigation sufficient."
        )
    else:
        reasoning_parts.append("No hazard statements found. Low priority.")

    return {
        "chemical_name":      chemical_name,
        "has_uid":            True,
        "is_unresolved":      False,
        "has_hazards":        has_hazards,
        "has_danger_signal":  has_danger,
        "has_warning_signal": has_warning,
        "has_critical_hazard":is_critical,
        "critical_codes":     crit_codes,
        "h_code_count":       len(h_codes),
        "data_completeness":  completeness,
        "recommended_depth":  depth,
        "preliminary_risk":   risk,
        "reasoning":          " ".join(reasoning_parts),
        "fetch_status":       "complete",
        "confidence":         completeness,
        "kg_confidence":      completeness,
    }