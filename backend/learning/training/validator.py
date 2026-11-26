"""Group-aware validation of a trained booster against held-out observations.

Metrics:
- MAE and R² on predicted quality per sample.
- Per-task "regret" vs the hindsight best candidate for each task.
- Rules-baseline quality: for each task, pick the candidate the rules router
  would have chosen using capability priors; compare its mean quality to the
  learned router's mean quality.
- Unseen categorical and schema-drift counts; any NaN prediction is flagged.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence

import numpy as np

from backend.contracts import TaskCategory
from backend.learning.training.trainer import TrainedModel
from backend.learning.training.types import (
    PerTaskMetrics,
    PromotionCriteria,
    TrainingSample,
    ValidationReport,
)
from backend.learning.training.vectorizer import build_vectors


class ValidationError(RuntimeError):
    """Raised for unrecoverable validation state; contains no caller values."""


def validate_model(
    trained: TrainedModel,
    samples: Sequence[TrainingSample],
    *,
    criteria: PromotionCriteria | None = None,
) -> ValidationReport:
    if not samples:
        raise ValidationError("Validation requires at least one sample")
    rules = criteria or PromotionCriteria()
    encoders = trained.vectors.encoders
    unseen_category = 0
    unseen_candidate = 0
    for sample in samples:
        if sample.task_category.value not in encoders["task"].vocabulary:
            unseen_category += 1
        if sample.candidate_id not in encoders["candidate_id"].vocabulary:
            unseen_candidate += 1
    try:
        vectors = build_vectors(samples, encoders=encoders)
    except Exception as error:
        raise ValidationError("Validation feature build failed") from error
    predictions = trained.booster.predict(vectors.x)
    predictions_array = np.asarray(predictions, dtype=np.float64)
    nan_prediction_count = int(np.isnan(predictions_array).sum())
    safe_predictions = np.where(
        np.isnan(predictions_array), vectors.y.mean(), predictions_array
    ).astype(np.float64)
    truths = vectors.y.astype(np.float64)
    mae = float(np.mean(np.abs(safe_predictions - truths)))
    truths_mean = float(np.mean(truths))
    ss_res = float(np.sum((safe_predictions - truths) ** 2))
    ss_tot = float(np.sum((truths - truths_mean) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    per_task_groups: dict[TaskCategory, list[int]] = defaultdict(list)
    for index, sample in enumerate(samples):
        per_task_groups[sample.task_category].append(index)

    task_groups: dict[str, list[int]] = defaultdict(list)
    for index, sample in enumerate(samples):
        task_groups[sample.task_id].append(index)

    router_quality_total = 0.0
    rules_quality_total = 0.0
    hindsight_quality_total = 0.0
    task_router_q: dict[str, float] = {}
    task_rules_q: dict[str, float] = {}
    task_hindsight_q: dict[str, float] = {}
    task_category: dict[str, TaskCategory] = {}
    for task_id, indices in task_groups.items():
        qualities = [samples[i].quality for i in indices]
        best_index = indices[int(np.argmax(safe_predictions[indices]))]
        router_q = samples[best_index].quality
        rules_index = _rules_pick(samples, indices)
        rules_q = samples[rules_index].quality
        hindsight_q = max(qualities)
        router_quality_total += router_q
        rules_quality_total += rules_q
        hindsight_quality_total += hindsight_q
        task_router_q[task_id] = router_q
        task_rules_q[task_id] = rules_q
        task_hindsight_q[task_id] = hindsight_q
        task_category[task_id] = samples[indices[0]].task_category
    task_count = len(task_groups)
    router_mean_q = _clamp_quality(router_quality_total / task_count)
    rules_mean_q = _clamp_quality(rules_quality_total / task_count)
    hindsight_mean_q = _clamp_quality(hindsight_quality_total / task_count)
    router_regret = _clamp_quality(max(0.0, hindsight_mean_q - router_mean_q))
    rules_regret = _clamp_quality(max(0.0, hindsight_mean_q - rules_mean_q))
    regret_improvement = rules_regret - router_regret

    per_task: list[PerTaskMetrics] = []
    for category, indices in per_task_groups.items():
        cat_mae = float(np.mean(np.abs(safe_predictions[indices] - truths[indices])))
        task_ids_in_category = {samples[i].task_id for i in indices}
        hindsight_q = sum(task_hindsight_q[t] for t in task_ids_in_category) / len(
            task_ids_in_category
        )
        router_q = sum(task_router_q[t] for t in task_ids_in_category) / len(task_ids_in_category)
        rules_q = sum(task_rules_q[t] for t in task_ids_in_category) / len(task_ids_in_category)
        per_task.append(
            PerTaskMetrics(
                task=category,
                sample_count=len(indices),
                mae=_clamp_quality(cat_mae),
                hindsight_quality=_clamp_quality(hindsight_q),
                router_quality=_clamp_quality(router_q),
                rules_quality=_clamp_quality(rules_q),
            )
        )
    per_task.sort(key=lambda item: item.task.value)

    distinct_families = len({sample.family_id for sample in samples})
    distinct_candidates = len({sample.candidate_id for sample in samples})

    blockers = _check_criteria(
        criteria=rules,
        distinct_families=distinct_families,
        distinct_tasks=task_count,
        distinct_candidates=distinct_candidates,
        sample_count=len(samples),
        mae=mae,
        r2=r2,
        regret_improvement=regret_improvement,
        nan_prediction_count=nan_prediction_count,
        unseen_category_count=unseen_category,
        unseen_candidate_count=unseen_candidate,
    )

    return ValidationReport(
        sample_count=len(samples),
        distinct_families=distinct_families,
        distinct_tasks=task_count,
        distinct_candidates=distinct_candidates,
        mae=_clamp_quality(mae),
        r2=_safe_float(r2),
        hindsight_quality_mean=hindsight_mean_q,
        router_quality_mean=router_mean_q,
        rules_quality_mean=rules_mean_q,
        router_regret=router_regret,
        rules_regret=rules_regret,
        regret_improvement=_safe_float(regret_improvement),
        nan_prediction_count=nan_prediction_count,
        unseen_category_count=unseen_category,
        unseen_candidate_count=unseen_candidate,
        per_task=tuple(per_task),
        promotion_passed=not blockers,
        promotion_blockers=tuple(blockers),
        criteria=rules,
    )


def _rules_pick(samples: Sequence[TrainingSample], indices: Sequence[int]) -> int:
    """Emulate the rules router: prefer highest capability_quality, break ties by id."""
    best = indices[0]
    best_quality = _capability_quality(samples[best])
    for candidate in indices[1:]:
        current = _capability_quality(samples[candidate])
        if current > best_quality or (
            current == best_quality and samples[candidate].candidate_id < samples[best].candidate_id
        ):
            best = candidate
            best_quality = current
    return best


def _capability_quality(sample: TrainingSample) -> float:
    value = sample.features.capability_quality
    if value is None:
        return -1.0
    return float(value)


def _clamp_quality(value: float) -> float:
    return max(0.0, min(1.0, _safe_float(value)))


def _safe_float(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return float(value)


def _check_criteria(
    *,
    criteria: PromotionCriteria,
    distinct_families: int,
    distinct_tasks: int,
    distinct_candidates: int,
    sample_count: int,
    mae: float,
    r2: float,
    regret_improvement: float,
    nan_prediction_count: int,
    unseen_category_count: int,
    unseen_candidate_count: int,
) -> list[str]:
    blockers: list[str] = []
    if sample_count < criteria.min_validation_samples:
        blockers.append("validation_sample_count_below_minimum")
    if distinct_families < criteria.min_distinct_families:
        blockers.append("validation_family_count_below_minimum")
    if distinct_tasks < criteria.min_distinct_tasks:
        blockers.append("validation_task_count_below_minimum")
    if distinct_candidates < criteria.min_distinct_candidates:
        blockers.append("validation_candidate_count_below_minimum")
    if mae > criteria.max_validation_mae:
        blockers.append("validation_mae_above_maximum")
    if r2 < criteria.min_validation_r2:
        blockers.append("validation_r2_below_minimum")
    if criteria.require_regret_improvement and regret_improvement < criteria.min_regret_improvement:
        blockers.append("regret_improvement_below_minimum")
    if nan_prediction_count > 0:
        blockers.append("prediction_contained_nan")
    if unseen_category_count > 0:
        blockers.append("validation_contains_unseen_task_category")
    if unseen_candidate_count > 0:
        blockers.append("validation_contains_unseen_candidate")
    return blockers
