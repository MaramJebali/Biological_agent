"""
queries.py — Cypher queries for Neo4j KG
─────────────────────────────────────────
WHAT CHANGED FROM ORIGINAL:
  1. Added GET_FULL_PROFILE — single query replacing 11 separate queries
     This is the most critical fix. Original made 11 round trips to Aura.
     One query = 10x faster on Aura (network latency is the bottleneck).
  2. Kept all resolution queries unchanged — they were correct.
  3. Kept individual getters for backward compatibility during transition.
  4. Removed BATCH_RESOLVE — agent uses resolve_ingredient per chemical.
  5. Kept GET_ORGAN_FOR_MULTIPLE_CHEMICALS — used by combination server.
  6. Kept HAS_CRITICAL_HAZARD — merged into get_hazard_profile but kept here.
"""

# ============================================================
# RESOLUTION QUERIES — unchanged, all correct
# ============================================================

RESOLVE_INGREDIENT_EXACT = """
MATCH (c:Chemical)
WHERE toLower(c.name) = toLower($name)
   OR toLower(c.preferred_name) = toLower($name)
RETURN c.uid AS uid,
       c.name AS name,
       c.preferred_name AS preferred_name,
       c.cas AS cas,
       c.molecular_formula AS molecular_formula,
       c.molecular_weight AS molecular_weight,
       c.description AS description,
       c.synonyms AS synonyms
LIMIT 1
"""

RESOLVE_INGREDIENT_CAS = """
MATCH (c:Chemical)
WHERE c.cas = $name
RETURN c.uid AS uid,
       c.name AS name,
       c.preferred_name AS preferred_name,
       c.cas AS cas,
       c.molecular_formula AS molecular_formula,
       c.molecular_weight AS molecular_weight,
       c.description AS description,
       c.synonyms AS synonyms
LIMIT 1
"""

RESOLVE_INGREDIENT_SYNONYM = """
MATCH (c:Chemical)
WHERE ANY(synonym IN c.synonyms WHERE toLower(synonym) = toLower($name))
RETURN c.uid AS uid,
       c.name AS name,
       c.preferred_name AS preferred_name,
       c.cas AS cas,
       c.molecular_formula AS molecular_formula,
       c.molecular_weight AS molecular_weight,
       c.description AS description,
       c.synonyms AS synonyms
LIMIT 1
"""

RESOLVE_INGREDIENT_PARTIAL = """
MATCH (c:Chemical)
WHERE toLower(c.name) CONTAINS toLower($name)
   OR toLower(c.preferred_name) CONTAINS toLower($name)
RETURN c.uid AS uid,
       c.name AS name,
       c.preferred_name AS preferred_name,
       c.cas AS cas
LIMIT 1
"""


# ============================================================
# GET_FULL_PROFILE — THE KEY FIX
# Single query replacing 11 separate round trips to Aura.
# Uses OPTIONAL MATCH so missing relationships return empty lists.
# collect(DISTINCT) deduplicates results.
# ============================================================

