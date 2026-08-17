"""Authoritative trading-calendar boundary.

Baculus never hard-codes "weekday minus holidays". The set of valid trading
sessions is authoritative *reference data*, sourced from a pinned, versioned
library (``exchange_calendars``) for the ``XNYS`` (NYSE) calendar. The calendar
provider, library version and calendar name are recorded so every dataset
manifest can pin the exact calendar vintage it was validated against.

Two distinct notions are kept separate:
  * ``expected_bar_date`` — the trading *session* a bar belongs to (event time);
  * ``expected_availability_time`` — the wall-clock time a bar is expected to be
    *available* from a source. The latter is source/SLA-specific and is used for
    staleness reasoning, not for deciding whether a session should exist.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version as pkg_version

import exchange_calendars as xcals


class TradingCalendar:
    """Thin, deterministic wrapper over a pinned exchange calendar."""

    def __init__(self, name: str = "XNYS") -> None:
        self.name = name
        self.provider = "exchange_calendars"
        self.version = pkg_version("exchange-calendars")
        self._cal = xcals.get_calendar(name)

    # -- session queries -----------------------------------------------------
    def sessions_in_range(self, start: date, end: date) -> list[date]:
        """Inclusive list of trading sessions between ``start`` and ``end``."""
        idx = self._cal.sessions_in_range(start.isoformat(), end.isoformat())
        return [ts.date() for ts in idx]

    def is_session(self, day: date) -> bool:
        """Whether ``day`` is a trading session on this calendar."""
        return len(self.sessions_in_range(day, day)) == 1

    # -- staleness architecture ---------------------------------------------
    def expected_availability_time(self, session: date, sla: timedelta) -> datetime:
        """When a source is expected to have delivered ``session``'s bar.

        Computed from the session close plus a source-specific ``sla`` offset.
        Returns a timezone-aware UTC datetime.
        """
        if not self.is_session(session):
            raise ValueError(f"{session} is not a trading session on {self.name}")
        close = self._cal.session_close(session.isoformat())
        close_utc = close.to_pydatetime().astimezone(UTC)
        return close_utc + sla

    # -- reference-data descriptor ------------------------------------------
    def reference_descriptor(self) -> dict[str, str]:
        """Pinnable descriptor recorded in dataset manifests."""
        return {
            "calendar_name": self.name,
            "calendar_provider": self.provider,
            "calendar_library_version": self.version,
            # For M0 the vintage is the library version; a future milestone may
            # snapshot the actual holiday table as an independent vintage.
            "calendar_vintage": self.version,
        }


__all__ = ["TradingCalendar"]
