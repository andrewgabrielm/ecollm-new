"""Consent-gated recorder that maps a ChatResponse/ErrorResponse to a TelemetryRecord."""

from __future__ import annotations

from time import time

from backend.api.schemas import CacheStatus, ChatResponse, ErrorResponse
from backend.contracts import ErrorCode, TaskCategory
from backend.telemetry.store import TelemetryStore
from backend.telemetry.types import RouteLabel, TelemetryOutcome, TelemetryRecord


class TelemetryRecorder:
    """Writes one typed record per chat turn when consent is enabled."""

    def __init__(self, store: TelemetryStore | None, *, enabled: bool) -> None:
        self._store = store
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled and self._store is not None

    def record_chat_response(self, response: ChatResponse) -> None:
        if not self.enabled or self._store is None:
            return
        record = TelemetryRecord(
            request_id=response.request_id,
            created_at=time(),
            task=response.task if response.task is not None else TaskCategory.QA,
            route=RouteLabel(response.route),
            reason=response.reason,
            model_id=response.model_id,
            model_version=response.model_version,
            router_type=response.router_type,
            router_version=response.router_version or "unknown",
            outcome=TelemetryOutcome.SUCCESS,
            cache_status=response.cache_status,
            total_ms=response.timings.total_ms,
            privacy_ms=response.timings.privacy_ms,
            routing_ms=response.timings.routing_ms,
            generation_ms=response.timings.generation_ms,
            input_tokens=(
                response.generation_metrics.input_tokens
                if response.generation_metrics is not None
                else None
            ),
            output_tokens=(
                response.generation_metrics.output_tokens
                if response.generation_metrics is not None
                else None
            ),
            pii_type_counts=response.privacy_counts,
            mock=response.mock,
        )
        self._store.insert(record)

    def record_error_response(
        self,
        response: ErrorResponse,
        *,
        task: TaskCategory | None,
        router_type: str,
        router_version: str,
    ) -> None:
        if not self.enabled or self._store is None:
            return
        outcome = _error_to_outcome(response.error_code)
        if router_type not in ("rules", "lightgbm"):
            raise ValueError("router_type must be rules or lightgbm")
        record = TelemetryRecord(
            request_id=response.request_id,
            created_at=time(),
            task=task if task is not None else TaskCategory.QA,
            route=RouteLabel.NONE,
            reason=response.error_code.value,
            model_id=None,
            model_version=None,
            router_type="lightgbm" if router_type == "lightgbm" else "rules",
            router_version=router_version,
            outcome=outcome,
            error_code=response.error_code,
            cache_status=CacheStatus.DISABLED,
            pii_type_counts=(),
            mock=False,
        )
        self._store.insert(record)


def _error_to_outcome(code: ErrorCode) -> TelemetryOutcome:
    mapping = {
        ErrorCode.POLICY_BLOCKED: TelemetryOutcome.POLICY_BLOCKED,
        ErrorCode.APPROVAL_CONFLICT: TelemetryOutcome.POLICY_BLOCKED,
        ErrorCode.OUTPUT_BLOCKED: TelemetryOutcome.INSPECTION_BLOCKED,
        ErrorCode.VALIDATION_FAILED: TelemetryOutcome.VALIDATION_FAILURE,
        ErrorCode.UNAVAILABLE: TelemetryOutcome.UNAVAILABLE,
        ErrorCode.TIMEOUT: TelemetryOutcome.LOCAL_FAILURE,
        ErrorCode.PRIVACY_FAILED: TelemetryOutcome.INSPECTION_BLOCKED,
    }
    return mapping.get(code, TelemetryOutcome.INTERNAL)
