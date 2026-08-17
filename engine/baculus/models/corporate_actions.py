"""Corporate-actions domain models.

Corporate actions are a *separate domain* from price bars. Baculus deliberately
does NOT collapse everything into an opaque adjusted-close series. Instead we
store and version the primitives independently:

  * raw / unadjusted price bars  (see :mod:`baculus.models.bar`)
  * splits
  * dividends
  * ticker / security reference changes  (see :mod:`baculus.reference`)

Adjusted prices are a *derived* view, reconstructable from unadjusted bars plus
the corporate-action primitives. For M0 we only establish the domain boundaries;
the full adjustment engine is deferred.

Important limitation (documented, enforced by process at M4):
  Internal reconstruction can validate the *mechanics* of adjustment, but it
  cannot independently verify the upstream market prints themselves. That
  independent verification is an M4 responsibility. Nothing here may be used to
  claim upstream price correctness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"
    TICKER_CHANGE = "TICKER_CHANGE"


@dataclass(frozen=True, slots=True)
class Split:
    """A stock split / reverse split.

    ``ratio`` is new-shares-per-old-share (e.g. 4.0 for a 4:1 split,
    0.1 for a 1:10 reverse split).
    """

    symbol: str
    ex_date: date
    ratio: float
    source: str
    observation_time: datetime
    source_artifact_id: str


@dataclass(frozen=True, slots=True)
class Dividend:
    """A cash dividend, in the instrument's currency, per share."""

    symbol: str
    ex_date: date
    cash_amount: float
    currency: str
    source: str
    observation_time: datetime
    source_artifact_id: str


@dataclass(frozen=True, slots=True)
class TickerChange:
    """A ticker / security-reference change.

    Reference identity is not a price attribute; it belongs to the reference
    domain and is versioned there. This record links the change to the vendor
    artifact that reported it.
    """

    old_symbol: str
    new_symbol: str
    effective_date: date
    source: str
    observation_time: datetime
    source_artifact_id: str


__all__ = [
    "CorporateActionType",
    "Dividend",
    "Split",
    "TickerChange",
]
