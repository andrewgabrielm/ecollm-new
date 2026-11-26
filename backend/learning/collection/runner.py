"""Drive a :class:`CollectionPlan` through the gated evaluation dispatch.

The runner is the one place that composes the evaluation driver, the scorer
dispatch and the observation store. It is deliberately resumable: every
successful write is committed before the next entry begins, so an interrupted
run restarts from the planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any

from backend.learning.benchmarks.types import BenchmarkTask
from backend.learning.collection.planner import CollectionPlan, PlanEntry
from backend.learning.collection.store import ObservationStore
from backend.learning.collection.types import Observation, ObservationStatus
from backend.learning.scorers import ScoreResult, score
from backend.versions import POLICY_VERSION


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    produced: int
    failed: int
    by_status: dict[ObservationStatus, int]


class CollectionRunner:
    """Composes an evaluator (``EvaluationDriver`` or a test double) with scoring
    and persistence. The evaluator type is intentionally loose so a deterministic
    stand-in can be substituted without constructing the broker stack."""

    def __init__(self, evaluator: Any, store: ObservationStore) -> None:
        self._evaluator = evaluator
        self._store = store

    async def run(self, plan: CollectionPlan) -> RunSummary:
        produced = 0
        failed = 0
        totals: dict[ObservationStatus, int] = {status: 0 for status in ObservationStatus}
        for entry in plan.entries:
            observation = await self._run_entry(plan.run_id, entry)
            self._store.insert(observation)
            totals[observation.status] += 1
            if observation.status == ObservationStatus.SUCCESS:
                produced += 1
            else:
                failed += 1
        return RunSummary(
            run_id=plan.run_id,
            produced=produced,
            failed=failed,
            by_status=totals,
        )

    async def _run_entry(self, run_id: str, entry: PlanEntry) -> Observation:
        result = await self._evaluator.evaluate(
            entry.task.prompt,
            entry.candidate.model_id,
            task=entry.task.category,
        )
        return _observation_from_result(run_id, entry, result)


def _observation_from_result(run_id: str, entry: PlanEntry, result: object) -> Observation:
    # Typed access without pulling the EvaluationResult class in at module scope —
    # keeps this file importable without the broker dispatcher stack.
    status_value = _get_attr(result, "status")
    if not isinstance(status_value, ObservationStatus):
        raise TypeError("evaluation result status must be an ObservationStatus")
    status: ObservationStatus = status_value
    quality: float | None = None
    format_valid: bool | None = None
    if status == ObservationStatus.SUCCESS:
        scored = _score_with_fallback(entry.task, _get_attr(result, "answer"))
        if scored is not None:
            quality = scored.quality
            format_valid = scored.format_valid
    payload: dict[str, object] = {
        "run_id": run_id,
        "task_id": entry.task.task_id,
        "model_id": _get_attr(result, "model_id"),
        "model_version": _get_attr(result, "model_version"),
        "split": entry.split,
        "task_category": entry.task.category,
        "feature_hash": _get_attr(result, "feature_hash"),
        "features": _get_attr(result, "features"),
        "status": status,
        "error_code": _get_attr(result, "error_code"),
        "quality": quality,
        "format_valid": format_valid,
        "input_tokens": _get_attr(result, "input_tokens"),
        "output_tokens": _get_attr(result, "output_tokens"),
        "total_ms": _get_attr(result, "total_ms"),
        "privacy_ms": _get_attr(result, "privacy_ms"),
        "routing_ms": _get_attr(result, "routing_ms"),
        "generation_ms": _get_attr(result, "generation_ms"),
        "inspection_ms": _get_attr(result, "inspection_ms"),
        "load_ms": _get_attr(result, "load_ms"),
        "prompt_ms": _get_attr(result, "prompt_ms"),
        "resident": _get_attr(result, "resident"),
        "policy_version": _get_attr(result, "policy_version") or POLICY_VERSION,
        "mock": _get_attr(result, "mock"),
        "created_at": time(),
    }
    return Observation.model_validate(payload)


def _score_with_fallback(task: BenchmarkTask, answer: object) -> ScoreResult | None:
    if not isinstance(answer, str) or not answer:
        return None
    try:
        return score(task, answer)
    except Exception:
        return None


def _get_attr(obj: object, name: str) -> object:
    return getattr(obj, name, None)
