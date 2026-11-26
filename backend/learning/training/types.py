"""Typed contracts for Step 13 training, validation and promotion.

Everything that lands on disk or crosses a function boundary flows through a
frozen :class:`backend.contracts.ContractModel`. Promotion criteria are declared
BEFORE a model is fit so a later tuning pass cannot move the goalposts.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr

from backend.broker.feature_extractor import FEATURE_SCHEMA_HASH, FEATURE_SCHEMA_VERSION, FeatureRow
from backend.contracts import ContractModel, TaskCategory

ARTIFACT_SCHEMA_VERSION: Literal["router-artifact-v1"] = "router-artifact-v1"

Quality = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
Positive = Annotated[StrictFloat, Field(gt=0.0)]


class TrainingSample(ContractModel):
    """One (task_id, candidate, features, quality) row used to fit or validate."""

    task_id: StrictStr
    family_id: StrictStr
    candidate_id: StrictStr
    candidate_manifest_version: StrictStr
    task_category: TaskCategory
    quality: Quality
    features: FeatureRow


class PromotionCriteria(ContractModel):
    """Predeclared, immutable gates that a trained model must pass to promote."""

    schema_version: Literal["promotion-criteria-v1"] = "promotion-criteria-v1"
    min_train_samples: StrictInt = Field(default=16, ge=1)
    min_validation_samples: StrictInt = Field(default=8, ge=1)
    min_distinct_families: StrictInt = Field(default=2, ge=1)
    min_distinct_tasks: StrictInt = Field(default=2, ge=1)
    min_distinct_candidates: StrictInt = Field(default=2, ge=1)
    max_validation_mae: Quality = Field(default=0.35)
    min_validation_r2: StrictFloat = Field(default=0.0)
    require_regret_improvement: StrictBool = True
    min_regret_improvement: StrictFloat = Field(default=0.0, ge=0.0)


class PerTaskMetrics(ContractModel):
    task: TaskCategory
    sample_count: StrictInt = Field(ge=0)
    mae: Quality
    hindsight_quality: Quality
    router_quality: Quality
    rules_quality: Quality


class ValidationReport(ContractModel):
    schema_version: Literal["validation-report-v1"] = "validation-report-v1"
    sample_count: StrictInt = Field(ge=0)
    distinct_families: StrictInt = Field(ge=0)
    distinct_tasks: StrictInt = Field(ge=0)
    distinct_candidates: StrictInt = Field(ge=0)
    mae: Quality
    r2: StrictFloat
    hindsight_quality_mean: Quality
    router_quality_mean: Quality
    rules_quality_mean: Quality
    router_regret: Quality
    rules_regret: Quality
    regret_improvement: StrictFloat
    nan_prediction_count: StrictInt = Field(ge=0)
    unseen_category_count: StrictInt = Field(ge=0)
    unseen_candidate_count: StrictInt = Field(ge=0)
    per_task: tuple[PerTaskMetrics, ...]
    promotion_passed: StrictBool
    promotion_blockers: tuple[StrictStr, ...]
    criteria: PromotionCriteria


class TrainingArtifact(ContractModel):
    """Everything required to reload and score the LightGBM model deterministically.

    ``booster_model`` is the native LightGBM text format produced by
    :meth:`lightgbm.Booster.model_to_string`. ``feature_order`` pins the exact
    column order used during training so inference can rebuild the matrix the
    same way. The checksum is computed over ``booster_model`` only — a change in
    the trained bytes invalidates reuse.
    """

    schema_version: Literal["router-artifact-v1"] = ARTIFACT_SCHEMA_VERSION
    artifact_id: StrictStr = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    feature_schema_version: Literal["routing-features-v1"] = FEATURE_SCHEMA_VERSION
    feature_schema_hash: StrictStr = Field(default_factory=lambda: FEATURE_SCHEMA_HASH)
    feature_order: tuple[StrictStr, ...]
    categorical_features: tuple[StrictStr, ...]
    categorical_vocabularies: dict[StrictStr, tuple[StrictStr, ...]]
    booster_model: StrictStr
    booster_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    training_sample_count: StrictInt = Field(ge=1)
    training_family_count: StrictInt = Field(ge=1)
    training_quality_mean: Quality
    training_quality_variance: StrictFloat = Field(ge=0.0)
    training_hash: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    validation_hash: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    validation_report: ValidationReport
    policy_version: StrictStr
    created_at: StrictFloat = Field(ge=0.0)


class ArtifactManifest(ContractModel):
    """Index describing an artifact on disk; the heavy ``TrainingArtifact`` is a sibling file."""

    schema_version: Literal["router-artifact-manifest-v1"] = "router-artifact-manifest-v1"
    artifact_id: StrictStr = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    artifact_path: StrictStr
    artifact_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    training_sample_count: StrictInt = Field(ge=1)
    validation_report_summary: ValidationReport
    policy_version: StrictStr
    created_at: StrictFloat = Field(ge=0.0)
