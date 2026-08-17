"""Deterministic validation rule catalog.

Each rule has a stable id and an independent version so findings can be tied to
the exact rule revision that produced them. ``RULESET_VERSION`` bumps when the
set or semantics of rules change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from baculus.models.enums import FindingSeverity

RULESET_VERSION = "1.0.0"


class RuleId(StrEnum):
    MALFORMED_RECORD = "MD001"
    NULL_REQUIRED_FIELD = "MD002"
    DUPLICATE_SYMBOL_DATE = "MD003"
    IMPOSSIBLE_OHLC = "MD004"
    NEGATIVE_VOLUME = "MD005"
    MISSING_TRADING_DATE = "MD006"
    DUPLICATE_SOURCE_OBSERVATION = "MD007"
    SCHEMA_MISMATCH = "MD008"


@dataclass(frozen=True, slots=True)
class Rule:
    id: RuleId
    version: str
    severity: FindingSeverity
    description: str


RULES: dict[RuleId, Rule] = {
    RuleId.MALFORMED_RECORD: Rule(
        RuleId.MALFORMED_RECORD,
        "1.0.0",
        FindingSeverity.ERROR,
        "Record could not be parsed into a canonical bar.",
    ),
    RuleId.NULL_REQUIRED_FIELD: Rule(
        RuleId.NULL_REQUIRED_FIELD,
        "1.0.0",
        FindingSeverity.ERROR,
        "A required field is null.",
    ),
    RuleId.DUPLICATE_SYMBOL_DATE: Rule(
        RuleId.DUPLICATE_SYMBOL_DATE,
        "1.0.0",
        FindingSeverity.ERROR,
        "More than one bar exists for the same symbol and event date.",
    ),
    RuleId.IMPOSSIBLE_OHLC: Rule(
        RuleId.IMPOSSIBLE_OHLC,
        "1.0.0",
        FindingSeverity.ERROR,
        "OHLC values violate high>=max(open,close)>=min(open,close)>=low with high>=low.",
    ),
    RuleId.NEGATIVE_VOLUME: Rule(
        RuleId.NEGATIVE_VOLUME,
        "1.0.0",
        FindingSeverity.ERROR,
        "Volume is negative.",
    ),
    RuleId.MISSING_TRADING_DATE: Rule(
        RuleId.MISSING_TRADING_DATE,
        "1.0.0",
        FindingSeverity.WARNING,
        "An expected trading session has no bar for a symbol.",
    ),
    RuleId.DUPLICATE_SOURCE_OBSERVATION: Rule(
        RuleId.DUPLICATE_SOURCE_OBSERVATION,
        "1.0.0",
        FindingSeverity.ERROR,
        "The same source reported the same symbol/date from the same artifact twice.",
    ),
    RuleId.SCHEMA_MISMATCH: Rule(
        RuleId.SCHEMA_MISMATCH,
        "1.0.0",
        FindingSeverity.ERROR,
        "The dataset frame does not match the canonical bar schema.",
    ),
}


__all__ = ["RULES", "RULESET_VERSION", "Rule", "RuleId"]
