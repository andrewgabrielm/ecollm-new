"""Step 14 learned router tests — fully mock, no real inference."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from backend.broker.context import RouterType, RoutingReason
from backend.broker.lightgbm_router import LightGBMRouter
from backend.broker.orchestrator import build_mock_broker
from backend.broker.rule_router import RuleRouter
from backend.contracts import TaskCategory
from backend.learning.training import (
    PromotionCriteria,
    observations_to_samples,
    promote_artifact,
    save_artifact,
    train_quality_model,
    validate_model,
)
from backend.learning.training.artifact import (
    canonical_training_hash,
    canonical_validation_hash,
)
from backend.learning.training.trainer import TrainingParameters
from backend.model_management.registry import RegistrySnapshot
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer
from tests.learning._router_fixtures import (
    BAD_MODEL as _BAD_MODEL,
)
from tests.learning._router_fixtures import (
    GOOD_MODEL as _GOOD_MODEL,
)
from tests.learning._router_fixtures import (
    make_dataset as _dataset,
)


def _save_artifact(tmp_path: Path, artifact_id: str = "router-step14") -> Path:
    train_dataset = _dataset(split="dev_train", task_count=24)
    val_dataset = _dataset(split="dev_validation", task_count=8)
    trained = train_quality_model(
        observations_to_samples(train_dataset),
        parameters=TrainingParameters(num_boost_round=80, min_data_in_leaf=1),
    )
    report = validate_model(
        trained,
        observations_to_samples(val_dataset),
        criteria=PromotionCriteria(
            min_train_samples=1,
            min_validation_samples=1,
            min_distinct_families=2,
            min_distinct_tasks=2,
            min_distinct_candidates=2,
            max_validation_mae=0.4,
        ),
    )
    assert report.promotion_passed, report.promotion_blockers
    save_artifact(
        artifact_id=artifact_id,
        trained=trained,
        report=report,
        training_hash=canonical_training_hash(["t1"], [_GOOD_MODEL, _BAD_MODEL]),
        validation_hash=canonical_validation_hash(["t2"], [_GOOD_MODEL, _BAD_MODEL]),
        root=tmp_path,
        created_at=1.0,
    )
    promote_artifact(root=tmp_path, artifact_id=artifact_id)
    return tmp_path


def _sanitized_payload(prompt: str) -> SanitizedPayload:
    broker = build_mock_broker()
    sanitized = Sanitizer(broker.analyzer).sanitize(uuid4(), prompt)
    return sanitized.payload


def _snapshot() -> RegistrySnapshot:
    broker = build_mock_broker()
    return broker.registry.snapshot()


class TestLightGBMRouter:
    def test_ranks_known_candidates_when_artifact_present(self, tmp_path: Path) -> None:
        _save_artifact(tmp_path)
        router = LightGBMRouter.from_root(tmp_path)
        payload = _sanitized_payload("Simple factual question.")
        plan = router.rank(payload, TaskCategory.QA, _snapshot())
        assert plan.router_type == RouterType.LIGHTGBM
        assert plan.reason == RoutingReason.LEARNED_RANKED
        assert plan.router_version is not None
        assert len(plan.model_ids) >= 1

    def test_missing_artifact_falls_back_to_rules(self, tmp_path: Path) -> None:
        router = LightGBMRouter.from_root(tmp_path)
        assert router.active_artifact_id is None
        payload = _sanitized_payload("Simple factual question.")
        plan = router.rank(payload, TaskCategory.QA, _snapshot())
        assert plan.router_type == RouterType.LIGHTGBM
        assert plan.reason in {
            RoutingReason.LEARNED_FALLBACK,
            RoutingReason.RULES_RANKED,
            RoutingReason.RULES_FALLBACK,
            RoutingReason.NO_ELIGIBLE_MODEL,
        }

    def test_corrupt_booster_falls_back(self, tmp_path: Path) -> None:
        _save_artifact(tmp_path, artifact_id="router-corrupt")
        artifact_file = tmp_path / "router-corrupt" / "artifact.json"
        payload = json.loads(artifact_file.read_text())
        payload["booster_model"] = "this is not a valid booster"
        import hashlib

        payload["booster_sha256"] = hashlib.sha256(
            payload["booster_model"].encode("utf-8")
        ).hexdigest()
        artifact_file.write_text(json.dumps(payload))
        router = LightGBMRouter.from_root(tmp_path)
        # Active-artifact lookup should bail out on booster parse failure.
        assert router.active_artifact_id is None
        request_payload = _sanitized_payload("Simple factual question.")
        plan = router.rank(request_payload, TaskCategory.QA, _snapshot())
        assert plan.router_type == RouterType.LIGHTGBM
        assert plan.reason != RoutingReason.LEARNED_RANKED

    def test_schema_mismatch_falls_back(self, tmp_path: Path) -> None:
        _save_artifact(tmp_path, artifact_id="router-schema")
        artifact_file = tmp_path / "router-schema" / "artifact.json"
        payload = json.loads(artifact_file.read_text())
        payload["feature_schema_hash"] = re.sub(r".", "0", "x" * 64)
        artifact_file.write_text(json.dumps(payload))
        # feature_schema_hash is validated by TrainingArtifact; the load succeeds but
        # the router refuses to bind because the hash differs from this build.
        router = LightGBMRouter.from_root(tmp_path)
        assert router.active_artifact_id is None
        request_payload = _sanitized_payload("Simple factual question.")
        plan = router.rank(request_payload, TaskCategory.QA, _snapshot())
        assert plan.reason != RoutingReason.LEARNED_RANKED

    def test_unseen_candidate_at_inference_falls_back(self, tmp_path: Path) -> None:
        # Train with a different vocabulary than the live snapshot's models.
        from tests.learning._router_fixtures import make_observation as _observation

        alien_dataset = [
            _observation(
                f"task-alien-{idx}-v0",
                "model-alien" if idx % 2 == 0 else "model-alien-2",
                split="dev_train",
                quality=min(1.0, 0.3 + 0.03 * idx),
                task=TaskCategory.QA,
                quality_prior=0.5,
            )
            for idx in range(20)
        ]
        trained = train_quality_model(
            observations_to_samples(alien_dataset),
            parameters=TrainingParameters(num_boost_round=20, min_data_in_leaf=1),
        )
        # Still validate (with the same alien vocab) so the artifact passes gates.
        val_dataset = [
            _observation(
                f"task-alien-val-{idx}-v0",
                "model-alien",
                split="dev_validation",
                quality=0.8,
                task=TaskCategory.QA,
                quality_prior=0.5,
            )
            for idx in range(2)
        ] + [
            _observation(
                f"task-alien-val-{idx}-v1",
                "model-alien-2",
                split="dev_validation",
                quality=0.4,
                task=TaskCategory.QA,
                quality_prior=0.5,
            )
            for idx in range(2)
        ]
        report = validate_model(
            trained,
            observations_to_samples(val_dataset),
            criteria=PromotionCriteria(
                min_train_samples=1,
                min_validation_samples=1,
                min_distinct_families=2,
                min_distinct_tasks=2,
                min_distinct_candidates=2,
                max_validation_mae=0.9,
                min_validation_r2=-10.0,
                min_regret_improvement=0.0,
            ),
        )
        assert report.promotion_passed, report.promotion_blockers
        save_artifact(
            artifact_id="router-alien",
            trained=trained,
            report=report,
            training_hash=canonical_training_hash(["t"], ["m"]),
            validation_hash=canonical_validation_hash(["t"], ["m"]),
            root=tmp_path,
            created_at=1.0,
        )
        promote_artifact(root=tmp_path, artifact_id="router-alien")
        router = LightGBMRouter.from_root(tmp_path)
        assert router.active_artifact_id == "router-alien"
        plan = router.rank(_sanitized_payload("Question?"), TaskCategory.QA, _snapshot())
        # Both of the mock broker's candidates are unknown to this artifact,
        # so the router must defer to the rules fallback.
        assert plan.reason in {
            RoutingReason.LEARNED_FALLBACK,
            RoutingReason.RULES_FALLBACK,
        }

    def test_accepts_injected_rules_router(self, tmp_path: Path) -> None:
        _save_artifact(tmp_path)
        custom_rules = RuleRouter()
        router = LightGBMRouter.from_root(tmp_path, rules=custom_rules)
        assert router.rules is custom_rules
