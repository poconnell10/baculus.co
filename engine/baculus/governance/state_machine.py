"""Dataset lifecycle state machine and the SEALED guard.

Permitted transitions are explicit. The single most important rule is enforced
here in code, not merely documented:

    A dataset may only reach ``SEALED`` if an *independent* reconciliation
    (M4) exists and has passed. Massive-only data can never satisfy this, so a
    single-source dataset can never be sealed. There is deliberately no bypass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from baculus.models.enums import DatasetState

# Allowed transitions between dataset states.
ALLOWED_TRANSITIONS: dict[DatasetState, frozenset[DatasetState]] = {
    DatasetState.RAW: frozenset({DatasetState.VALIDATED, DatasetState.QUARANTINED}),
    DatasetState.VALIDATED: frozenset({DatasetState.PROVISIONAL, DatasetState.QUARANTINED}),
    DatasetState.PROVISIONAL: frozenset(
        {DatasetState.SEALED, DatasetState.QUARANTINED, DatasetState.SUPERSEDED}
    ),
    DatasetState.QUARANTINED: frozenset({DatasetState.VALIDATED, DatasetState.SUPERSEDED}),
    DatasetState.SEALED: frozenset({DatasetState.SUPERSEDED}),
    DatasetState.SUPERSEDED: frozenset(),
}


class StateTransitionError(Exception):
    """Raised for a transition that is not permitted by the state machine."""


class SealGuardError(StateTransitionError):
    """Raised when a SEALED transition lacks an independent M4 reconciliation."""


@dataclass(frozen=True, slots=True)
class ReconciliationEvidence:
    """Evidence that an independent (M4) reconciliation has been performed.

    ``sources`` is the set of distinct data sources that were cross-checked.
    Independence requires at least one source *other than* the dataset's own
    source. For an M0 Massive-only dataset, no such evidence can exist, so
    SEALED is unreachable.
    """

    dataset_source: str
    sources: frozenset[str]
    passed: bool
    reconciliation_id: str = ""
    notes: str = ""
    _explicit: bool = field(default=True, repr=False)

    @property
    def is_independent(self) -> bool:
        others = {s for s in self.sources if s != self.dataset_source}
        return len(others) >= 1

    def is_valid_for_seal(self) -> bool:
        return self.passed and self.is_independent


def assert_transition_allowed(
    current: DatasetState,
    target: DatasetState,
    *,
    reconciliation: ReconciliationEvidence | None = None,
) -> None:
    """Validate a state transition, enforcing the SEALED guard.

    Raises ``StateTransitionError`` for a disallowed edge, or ``SealGuardError``
    when a SEALED transition is attempted without valid independent M4 evidence.
    """
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise StateTransitionError(f"transition {current.value} -> {target.value} is not permitted")

    if target is DatasetState.SEALED:
        if reconciliation is None:
            raise SealGuardError(
                "PROVISIONAL -> SEALED requires an independent M4 reconciliation; "
                "none was provided. Single-source (Massive-only) data cannot be sealed."
            )
        if not reconciliation.is_independent:
            raise SealGuardError(
                "SEALED blocked: reconciliation is not independent (no source other "
                f"than '{reconciliation.dataset_source}'). Massive-only data cannot be sealed."
            )
        if not reconciliation.passed:
            raise SealGuardError("SEALED blocked: independent reconciliation did not pass.")


__all__ = [
    "ALLOWED_TRANSITIONS",
    "ReconciliationEvidence",
    "SealGuardError",
    "StateTransitionError",
    "assert_transition_allowed",
]
