"""The deterministic validator.

Runs the M0 integrity rules over a canonical frame plus the expected trading
sessions (from the authoritative calendar). Every check is deterministic and
order-stable, so the same input always produces the same findings.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from baculus.models.bar import REQUIRED_BAR_FIELDS
from baculus.normalization.canonical import frame_schema_matches
from baculus.reference.calendar import TradingCalendar
from baculus.validation.findings import ValidationFinding, ValidationReport
from baculus.validation.rules import RULES, RuleId


def _finding(rule_id: RuleId, reason: str, **ctx: object) -> ValidationFinding:
    rule = RULES[rule_id]
    return ValidationFinding(
        rule_id=rule.id,
        rule_version=rule.version,
        severity=rule.severity,
        reason=reason,
        source=ctx.get("source"),  # type: ignore[arg-type]
        artifact_id=ctx.get("artifact_id"),  # type: ignore[arg-type]
        symbol=ctx.get("symbol"),  # type: ignore[arg-type]
        event_date=ctx.get("event_date"),  # type: ignore[arg-type]
        observed_value=ctx.get("observed_value"),  # type: ignore[arg-type]
    )


class Validator:
    def __init__(self, calendar: TradingCalendar | None = None) -> None:
        self._calendar = calendar

    def validate(
        self,
        df: pl.DataFrame,
        *,
        source: str,
        artifact_id: str,
        symbols: tuple[str, ...],
        start: date,
        end: date,
        expected_sessions: list[date] | None = None,
    ) -> ValidationReport:
        findings: list[ValidationFinding] = []

        # MD008 — schema mismatch. If the schema is wrong, downstream column
        # access is unreliable; record and return early.
        if not frame_schema_matches(df):
            findings.append(
                _finding(
                    RuleId.SCHEMA_MISMATCH,
                    reason="frame schema does not match canonical bar schema",
                    source=source,
                    artifact_id=artifact_id,
                    observed_value=str(dict(df.schema)),
                )
            )
            return ValidationReport(findings=findings)

        findings.extend(self._check_nulls(df, source, artifact_id))
        findings.extend(self._check_duplicate_symbol_date(df, source, artifact_id))
        findings.extend(self._check_ohlc(df, source, artifact_id))
        findings.extend(self._check_negative_volume(df, source, artifact_id))
        findings.extend(self._check_duplicate_source_observation(df))
        findings.extend(
            self._check_missing_trading_dates(
                df, symbols, start, end, source, artifact_id, expected_sessions
            )
        )
        return ValidationReport(findings=findings)

    # -- individual rules ----------------------------------------------------
    def _check_nulls(
        self, df: pl.DataFrame, source: str, artifact_id: str
    ) -> list[ValidationFinding]:
        out: list[ValidationFinding] = []
        for col in REQUIRED_BAR_FIELDS:
            offending = df.filter(pl.col(col).is_null())
            for row in offending.iter_rows(named=True):
                out.append(
                    _finding(
                        RuleId.NULL_REQUIRED_FIELD,
                        reason=f"required field '{col}' is null",
                        source=source,
                        artifact_id=artifact_id,
                        symbol=row.get("symbol"),
                        event_date=row.get("event_date"),
                        observed_value=col,
                    )
                )
        return out

    def _check_duplicate_symbol_date(
        self, df: pl.DataFrame, source: str, artifact_id: str
    ) -> list[ValidationFinding]:
        dupes = (
            df.group_by(["symbol", "event_date"])
            .agg(pl.len().alias("n"))
            .filter(pl.col("n") > 1)
            .sort(["symbol", "event_date"])
        )
        out: list[ValidationFinding] = []
        for row in dupes.iter_rows(named=True):
            out.append(
                _finding(
                    RuleId.DUPLICATE_SYMBOL_DATE,
                    reason=f"{row['n']} bars for the same symbol/date",
                    source=source,
                    artifact_id=artifact_id,
                    symbol=row["symbol"],
                    event_date=row["event_date"],
                    observed_value=f"count={row['n']}",
                )
            )
        return out

    def _check_ohlc(
        self, df: pl.DataFrame, source: str, artifact_id: str
    ) -> list[ValidationFinding]:
        # Valid iff high >= max(open, close) and low <= min(open, close) and high >= low.
        hi, lo, op, cl = (pl.col("high"), pl.col("low"), pl.col("open"), pl.col("close"))
        valid = (hi >= op) & (hi >= cl) & (lo <= op) & (lo <= cl) & (hi >= lo)
        bad = df.filter(~valid).sort(["symbol", "event_date"])
        out: list[ValidationFinding] = []
        for row in bad.iter_rows(named=True):
            out.append(
                _finding(
                    RuleId.IMPOSSIBLE_OHLC,
                    reason="impossible OHLC relationship",
                    source=source,
                    artifact_id=artifact_id,
                    symbol=row["symbol"],
                    event_date=row["event_date"],
                    observed_value=(
                        f"o={row['open']},h={row['high']},l={row['low']},c={row['close']}"
                    ),
                )
            )
        return out

    def _check_negative_volume(
        self, df: pl.DataFrame, source: str, artifact_id: str
    ) -> list[ValidationFinding]:
        bad = df.filter(pl.col("volume") < 0).sort(["symbol", "event_date"])
        out: list[ValidationFinding] = []
        for row in bad.iter_rows(named=True):
            out.append(
                _finding(
                    RuleId.NEGATIVE_VOLUME,
                    reason="negative volume",
                    source=source,
                    artifact_id=artifact_id,
                    symbol=row["symbol"],
                    event_date=row["event_date"],
                    observed_value=f"volume={row['volume']}",
                )
            )
        return out

    def _check_duplicate_source_observation(self, df: pl.DataFrame) -> list[ValidationFinding]:
        dupes = (
            df.group_by(["source", "symbol", "event_date", "source_artifact_id"])
            .agg(pl.len().alias("n"))
            .filter(pl.col("n") > 1)
            .sort(["source", "symbol", "event_date"])
        )
        out: list[ValidationFinding] = []
        for row in dupes.iter_rows(named=True):
            out.append(
                _finding(
                    RuleId.DUPLICATE_SOURCE_OBSERVATION,
                    reason="same source/artifact reported the same symbol/date more than once",
                    source=row["source"],
                    artifact_id=row["source_artifact_id"],
                    symbol=row["symbol"],
                    event_date=row["event_date"],
                    observed_value=f"count={row['n']}",
                )
            )
        return out

    def _check_missing_trading_dates(
        self,
        df: pl.DataFrame,
        symbols: tuple[str, ...],
        start: date,
        end: date,
        source: str,
        artifact_id: str,
        expected_sessions: list[date] | None,
    ) -> list[ValidationFinding]:
        if expected_sessions is None:
            if self._calendar is None:
                # No authority available: cannot assert missing sessions.
                return []
            expected_sessions = self._calendar.sessions_in_range(start, end)
        expected = set(expected_sessions)

        out: list[ValidationFinding] = []
        for symbol in sorted(symbols):
            present = set(df.filter(pl.col("symbol") == symbol).get_column("event_date").to_list())
            missing = sorted(expected - present)
            for day in missing:
                out.append(
                    _finding(
                        RuleId.MISSING_TRADING_DATE,
                        reason="expected trading session has no bar",
                        source=source,
                        artifact_id=artifact_id,
                        symbol=symbol,
                        event_date=day,
                        observed_value="missing",
                    )
                )
        return out


__all__ = ["Validator"]
