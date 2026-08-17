"""M9 out-of-sample protection guard.

M9 itself is not implemented in M0. What *is* established now is the structural
guard: a dataset that has not passed M4 and is not ``SEALED`` must be incapable
of being treated as eligible for true out-of-sample M9 research.

Before an independent Source B / M4 pass exists, engineering pipeline exercises
may run on ``PROVISIONAL`` data, but any numbers they produce are disposable and
must never consume or expose the real M9 out-of-sample window. This guard is the
single choke point that enforces that intent in code.
"""

from __future__ import annotations

from baculus.models.enums import DatasetState


class M9EligibilityError(Exception):
    """Raised when non-SEALED data is used where M9 eligibility is required."""


def is_m9_eligible(state: DatasetState) -> bool:
    """Only SEALED data (which requires a passed independent M4) is M9-eligible."""
    return state is DatasetState.SEALED


def assert_m9_eligible(state: DatasetState, *, dataset_id: str = "") -> None:
    if not is_m9_eligible(state):
        subject = f" for dataset {dataset_id}" if dataset_id else ""
        raise M9EligibilityError(
            f"dataset state {state.value} is not M9-eligible{subject}: "
            "true out-of-sample M9 research requires SEALED (independent M4) data."
        )


__all__ = ["M9EligibilityError", "assert_m9_eligible", "is_m9_eligible"]
