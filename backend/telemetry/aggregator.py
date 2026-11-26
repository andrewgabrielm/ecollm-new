"""Suppressed-at-threshold aggregation over :class:`TelemetryRecord` rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

from backend.api.schemas import CacheStatus
from backend.contracts import TaskCategory
from backend.telemetry.types import (
    CacheHitRate,
    ModelUsage,
    PerCategoryLatency,
    TelemetryRecord,
    TelemetrySummary,
)

DEFAULT_SUPPRESSION = 30


def aggregate(
    records: Iterable[TelemetryRecord],
    *,
    enabled: bool,
    suppression_threshold: int = DEFAULT_SUPPRESSION,
    retention_days: int = 30,
) -> TelemetrySummary:
    rows = tuple(records)
    route_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    router_type_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    model_counts: Counter[tuple[str, str]] = Counter()
    latencies_overall: list[float] = []
    latencies_per_task: dict[TaskCategory, list[float]] = defaultdict(list)
    cache_hits = 0
    for record in rows:
        route_counts[record.route.value] += 1
        outcome_counts[record.outcome.value] += 1
        router_type_counts[record.router_type] += 1
        reason_counts[record.reason] += 1
        if record.model_id is not None and record.model_version is not None:
            model_counts[(record.model_id, record.model_version)] += 1
        if record.total_ms is not None:
            latencies_overall.append(float(record.total_ms))
            latencies_per_task[record.task].append(float(record.total_ms))
        if record.cache_status in {CacheStatus.EXACT_HIT, CacheStatus.SEMANTIC_HIT}:
            cache_hits += 1
    per_task = tuple(
        _per_task(task, samples, suppression_threshold)
        for task, samples in sorted(latencies_per_task.items(), key=lambda item: item[0].value)
    )
    overall = _summary_latency(latencies_overall, suppression_threshold) if rows else None
    cache_rate = _cache_rate(len(rows), cache_hits, suppression_threshold)
    return TelemetrySummary(
        sample_count=len(rows),
        suppression_threshold=suppression_threshold,
        enabled=enabled,
        route_counts=dict(route_counts),
        outcome_counts=dict(outcome_counts),
        router_type_counts=dict(router_type_counts),
        reason_counts=dict(reason_counts),
        model_usage=tuple(
            ModelUsage(model_id=mid, model_version=ver, count=count)
            for (mid, ver), count in sorted(
                model_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ),
        latency_overall=overall,
        latency_per_task=per_task,
        cache=cache_rate,
        retention_days=retention_days,
    )


def _summary_latency(
    samples: Sequence[float], suppression_threshold: int
) -> PerCategoryLatency | None:
    if not samples:
        return None
    sample_count = len(samples)
    if sample_count < suppression_threshold:
        return PerCategoryLatency(
            task=TaskCategory.QA,  # placeholder; caller uses this only when len > 0
            sample_count=sample_count,
            median_ms=None,
            p90_ms=None,
        )
    ordered = sorted(samples)
    return PerCategoryLatency(
        task=TaskCategory.QA,
        sample_count=sample_count,
        median_ms=_percentile(ordered, 0.5),
        p90_ms=_percentile(ordered, 0.9),
    )


def _per_task(
    task: TaskCategory,
    samples: Sequence[float],
    suppression_threshold: int,
) -> PerCategoryLatency:
    if len(samples) < suppression_threshold:
        return PerCategoryLatency(
            task=task,
            sample_count=len(samples),
            median_ms=None,
            p90_ms=None,
        )
    ordered = sorted(samples)
    return PerCategoryLatency(
        task=task,
        sample_count=len(samples),
        median_ms=_percentile(ordered, 0.5),
        p90_ms=_percentile(ordered, 0.9),
    )


def _cache_rate(total: int, hits: int, suppression_threshold: int) -> CacheHitRate:
    if total < suppression_threshold:
        return CacheHitRate(total_records=total, cache_hits=hits, hit_rate=None)
    return CacheHitRate(total_records=total, cache_hits=hits, hit_rate=hits / total)


def _percentile(ordered: Sequence[float], q: float) -> float:
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(len(ordered) * q))
    return float(ordered[index])
