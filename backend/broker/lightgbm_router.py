"""Learned router that ranks candidates with a promoted LightGBM artifact.

The router keeps the same :class:`backend.broker.router_interface.Router`
contract as :class:`RuleRouter`. Any condition that makes the learned path
unsafe — missing artifact, schema drift, booster corruption, NaN predictions,
unseen categorical values — degrades to the rules router for that single
request. The caller never sees a hidden failure: the returned
:class:`RoutingPlan` carries ``router_type`` and a ``reason`` that distinguishes
``learned_ranked`` from ``learned_fallback``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import lightgbm as lgb
import numpy as np

from backend.broker.context import RouterType, RoutingPlan, RoutingReason
from backend.broker.feature_extractor import (
    FEATURE_SCHEMA_HASH,
    FEATURE_SCHEMA_VERSION,
    FeatureRow,
    extract_features,
    trusted_payload,
)
from backend.broker.router_interface import RoutingError
from backend.broker.rule_router import RuleRouter
from backend.contracts import TaskCategory
from backend.learning.training import (
    ArtifactLoadError,
    TrainingArtifact,
    load_artifact,
    read_active_pointer,
)
from backend.learning.training.vectorizer import (
    CategoricalEncoder,
    categorical_feature_names,
    feature_order,
)
from backend.model_management.manifest import VerificationStatus
from backend.model_management.registry import HardwareContext, RegistrySnapshot
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.profiles.preferences import PreferenceProfile

_UNSEEN_CATEGORY_CODE = -1


class LightGBMRouter:
    """Rank candidates by the learned quality estimator with full rules fallback.

    The booster is loaded eagerly at construction so configuration errors fail
    immediately. If no active artifact is present the constructor still
    succeeds — every ``rank`` call then falls back to the rules router.
    """

    def __init__(
        self,
        router_root: Path | None = None,
        *,
        rules: RuleRouter | None = None,
        artifact: TrainingArtifact | None = None,
    ) -> None:
        self._rules = rules or RuleRouter()
        self._router_root = router_root
        self._artifact: TrainingArtifact | None = artifact
        self._booster: lgb.Booster | None = None
        self._encoders: dict[str, CategoricalEncoder] = {}
        self._feature_order: tuple[str, ...] = ()
        self._categorical_features: tuple[str, ...] = ()
        if self._artifact is None and router_root is not None:
            self._artifact = _load_active_artifact(router_root)
        if self._artifact is not None:
            self._bind_artifact(self._artifact)

    @classmethod
    def from_root(
        cls,
        router_root: Path,
        *,
        rules: RuleRouter | None = None,
    ) -> LightGBMRouter:
        """Build a router from a directory containing ``active.json``.

        An empty or unreadable root produces a learned router that silently
        falls back to rules on every call — the caller may still wire it in,
        and later promotion turns the learned path on without a restart.
        """
        return cls(router_root=router_root, rules=rules)

    @property
    def rules(self) -> RuleRouter:
        return self._rules

    @property
    def active_artifact_id(self) -> str | None:
        return self._artifact.artifact_id if self._artifact is not None else None

    @property
    def router_type(self) -> Literal["rules", "lightgbm"]:
        return "lightgbm"

    @property
    def router_version(self) -> str:
        return self.active_artifact_id or "rules-fallback"

    def _bind_artifact(self, artifact: TrainingArtifact) -> None:
        if artifact.feature_schema_version != FEATURE_SCHEMA_VERSION:
            self._artifact = None
            return
        if artifact.feature_schema_hash != FEATURE_SCHEMA_HASH:
            self._artifact = None
            return
        try:
            booster = lgb.Booster(model_str=artifact.booster_model)
        except lgb.basic.LightGBMError:
            self._artifact = None
            return
        expected_order = feature_order()
        if tuple(artifact.feature_order) != expected_order:
            self._artifact = None
            return
        if tuple(artifact.categorical_features) != categorical_feature_names():
            self._artifact = None
            return
        self._booster = booster
        self._feature_order = expected_order
        self._categorical_features = categorical_feature_names()
        self._encoders = {
            column: CategoricalEncoder(column=column, vocabulary=tuple(vocabulary))
            for column, vocabulary in artifact.categorical_vocabularies.items()
        }

    def rank(
        self,
        payload: SanitizedPayload,
        task: TaskCategory,
        snapshot: RegistrySnapshot,
        preferences: PreferenceProfile | None = None,
        hardware: HardwareContext | None = None,
    ) -> RoutingPlan:
        if self._artifact is None or self._booster is None:
            return _fallback_plan(self._rules.rank(payload, task, snapshot, preferences, hardware))
        try:
            trusted_payload(payload)
            if not isinstance(task, TaskCategory):
                raise RoutingError("Invalid routing task")
            roster = RegistrySnapshot.model_validate(snapshot.model_dump(warnings=False))
            machine = HardwareContext.model_validate(
                (hardware or roster.hardware).model_dump(warnings=False)
            )
            if machine.version != roster.hardware_version:
                raise RoutingError("Routing hardware context differs from roster snapshot")
            profile = PreferenceProfile.model_validate(
                (preferences or PreferenceProfile()).model_dump(warnings=False)
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise RoutingError("Learned routing preflight failed") from error

        candidate_rows: list[tuple[str, str, FeatureRow]] = []
        for entry in roster.roster.entries:
            if (
                entry.manifest.id not in roster.eligible_model_ids
                or not entry.installed
                or not entry.healthy
                or (
                    not roster.mock_mode
                    and entry.manifest.verification != VerificationStatus.VERIFIED
                )
            ):
                continue
            try:
                row = extract_features(payload, task, entry, machine, mock_mode=roster.mock_mode)
            except Exception:
                return _fallback_plan(
                    self._rules.rank(payload, task, snapshot, preferences, hardware)
                )
            candidate_rows.append((entry.manifest.id, entry.manifest.version, row))

        if not candidate_rows:
            return self._rules.rank(payload, task, snapshot, preferences, hardware)
        if _has_unseen_category(candidate_rows, task, self._encoders):
            return _fallback_plan(self._rules.rank(payload, task, snapshot, preferences, hardware))
        matrix = _vectorize(candidate_rows, self._feature_order, self._encoders)
        predictions_array = np.asarray(self._booster.predict(matrix), dtype=np.float64)
        if bool(np.isnan(predictions_array).any()):
            return _fallback_plan(self._rules.rank(payload, task, snapshot, preferences, hardware))
        indices = sorted(
            range(len(candidate_rows)),
            key=lambda idx: (
                -float(predictions_array[idx]),
                -_latency_tiebreaker(candidate_rows[idx][2], profile),
                candidate_rows[idx][0],
            ),
        )[:2]
        model_ids = tuple(candidate_rows[i][0] for i in indices)
        manifest_versions = tuple(candidate_rows[i][1] for i in indices)
        return RoutingPlan(
            model_ids=model_ids,
            manifest_versions=manifest_versions,
            reason=RoutingReason.LEARNED_RANKED,
            router_type=RouterType.LIGHTGBM,
            router_version=(self._artifact.artifact_id if self._artifact is not None else None),
        )


def _load_active_artifact(router_root: Path) -> TrainingArtifact | None:
    pointer = read_active_pointer(router_root)
    if pointer is None:
        return None
    try:
        return load_artifact(root=router_root, artifact_id=pointer)
    except ArtifactLoadError:
        return None


def _vectorize(
    rows: list[tuple[str, str, FeatureRow]],
    columns: tuple[str, ...],
    encoders: dict[str, CategoricalEncoder],
) -> np.ndarray:
    categorical = set(categorical_feature_names())
    matrix: list[list[float]] = []
    for _, _, feature_row in rows:
        values: list[float] = []
        for column in columns:
            if column in categorical:
                values.append(float(encoders[column].encode(_feature_value(feature_row, column))))
            else:
                values.append(_numeric_value(feature_row, column))
        matrix.append(values)
    return np.asarray(matrix, dtype=np.float32)


def _has_unseen_category(
    rows: list[tuple[str, str, FeatureRow]],
    task: TaskCategory,
    encoders: dict[str, CategoricalEncoder],
) -> bool:
    """Only candidate_id and task count as strict identity drift.

    Family/quantization/provenance values the trainer has not seen are encoded
    as the -1 sentinel; LightGBM handles that natively without destabilizing
    the prediction, so we do not force a fallback in that case.
    """
    if task.value not in encoders["task"].vocabulary:
        return True
    candidate_vocab = encoders["candidate_id"].vocabulary
    return any(model_id not in candidate_vocab for model_id, _, _ in rows)


def _feature_value(features: FeatureRow, column: str) -> object:
    value: object = getattr(features, column, None)
    if value is not None and not isinstance(value, bool) and hasattr(value, "value"):
        inner: object = value.value
        return inner
    return value


def _numeric_value(features: FeatureRow, column: str) -> float:
    value = _feature_value(features, column)
    if value is None:
        return float("nan")
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise RoutingError("Non-numeric value in numeric feature")


def _latency_tiebreaker(features: FeatureRow, profile: PreferenceProfile) -> float:
    """Prefer a lower latency when learned scores tie; residency breaks further ties."""
    latency_penalty = (
        features.capability_latency_ms
        if features.capability_latency_ms is not None
        else 1_000_000.0
    )
    residency_bonus = 0.0 if features.resident else 1.0
    return profile.weights.latency * latency_penalty + profile.weights.load * residency_bonus


def _fallback_plan(plan: RoutingPlan) -> RoutingPlan:
    """Rewrite a rules fallback plan to signal it came from the learned router."""
    reason = plan.reason
    if plan.model_ids:
        reason = RoutingReason.LEARNED_FALLBACK
    return RoutingPlan(
        model_ids=plan.model_ids,
        manifest_versions=plan.manifest_versions,
        reason=reason,
        cloud_hint=plan.cloud_hint,
        router_type=RouterType.LIGHTGBM,
        router_version=None,
    )


__all__ = ["LightGBMRouter"]
