"""Fit a LightGBM quality regressor over benchmark observations.

The trainer is a thin wrapper around :func:`lightgbm.train`. It accepts the
observations as-is, discards non-trainable rows and returns a native booster
plus the encoder maps so the caller can rebuild the same matrix at inference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import lightgbm as lgb

from backend.learning.training.vectorizer import (
    FeatureVectors,
    build_vectors,
    categorical_feature_names,
)

if TYPE_CHECKING:
    from backend.learning.training.types import TrainingSample


class TrainingError(RuntimeError):
    """Raised for unrecoverable training state; contains no caller values."""


@dataclass(frozen=True)
class TrainingParameters:
    objective: str = "regression"
    metric: str = "l2"
    num_leaves: int = 15
    learning_rate: float = 0.08
    feature_fraction: float = 0.9
    bagging_fraction: float = 1.0
    bagging_freq: int = 0
    min_data_in_leaf: int = 4
    num_boost_round: int = 60
    seed: int = 20260101
    verbose: int = -1


@dataclass(frozen=True)
class TrainedModel:
    booster: lgb.Booster
    vectors: FeatureVectors
    parameters: TrainingParameters


def train_quality_model(
    samples: Sequence[TrainingSample],
    *,
    parameters: TrainingParameters | None = None,
) -> TrainedModel:
    if not samples:
        raise TrainingError("No training samples were supplied")
    params = parameters or TrainingParameters()
    if params.num_boost_round <= 0:
        raise TrainingError("num_boost_round must be positive")
    vectors = build_vectors(samples)
    categorical_indices = [
        vectors.feature_order.index(name) for name in categorical_feature_names()
    ]
    dataset = lgb.Dataset(
        vectors.x,
        label=vectors.y,
        categorical_feature=categorical_indices,
        free_raw_data=False,
    )
    try:
        booster = lgb.train(
            params={
                "objective": params.objective,
                "metric": params.metric,
                "num_leaves": params.num_leaves,
                "learning_rate": params.learning_rate,
                "feature_fraction": params.feature_fraction,
                "bagging_fraction": params.bagging_fraction,
                "bagging_freq": params.bagging_freq,
                "min_data_in_leaf": params.min_data_in_leaf,
                "seed": params.seed,
                "verbose": params.verbose,
            },
            train_set=dataset,
            num_boost_round=params.num_boost_round,
        )
    except (lgb.basic.LightGBMError, ValueError) as error:
        raise TrainingError("LightGBM training failed") from error
    return TrainedModel(booster=booster, vectors=vectors, parameters=params)
