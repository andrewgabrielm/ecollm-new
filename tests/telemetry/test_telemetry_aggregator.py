"""Step 15 aggregator tests — suppression below threshold, percentile math."""

from __future__ import annotations

from uuid import uuid4

from backend.api.schemas import CacheStatus
from backend.contracts import TaskCategory
from backend.telemetry import TelemetryRecord, aggregate
from backend.telemetry.types import RouteLabel, TelemetryOutcome
from backend.versions import POLICY_VERSION


def _record(**overrides: object) -> TelemetryRecord:
    defaults: dict[str, object] = dict(
        request_id=uuid4(),
        created_at=1.0,
        task=TaskCategory.QA,
        route=RouteLabel.LOCAL,
        reason="rules_ranked",
        model_id="mock-general",
        model_version="v1",
        router_type="rules",
        router_version="rules-v1",
        outcome=TelemetryOutcome.SUCCESS,
        cache_status=CacheStatus.MISS,
        total_ms=10.0,
        privacy_ms=0.5,
        routing_ms=0.5,
        generation_ms=8.0,
        input_tokens=20,
        output_tokens=5,
        pii_type_counts=(),
        mock=True,
        policy_version=POLICY_VERSION,
    )
    defaults.update(overrides)
    return TelemetryRecord.model_validate(defaults)


class TestAggregator:
    def test_below_threshold_suppresses_latency(self) -> None:
        records = [_record(total_ms=float(idx)) for idx in range(5)]
        summary = aggregate(records, enabled=True, suppression_threshold=30)
        assert summary.sample_count == 5
        assert summary.latency_overall is not None
        assert summary.latency_overall.median_ms is None
        assert summary.latency_overall.p90_ms is None
        assert summary.cache.hit_rate is None

    def test_at_threshold_reports_percentiles(self) -> None:
        records = [_record(total_ms=float(idx + 1)) for idx in range(30)]
        summary = aggregate(records, enabled=True, suppression_threshold=30)
        assert summary.latency_overall is not None
        assert summary.latency_overall.median_ms is not None
        assert 14.0 <= summary.latency_overall.median_ms <= 16.0
        assert summary.latency_overall.p90_ms is not None
        assert summary.latency_overall.p90_ms >= summary.latency_overall.median_ms

    def test_cache_hit_rate_counted(self) -> None:
        records = [
            _record(cache_status=CacheStatus.EXACT_HIT if idx % 3 == 0 else CacheStatus.MISS)
            for idx in range(30)
        ]
        summary = aggregate(records, enabled=True, suppression_threshold=30)
        assert summary.cache.total_records == 30
        assert summary.cache.cache_hits == 10
        assert summary.cache.hit_rate is not None
        assert abs(summary.cache.hit_rate - 10 / 30) < 1e-9

    def test_per_task_breakdown(self) -> None:
        records = []
        for idx in range(30):
            records.append(_record(task=TaskCategory.QA, total_ms=float(idx + 1)))
            records.append(_record(task=TaskCategory.CODE, total_ms=float(1000 + idx)))
        summary = aggregate(records, enabled=True, suppression_threshold=30)
        per_task = {row.task.value: row for row in summary.latency_per_task}
        assert "qa" in per_task and per_task["qa"].median_ms is not None
        assert "code" in per_task and per_task["code"].median_ms is not None
        assert per_task["code"].median_ms > per_task["qa"].median_ms

    def test_disabled_flag_passes_through(self) -> None:
        summary = aggregate([], enabled=False)
        assert summary.enabled is False
        assert summary.sample_count == 0
