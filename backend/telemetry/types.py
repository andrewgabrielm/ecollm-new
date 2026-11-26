"""Typed allowlist for telemetry records; no prompt or response content ever lands here."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr

from backend.api.schemas import CacheStatus, PrivacyTypeCount
from backend.contracts import ContractModel, ErrorCode, TaskCategory
from backend.versions import POLICY_VERSION

TELEMETRY_SCHEMA_VERSION: Literal["telemetry-v1"] = "telemetry-v1"

Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
Version = Annotated[StrictStr, Field(min_length=1, max_length=128)]
Positive = Annotated[StrictFloat, Field(ge=0.0)]
Count = Annotated[StrictInt, Field(ge=0)]


class RouteLabel(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"
    NONE = "none"


class TelemetryOutcome(StrEnum):
    SUCCESS = "success"
    LOCAL_FAILURE = "local_failure"
    CLOUD_FAILURE = "cloud_failure"
    POLICY_BLOCKED = "policy_blocked"
    INSPECTION_BLOCKED = "inspection_blocked"
    VALIDATION_FAILURE = "validation_failure"
    APPROVAL_DECLINED = "approval_declined"
    APPROVAL_EXPIRED = "approval_expired"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"


class TelemetryRecord(ContractModel):
    """One allowlisted record per completed chat turn.

    Only typed, bounded, non-content fields land in this contract. No raw
    prompt text, answer text, PII token value, secret, credential, request
    content or placeholder map may be stored — the recorder is responsible for
    ensuring all inputs come from already-sanitized and typed sources.
    """

    schema_version: Literal["telemetry-v1"] = TELEMETRY_SCHEMA_VERSION
    request_id: UUID
    created_at: Positive
    task: TaskCategory
    route: RouteLabel
    reason: StrictStr = Field(min_length=1, max_length=64)
    model_id: Identifier | None = None
    model_version: Version | None = None
    router_type: Literal["rules", "lightgbm"]
    router_version: Version
    outcome: TelemetryOutcome
    error_code: ErrorCode | None = None
    cache_status: CacheStatus = CacheStatus.DISABLED
    total_ms: Positive | None = None
    privacy_ms: Positive | None = None
    routing_ms: Positive | None = None
    generation_ms: Positive | None = None
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    pii_type_counts: tuple[PrivacyTypeCount, ...] = ()
    mock: StrictBool = False
    policy_version: Version = POLICY_VERSION


class PerCategoryLatency(ContractModel):
    task: TaskCategory
    sample_count: Count
    median_ms: Positive | None
    p90_ms: Positive | None


class ModelUsage(ContractModel):
    model_id: Identifier
    model_version: Version
    count: Count


class CacheHitRate(ContractModel):
    total_records: Count
    cache_hits: Count
    hit_rate: Positive | None


class TelemetrySummary(ContractModel):
    """Aggregate numbers suitable for a lightweight dashboard.

    Statistics are *suppressed* (returned as ``None``) when the sample count is
    below a stability threshold so sparse local data cannot mislead the viewer.
    """

    schema_version: Literal["telemetry-summary-v1"] = "telemetry-summary-v1"
    sample_count: Count
    suppression_threshold: Count = 30
    enabled: StrictBool
    route_counts: dict[StrictStr, Count]
    outcome_counts: dict[StrictStr, Count]
    router_type_counts: dict[StrictStr, Count]
    reason_counts: dict[StrictStr, Count]
    model_usage: tuple[ModelUsage, ...]
    latency_overall: PerCategoryLatency | None
    latency_per_task: tuple[PerCategoryLatency, ...]
    cache: CacheHitRate
    retention_days: StrictInt = Field(ge=1)
