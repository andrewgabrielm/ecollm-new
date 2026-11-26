"""Deterministic local ranking with fixed normalization and honest missing priors.

Utility is quality_weight * prior_quality - latency_weight * normalized_latency
- load_weight * cold_start_penalty. The latter is a residency heuristic, not a
measurement of current utilization. Scores are tunable hypotheses; fixture
observations never establish real model quality or speed.
"""

from typing import Literal

from pydantic import Field, StrictBool

from backend.broker.context import RouterType, RoutingPlan, RoutingReason
from backend.broker.feature_extractor import FeatureRow, extract_features, trusted_payload
from backend.broker.router_interface import RoutingError
from backend.contracts import ContractModel, TaskCategory
from backend.model_management.manifest import Digest, Identifier, VerificationStatus
from backend.model_management.registry import HardwareContext, RegistrySnapshot
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.profiles.preferences import PreferenceProfile
from backend.versions import canonical_hash


class RuleRouterConfig(ContractModel):
    version: Literal["rule-router-v1"] = "rule-router-v1"
    latency_reference_ms: float = Field(default=1000.0, gt=0, strict=True)
    unknown_load_penalty: float = Field(default=1.0, ge=0, le=1, strict=True)
    nonresident_load_penalty: float = Field(default=1.0, ge=0, le=1, strict=True)


class CandidateScore(ContractModel):
    model_id: Identifier
    manifest_version: Digest
    utility: float = Field(strict=True)
    quality_used: float = Field(ge=0, le=1, strict=True)
    normalized_latency: float = Field(ge=0, le=1, strict=True)
    cold_start_penalty: float = Field(ge=0, le=1, strict=True)
    unknown_quality: StrictBool
    unknown_latency: StrictBool
    unknown_residency: StrictBool
    declares_task: StrictBool = False
    features: FeatureRow


class RuleRouter:
    def __init__(self, config: RuleRouterConfig | None = None) -> None:
        try:
            self._config = RuleRouterConfig.model_validate(
                (config or RuleRouterConfig()).model_dump(warnings=False)
            )
        except (AttributeError, TypeError, ValueError):
            raise RoutingError("Invalid rules router configuration") from None

    @property
    def config_version(self) -> str:
        return canonical_hash(self._config.model_dump(mode="json"), namespace="rule-router-v1")

    @property
    def router_type(self) -> Literal["rules", "lightgbm"]:
        return "rules"

    @property
    def router_version(self) -> str:
        return self.config_version

    def rank_details(
        self,
        payload: SanitizedPayload,
        task: TaskCategory,
        snapshot: RegistrySnapshot,
        preferences: PreferenceProfile | None = None,
        hardware: HardwareContext | None = None,
    ) -> tuple[CandidateScore, ...]:
        """Pure pre-generation scoring; no content or policy authority is returned."""
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
            scores: list[CandidateScore] = []
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
                row = extract_features(payload, task, entry, machine, mock_mode=roster.mock_mode)
                # Whether the catalog lists this task for this model, regardless of
                # whether it has been benchmarked. capability_for() only surfaces a
                # measured entry, so with every score unknown this declaration is
                # the only thing distinguishing a specialist from a general model.
                declares_task = any(
                    item.task == task for item in entry.manifest.capability_benchmark
                )
                quality = row.capability_quality if row.capability_quality is not None else 0.0
                latency = (
                    min(row.capability_latency_ms / self._config.latency_reference_ms, 1.0)
                    if row.capability_latency_ms is not None
                    else 1.0
                )
                load = (
                    0.0
                    if row.resident is True
                    else self._config.nonresident_load_penalty
                    if row.resident is False
                    else self._config.unknown_load_penalty
                )
                utility = (
                    profile.weights.quality * quality
                    - profile.weights.latency * latency
                    - profile.weights.load * load
                )
                scores.append(
                    CandidateScore(
                        model_id=row.candidate_id,
                        manifest_version=row.candidate_manifest_version,
                        utility=utility,
                        quality_used=quality,
                        normalized_latency=latency,
                        cold_start_penalty=load,
                        unknown_quality=row.capability_quality is None,
                        unknown_latency=row.capability_latency_ms is None,
                        unknown_residency=row.resident is None,
                        declares_task=declares_task,
                        features=row,
                    )
                )
            # Declared coverage breaks ties only. It is owner-reviewed catalog
            # data, not a measurement, so it never outranks a real utility
            # difference: once benchmarking supplies quality scores those decide,
            # and this stops mattering. Without it every score is 0.0 and ranking
            # degrades to alphabetical, which never picks a specialist.
            return tuple(
                sorted(
                    scores,
                    key=lambda item: (-item.utility, not item.declares_task, item.model_id),
                )
            )
        except (AttributeError, TypeError, ValueError):
            raise RoutingError("Rules routing failed") from None

    def rank(
        self,
        payload: SanitizedPayload,
        task: TaskCategory,
        snapshot: RegistrySnapshot,
        preferences: PreferenceProfile | None = None,
        hardware: HardwareContext | None = None,
    ) -> RoutingPlan:
        scores = self.rank_details(payload, task, snapshot, preferences, hardware)[:2]
        if not scores:
            reason = RoutingReason.NO_ELIGIBLE_MODEL
        elif any(score.unknown_quality or score.unknown_latency for score in scores):
            reason = RoutingReason.RULES_FALLBACK
        else:
            reason = RoutingReason.RULES_RANKED
        return RoutingPlan(
            model_ids=tuple(score.model_id for score in scores),
            manifest_versions=tuple(score.manifest_version for score in scores),
            reason=reason,
            router_type=RouterType.RULES,
            router_version=self.config_version,
        )
