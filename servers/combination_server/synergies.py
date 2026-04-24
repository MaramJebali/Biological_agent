"""
servers/combination_server/synergies.py
────────────────────────────────────────
Pure Python logic for multi-chemical risk analysis.
Zero LLM calls. Zero Neo4j calls. Fully deterministic.

The combination server receives data already fetched by the KG server.
It only analyzes relationships between chemicals — it never fetches.

Three analyses:
  check_organ_overlap()        — organs hit by 2+ chemicals
  check_cumulative_presence()  — same chemical in 2+ products
  check_hazard_intersection()  — H-codes shared across chemicals

CRITICAL DESIGN NOTE:
  Some chemicals have no AFFECTS_ORGAN relationships in the KG
  (e.g. SLS has H372 "organ damage" but no TargetOrgan node).
  These are handled via ORGAN_DAMAGE_H_CODES fallback.
  Never treat empty target_organs as "no organ risk".
"""
from __future__ import annotations
from collections import defaultdict

# H-codes that signal organ damage even without explicit TargetOrgan nodes
ORGAN_DAMAGE_H_CODES = {"H370", "H371", "H372", "H373"}

# H-codes classified as critical for escalation purposes
CRITICAL_H_CODES = {
    "H340", "H341",              # Mutagenicity
    "H350", "H351",              # Carcinogenicity
    "H360", "H361", "H362",      # Reproductive toxicity
    "H370", "H371", "H372", "H373",  # STOT
}

# Organ overlap thresholds
MODERATE_THRESHOLD = 2   # 2+ chemicals on same organ → MODERATE
HIGH_THRESHOLD     = 3   # 3+ chemicals on same organ → HIGH


# ── Tool 1: check_organ_overlap ───────────────────────────────────────────────

def check_organ_overlap(chemicals: list[dict]) -> dict:
    """
    Find organs targeted by 2 or more chemicals.

    Args:
        chemicals: list of dicts, each must have:
            {
              "name":          str,
              "uid":           str | None,
              "target_organs": list[str],   ← from get_target_organs tool
              "h_codes":       list[str],   ← from get_hazard_profile tool
            }

    Returns:
        {
          "has_overlap":        bool,
          "overlapping_organs": [
            {
              "organ":      str,
              "chemicals":  [str],
              "count":      int,
              "risk_flag":  "HIGH" | "MODERATE",
              "message":    str,
            }
          ],
          "unspecified_organ_damage": [str],  chemicals with H370-H373 but no organ nodes
          "max_chemicals_per_organ":  int,
          "verdict_escalation":       "HIGH" | "MODERATE" | null,
          "summary":                  str,
        }
    """
    if not chemicals:
        return _empty_overlap()

    # Build organ → [chemical_name] map
    organ_map: dict[str, list[str]] = defaultdict(list)
    unspecified_damage: list[str]   = []

    for chem in chemicals:
        name   = chem.get("name", "unknown")
        organs = [o for o in (chem.get("target_organs") or []) if o]
        h_codes = set(chem.get("h_codes") or [])

        if organs:
            for organ in organs:
                organ_map[organ.lower().strip()].append(name)
        elif h_codes & ORGAN_DAMAGE_H_CODES:
            # Chemical has organ-damage H-codes but no explicit organ nodes
            # Flag it separately — cannot determine which organ, but risk exists
            unspecified_damage.append(name)
            # Add to a synthetic "unspecified" organ so it participates in overlap
            organ_map["organ_damage_unspecified"].append(name)

    # Find overlapping organs (2+ chemicals)
    overlapping = []
    max_count   = 0

    for organ, chems in organ_map.items():
        count = len(chems)
        if count >= MODERATE_THRESHOLD:
            max_count = max(max_count, count)
            flag = "HIGH" if count >= HIGH_THRESHOLD else "MODERATE"
            overlapping.append({
                "organ":     organ,
                "chemicals": chems,
                "count":     count,
                "risk_flag": flag,
                "message":   (
                    f"{count} chemicals affect {organ} — "
                    f"{'high' if flag == 'HIGH' else 'moderate'} combined organ pressure"
                ),
            })

    # Sort by count descending
    overlapping.sort(key=lambda x: x["count"], reverse=True)

    # Determine escalation
    escalation = None
    if max_count >= HIGH_THRESHOLD:
        escalation = "HIGH"
    elif max_count >= MODERATE_THRESHOLD:
        escalation = "MODERATE"

    summary_parts = []
    if overlapping:
        summary_parts.append(
            f"{len(overlapping)} organ(s) targeted by multiple chemicals"
        )
    if unspecified_damage:
        summary_parts.append(
            f"{len(unspecified_damage)} chemical(s) with unspecified organ damage "
            f"({', '.join(unspecified_damage)})"
        )
    if not summary_parts:
        summary_parts.append("No organ overlap detected")

    return {
        "has_overlap":               len(overlapping) > 0,
        "overlapping_organs":        overlapping,
        "unspecified_organ_damage":  unspecified_damage,
        "max_chemicals_per_organ":   max_count,
        "verdict_escalation":        escalation,
        "summary":                   ". ".join(summary_parts),
    }


