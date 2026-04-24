"""Neo4j Knowledge Graph response models - YOU BUILD"""

from dataclasses import dataclass
from typing import List, Optional

@dataclass
class ChemicalResolution:
    """Result of resolve_ingredient tool"""
    uid: Optional[str]
    name: str
    preferred_name: Optional[str]
    cas: Optional[str]
    
@dataclass
class HazardClassification:
    """Result of get_hazard_classification tool"""
    h_codes: List[str]
    signal: str  # "Danger", "Warning", or None
    meanings: dict  # H-code -> meaning

@dataclass
class TargetOrgans:
    """Result of get_target_organs tool"""
    organs: List[str]
    descriptions: dict

@dataclass
class ChemicalClass:
    """Result of get_chemical_class tool"""
    name: str
    category: Optional[str]