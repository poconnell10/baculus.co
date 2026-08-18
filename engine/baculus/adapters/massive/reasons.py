"""Massive vendor/transport/identity reason codes.

These name failures at the VENDOR/ADAPTER layer — auth, transport, identity, and
response-integrity problems that the deterministic canonical-frame validator
(rule ids MD00x) cannot express because they require vendor/request context or
must be caught before canonical construction.

They complement, and do not replace, the existing M0 canonical-frame taxonomy:
data-integrity checks on a built frame remain MD003/MD004/MD005/... The adapter
fails closed with a ``MASSIVE_*`` reason before a bad vendor response ever
becomes canonical data; the runner then quarantines with that exact reason.
"""

from __future__ import annotations

from enum import StrEnum


class MassiveReason(StrEnum):
    # transport / auth
    MASSIVE_AUTH_FAILED = "MASSIVE_AUTH_FAILED"
    MASSIVE_RATE_LIMITED = "MASSIVE_RATE_LIMITED"
    MASSIVE_HTTP_ERROR = "MASSIVE_HTTP_ERROR"
    MASSIVE_TIMEOUT = "MASSIVE_TIMEOUT"
    MASSIVE_CONNECTION_FAILED = "MASSIVE_CONNECTION_FAILED"
    # payload structure
    MASSIVE_MALFORMED_JSON = "MASSIVE_MALFORMED_JSON"
    MASSIVE_UNEXPECTED_STATUS = "MASSIVE_UNEXPECTED_STATUS"
    # identity / integrity (pre-canonical)
    MASSIVE_TICKER_MISMATCH = "MASSIVE_TICKER_MISMATCH"
    MASSIVE_TIMESTAMP_INVALID = "MASSIVE_TIMESTAMP_INVALID"
    MASSIVE_BUSINESS_DATE_DUPLICATE = "MASSIVE_BUSINESS_DATE_DUPLICATE"
    MASSIVE_OHLC_INVALID = "MASSIVE_OHLC_INVALID"
    MASSIVE_VOLUME_INVALID = "MASSIVE_VOLUME_INVALID"
    MASSIVE_NUMERIC_NONFINITE = "MASSIVE_NUMERIC_NONFINITE"
    MASSIVE_OUTSIDE_REQUEST_RANGE = "MASSIVE_OUTSIDE_REQUEST_RANGE"


class MassiveError(Exception):
    """A Massive vendor/adapter failure carrying a machine-readable reason.

    ``retryable`` marks transport conditions the HTTP client may retry within a
    bounded budget (never auth or deterministic-validation failures).
    """

    def __init__(
        self,
        reason: MassiveReason,
        message: str,
        *,
        retryable: bool = False,
        http_status: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.retryable = retryable
        self.http_status = http_status
        self.request_id = request_id
        super().__init__(f"{reason.value}: {message}")


__all__ = ["MassiveError", "MassiveReason"]