def _empty_overlap() -> dict:
    return {
        "has_overlap":               False,
        "overlapping_organs":        [],
        "unspecified_organ_damage":  [],
        "max_chemicals_per_organ":   0,
        "verdict_escalation":        None,
        "summary":                   "No chemicals provided",
    }


# ── Tool 2: check_cumulative_presence ─────────────────────────────────────────

def check_cumulative_presence(
    chemical_name: str,
    products: list[dict],
) -> dict:
    """
    Check if the same chemical appears in multiple products.
    Without dose data, this is a presence-based cumulative risk signal.

    Args:
        chemical_name: name of the chemical to check
        products: list of {product_id: str, product_name: str}
                  — all products that contain this chemical

    Returns:
        {
          "chemical_name":  str,
          "frequency":      int,
          "products":       [{product_id, product_name}],
          "is_cumulative":  bool,   true if appears in 2+ products
          "risk_note":      str,
          "recommendation": str,
        }
    """
    frequency = len(products)
    is_cumulative = frequency >= 2

    if is_cumulative:
        risk_note = (
            f"{chemical_name} appears in {frequency} products. "
            f"Total exposure is compounded even without dose data. "
            f"The user encounters this chemical multiple times per routine."
        )
        recommendation = (
            f"Review all {frequency} products containing {chemical_name}. "
            f"Consider using fewer products with this ingredient."
        )
    else:
        risk_note = f"{chemical_name} appears in only 1 product — no cumulative concern."
        recommendation = "No action needed for cumulative exposure."

    return {
        "chemical_name":  chemical_name,
        "frequency":      frequency,
        "products":       products,
        "is_cumulative":  is_cumulative,
        "risk_note":      risk_note,
        "recommendation": recommendation,
    }


# ── Tool 3: check_hazard_intersection ─────────────────────────────────────────

def check_hazard_intersection(chemicals: list[dict]) -> dict:
    """
    Find H-codes shared across 2 or more chemicals.
    Shared hazards amplify risk — especially shared critical H-codes.

    Args:
        chemicals: list of {name: str, h_codes: list[str]}

    Returns:
        {
          "shared_h_codes":        [str],   H-codes in 2+ chemicals
          "shared_critical_codes": [str],   shared codes that are critical
          "has_critical_overlap":  bool,
          "details": {
            h_code: [chemical_names]
          },
          "severity_escalation":   bool,
          "summary":               str,
        }
    """
    if not chemicals:
        return {
            "shared_h_codes":        [],
            "shared_critical_codes": [],
            "has_critical_overlap":  False,
            "details":               {},
            "severity_escalation":   False,
            "summary":               "No chemicals provided",
        }

    # Build h_code → [chemical_names] map
    code_map: dict[str, list[str]] = defaultdict(list)
    for chem in chemicals:
        name    = chem.get("name", "unknown")
        h_codes = chem.get("h_codes") or []
        for code in h_codes:
            if code:
                code_map[code].append(name)

    # Find codes in 2+ chemicals
    shared = {
        code: chems
        for code, chems in code_map.items()
        if len(chems) >= 2
    }

    shared_codes    = sorted(shared.keys())
    critical_shared = [c for c in shared_codes if c in CRITICAL_H_CODES]

    summary_parts = []
    if shared_codes:
        summary_parts.append(
            f"{len(shared_codes)} H-code(s) shared across multiple chemicals: "
            f"{', '.join(shared_codes[:5])}"
        )
    if critical_shared:
        summary_parts.append(
            f"CRITICAL overlap: {', '.join(critical_shared)} — "
            f"multiple chemicals share serious hazards"
        )
    if not summary_parts:
        summary_parts.append("No shared hazard codes")

    return {
        "shared_h_codes":        shared_codes,
        "shared_critical_codes": critical_shared,
        "has_critical_overlap":  len(critical_shared) > 0,
        "details":               {code: chems for code, chems in shared.items()},
        "severity_escalation":   len(critical_shared) > 0,
        "summary":               ". ".join(summary_parts),
    }