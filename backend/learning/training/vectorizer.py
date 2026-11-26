"""Deterministic encoding from :class:`FeatureRow` to numeric LightGBM inputs.

The encoding is identical at fit time and at inference. Categorical columns are
remembered as ordered vocabularies so inference can either decode a known value
or report an ``unseen_candidate`` — never silently substitute a different code.
Missing numeric values become NaN; LightGBM handles them natively.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from backend.broker.feature_extractor import FeatureRow
from backend.learning.collection.types import Observation, ObservationStatus
from backend.learning.training.types import TrainingSample

_NUMERIC_COLUMNS: tuple[str, ...] = (
    "sanitized_char_count",
    "sanitized_token_estimate",
    "line_count",
    "question_count",
    "digit_count",
    "bullet_line_count",
    "bracket_count",
    "has_json_cue",
    "has_code_fence",
    "capability_quality",
    "capability_latency_ms",
    "installed",
    "healthy",
    "resident",
    "hardware_ram_bytes",
    "hardware_vram_bytes",
    "hardware_cpu_threads",
)

_CATEGORICAL_COLUMNS: tuple[str, ...] = (
    "task",
    "candidate_id",
    "candidate_family",
    "candidate_quantization",
    "capability_provenance",
    "hardware_platform",
)

_UNSEEN_CATEGORY_CODE = -1


class VectorizerError(ValueError):
    """Raised for malformed input; never contains caller values."""


@dataclass(frozen=True)
class CategoricalEncoder:
    """Integer-code mapping for one column; value order is the stored vocabulary."""

    column: str
    vocabulary: tuple[str, ...]

    def encode(self, value: object) -> int:
        if value is None:
            return _UNSEEN_CATEGORY_CODE
        token = _as_category(value)
        try:
            return self.vocabulary.index(token)
        except ValueError:
            return _UNSEEN_CATEGORY_CODE


@dataclass(frozen=True)
class FeatureVectors:
    """Matrix-plus-metadata returned by :func:`build_vectors`."""

    x: npt.NDArray[np.float32]
    y: npt.NDArray[np.float32]
    feature_order: tuple[str, ...]
    categorical_features: tuple[str, ...]
    encoders: dict[str, CategoricalEncoder]
    group_ids: tuple[str, ...]
    sample_count: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_count", int(self.x.shape[0]))


def feature_order() -> tuple[str, ...]:
    return _NUMERIC_COLUMNS + _CATEGORICAL_COLUMNS


def categorical_feature_names() -> tuple[str, ...]:
    return _CATEGORICAL_COLUMNS


def observations_to_samples(observations: Iterable[Observation]) -> tuple[TrainingSample, ...]:
    """Keep only observations usable as training targets: success + quality + features."""
    samples: list[TrainingSample] = []
    for observation in observations:
        if observation.status != ObservationStatus.SUCCESS:
            continue
        if observation.quality is None or observation.features is None:
            continue
        samples.append(
            TrainingSample(
                task_id=observation.task_id,
                family_id=_family_id(observation.task_id),
                candidate_id=observation.model_id,
                candidate_manifest_version=observation.model_version,
                task_category=observation.task_category,
                quality=observation.quality,
                features=observation.features,
            )
        )
    return tuple(samples)


def build_vectors(
    samples: Sequence[TrainingSample],
    *,
    encoders: dict[str, CategoricalEncoder] | None = None,
) -> FeatureVectors:
    """Build numeric X, y and preserve or learn the categorical vocabularies."""
    if not samples:
        raise VectorizerError("No training samples provided")
    columns = feature_order()
    resolved_encoders = encoders if encoders is not None else _fit_encoders(samples)
    for column in _CATEGORICAL_COLUMNS:
        if column not in resolved_encoders:
            raise VectorizerError("Encoder is missing a required categorical column")
    rows: list[list[float]] = []
    for sample in samples:
        row = [_numeric_value(sample.features, column) for column in _NUMERIC_COLUMNS]
        for column in _CATEGORICAL_COLUMNS:
            row.append(
                float(resolved_encoders[column].encode(_feature_value(sample.features, column)))
            )
        rows.append(row)
    x = np.asarray(rows, dtype=np.float32)
    y = np.asarray([sample.quality for sample in samples], dtype=np.float32)
    group_ids = tuple(sample.family_id for sample in samples)
    return FeatureVectors(
        x=x,
        y=y,
        feature_order=columns,
        categorical_features=_CATEGORICAL_COLUMNS,
        encoders=dict(resolved_encoders),
        group_ids=group_ids,
    )


def _fit_encoders(samples: Sequence[TrainingSample]) -> dict[str, CategoricalEncoder]:
    vocabularies: dict[str, list[str]] = {column: [] for column in _CATEGORICAL_COLUMNS}
    seen: dict[str, set[str]] = {column: set() for column in _CATEGORICAL_COLUMNS}
    for sample in samples:
        for column in _CATEGORICAL_COLUMNS:
            raw = _feature_value(sample.features, column)
            if raw is None:
                continue
            token = _as_category(raw)
            if token in seen[column]:
                continue
            vocabularies[column].append(token)
            seen[column].add(token)
    return {
        column: CategoricalEncoder(column=column, vocabulary=tuple(vocabularies[column]))
        for column in _CATEGORICAL_COLUMNS
    }


def _numeric_value(features: FeatureRow, column: str) -> float:
    value = _feature_value(features, column)
    if value is None:
        return float("nan")
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise VectorizerError("Non-numeric value supplied for a numeric feature")


def _feature_value(features: FeatureRow, column: str) -> object:
    value: object = getattr(features, column, None)
    if value is not None and not isinstance(value, bool) and hasattr(value, "value"):
        # StrEnum instances expose their string representation via ``.value``.
        inner: object = value.value
        return inner
    return value


def _as_category(value: object) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "value"):
        inner: object = value.value
        if isinstance(inner, str):
            return inner
    raise VectorizerError("Categorical feature must be string-valued")


def _family_id(task_id: str) -> str:
    if "-v" not in task_id:
        return task_id
    return task_id.rsplit("-v", 1)[0]
