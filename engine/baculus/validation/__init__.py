"""Deterministic market-data integrity validation for M0.

Only deterministic, objective checks live here. No subjective anomaly detection
and no strategy-specific validation — those are out of scope for M0.
"""

from __future__ import annotations

from baculus.validation.findings import ValidationFinding, ValidationReport
from baculus.validation.rules import RULES, RULESET_VERSION, RuleId
from baculus.validation.validator import Validator

__all__ = [
    "RULES",
    "RULESET_VERSION",
    "RuleId",
    "ValidationFinding",
    "ValidationReport",
    "Validator",
]