GET_FULL_PROFILE = """
MATCH (c:Chemical {uid: $uid})
OPTIONAL MATCH (c)-[:HAS_HAZARD_STATEMENT]->(h:HazardStatement)
OPTIONAL MATCH (c)-[:AFFECTS_ORGAN]->(o:TargetOrgan)
OPTIONAL MATCH (c)-[:CLASSIFIED_AS]->(cc:ChemicalClass)
OPTIONAL MATCH (c)-[:HAS_TOXICITY_PROFILE]->(t:ToxicityMeasure)
OPTIONAL MATCH (c)-[:SUBJECT_TO_EXPOSURE_LIMIT]->(e:ExposureLimit)
OPTIONAL MATCH (c)-[:CAUSES_SKIN_EFFECT]->(sk:SkinExposure)
OPTIONAL MATCH (c)-[:CAUSES_EYE_EFFECT]->(ey:EyeExposure)
OPTIONAL MATCH (c)-[:CAUSES_INHALATION_EFFECT]->(ih:InhalationExposure)
OPTIONAL MATCH (c)-[:CAUSES_INGESTION_EFFECT]->(ig:IngestionExposure)
OPTIONAL MATCH (c)-[:EXCRETED_VIA]->(ex:ExcretionRoute)
RETURN
    c.uid               AS uid,
    c.name              AS name,
    c.preferred_name    AS preferred_name,
    c.cas               AS cas,
    c.molecular_formula AS molecular_formula,
    c.molecular_weight  AS molecular_weight,
    c.description       AS description,
    c.synonyms          AS synonyms,
    collect(DISTINCT {
        code: h.code, signal: h.signal, meaning: h.meaning, category: h.category
    })                  AS hazards,
    collect(DISTINCT o.name)                                          AS target_organs,
    collect(DISTINCT cc.class)                                        AS chemical_classes,
    collect(DISTINCT {type: t.name, value: t.value})                  AS toxicity,
    collect(DISTINCT {standard: e.standard, value: e.value,
                      unit: e.unit, type: e.type})                    AS exposure_limits,
    collect(DISTINCT sk.name)                                         AS skin_effects,
    collect(DISTINCT ey.name)                                         AS eye_effects,
    collect(DISTINCT ih.name)                                         AS inhalation_effects,
    collect(DISTINCT ig.name)                                         AS ingestion_effects,
    collect(DISTINCT ex.name)                                         AS excretion_routes
"""


# ============================================================
# HAZARDS ONLY — used by get_hazard_profile (fast check)
# ============================================================

GET_HAZARDS_LIST = """
MATCH (c:Chemical {uid: $uid})
MATCH (c)-[:HAS_HAZARD_STATEMENT]->(h:HazardStatement)
RETURN collect(DISTINCT {
    code: h.code, signal: h.signal,
    meaning: h.meaning, category: h.category
}) AS hazards
"""


# ============================================================
# ORGANS ONLY — used by combination server directly
# ============================================================

GET_ORGANS_LIST = """
MATCH (c:Chemical {uid: $uid})
MATCH (c)-[:AFFECTS_ORGAN]->(o:TargetOrgan)
RETURN collect(DISTINCT o.name) AS organs
"""


# ============================================================
# EXPOSURE LIMITS ONLY
# ============================================================

GET_EXPOSURE_LIMITS_LIST = """
MATCH (c:Chemical {uid: $uid})
MATCH (c)-[:SUBJECT_TO_EXPOSURE_LIMIT]->(e:ExposureLimit)
RETURN collect(DISTINCT {
    standard: e.standard, value: e.value,
    unit: e.unit, type: e.type
}) AS limits
"""


# ============================================================
# CRITICAL HAZARD CHECK — H-codes that mean carcinogen,
# mutagen, reprotoxic, or STOT severe
# ============================================================

HAS_CRITICAL_HAZARD = """
MATCH (c:Chemical {uid: $uid})
MATCH (c)-[:HAS_HAZARD_STATEMENT]->(h:HazardStatement)
WHERE h.code IN ['H340', 'H341', 'H350', 'H351',
                 'H360', 'H361', 'H362',
                 'H370', 'H372']
RETURN collect(DISTINCT h.code) AS critical_hazards
"""


# ============================================================
# ORGAN OVERLAP — used internally by combination server
# Fetches organs for multiple chemicals in one query
# ============================================================

GET_ORGAN_FOR_MULTIPLE_CHEMICALS = """
UNWIND $uids AS uid
MATCH (c:Chemical {uid: uid})
OPTIONAL MATCH (c)-[:AFFECTS_ORGAN]->(o:TargetOrgan)
RETURN uid                      AS chemical_uid,
       collect(DISTINCT o.name) AS organs
"""


# ============================================================
# TEST
# ============================================================

TEST_QUERY = """
MATCH (c:Chemical)
RETURN c.name, c.preferred_name, c.uid
LIMIT 5
"""