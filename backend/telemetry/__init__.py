"""Opt-in local telemetry: typed records, bounded retention, suppressed aggregation."""

from backend.telemetry.aggregator import DEFAULT_SUPPRESSION, aggregate
from backend.telemetry.recorder import TelemetryRecorder
from backend.telemetry.store import TelemetryStore
from backend.telemetry.types import (
    TELEMETRY_SCHEMA_VERSION,
    CacheHitRate,
    ModelUsage,
    PerCategoryLatency,
    RouteLabel,
    TelemetryOutcome,
    TelemetryRecord,
    TelemetrySummary,
)

__all__ = [
    "DEFAULT_SUPPRESSION",
    "TELEMETRY_SCHEMA_VERSION",
    "CacheHitRate",
    "ModelUsage",
    "PerCategoryLatency",
    "RouteLabel",
    "TelemetryOutcome",
    "TelemetryRecord",
    "TelemetryRecorder",
    "TelemetryStore",
    "TelemetrySummary",
    "aggregate",
]
