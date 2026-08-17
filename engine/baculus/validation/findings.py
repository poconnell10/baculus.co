"""Validation finding and report structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from baculus.models.enums import FindingSeverity
from baculus.validation.rules import RULESET_VERSION, RuleId


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """A single deterministic validation finding.

    Carries everything needed to reproduce and audit the finding: the rule id
    and version, severity, the affected source/artifact, the affected
    symbol/date where relevant, the observed value/context, and a deterministic
    human-readable reason.
    """

    rule_id: RuleId
    rule_version: str
    severity: FindingSeverity
    reason: str
    source: str | None = None
    artifact_id: str | None = None
    symbol: str | None = None
    event_date: date | None = None
    observed_value: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "rule_id": self.rule_id.value,
            "rule_version": self.rule_version,
            "severity": self.severity.value,
            "reason": self.reason,
            "source": self.source,
            "artifact_id": self.artifact_id,
            "symbol": self.symbol,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "observed_value": self.observed_value,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    ruleset_version: str = RULESET_VERSION
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is FindingSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is FindingSeverity.WARNING)

    @property
    def passed(self) -> bool:
        """A report passes when it has no ERROR-severity findings."""
        return self.error_count == 0

    def by_rule(self, rule_id: RuleId) -> list[ValidationFinding]:
        return [f for f in self.findings if f.rule_id is rule_id]

    def summary(self) -> dict[str, object]:
        return {
            "ruleset_version": self.ruleset_version,
            "passed": self.passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "total_findings": len(self.findings),
        }


__all__ = ["ValidationFinding", "ValidationReport"]
