"""
agent/state.py
───────────────
Lightweight state container for the agent run.
Tracks what has been investigated so we never duplicate calls.
PHASE 5: Added confidence tracking.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentState:
    """Tracks agent progress during a single evaluation run."""

    # Chemicals already resolved — uid keyed by name
    resolved: dict = field(default_factory=dict)

    # Chemicals confirmed unresolved — set of names
    unresolved: set = field(default_factory=set)

    # Hazard profiles already fetched — keyed by uid
    hazard_profiles: dict = field(default_factory=dict)

    # Full profiles already fetched — keyed by uid
    full_profiles: dict = field(default_factory=dict)

    # Confidence scores per chemical
    confidence_scores: dict = field(default_factory=dict)

    # All findings accumulated
    findings: list = field(default_factory=list)

    # Errors encountered
    errors: list = field(default_factory=list)

    def mark_resolved(self, name: str, uid: str):
        self.resolved[name] = uid

    def mark_unresolved(self, name: str):
        self.unresolved.add(name)

    def set_confidence(self, name: str, confidence: float):
        self.confidence_scores[name] = confidence

    def get_confidence(self, name: str) -> float:
        return self.confidence_scores.get(name, 0.0)

    def is_investigated(self, name: str) -> bool:
        return name in self.resolved or name in self.unresolved

    def add_finding(self, finding: dict):
        self.findings.append(finding)

    def add_error(self, context: str, error: str):
        self.errors.append({"context": context, "error": error})

    def summary(self) -> dict:
        return {
            "resolved_count":   len(self.resolved),
            "unresolved_count": len(self.unresolved),
            "findings_count":   len(self.findings),
            "error_count":      len(self.errors),
        }