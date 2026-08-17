"""Ingestion orchestration: fetch -> hash -> store -> vintage -> validate."""

from __future__ import annotations

from baculus.ingestion.runner import IngestionResult, IngestionRunner

__all__ = ["IngestionResult", "IngestionRunner"]
