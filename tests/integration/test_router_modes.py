"""Step 14 end-to-end: both router modes succeed through the chat orchestrator."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.api.schemas import ChatRequest, ChatResponse
from backend.broker.lightgbm_router import LightGBMRouter
from backend.broker.orchestrator import ChatOrchestrator, build_mock_broker
from backend.broker.rule_router import RuleRouter
from backend.contracts import ExecutionMode
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
from tests.learning._router_fixtures import (
    BAD_MODEL,
    GOOD_MODEL,
    make_dataset,
)

pytestmark = pytest.mark.privacy


def _save_passing_artifact(router_root: Path, artifact_id: str = "router-e2e") -> None:
    train_dataset = make_dataset(split="dev_train", task_count=16)
    val_dataset = make_dataset(split="dev_validation", task_count=4)
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
            max_validation_mae=0.5,
            min_validation_r2=-10.0,
        ),
    )
    assert report.promotion_passed, report.promotion_blockers
    save_artifact(
        artifact_id=artifact_id,
        trained=trained,
        report=report,
        training_hash=canonical_training_hash(["t1"], [GOOD_MODEL, BAD_MODEL]),
        validation_hash=canonical_validation_hash(["t2"], [GOOD_MODEL, BAD_MODEL]),
        root=router_root,
        created_at=1.0,
    )
    promote_artifact(root=router_root, artifact_id=artifact_id)


def _run(orchestrator: ChatOrchestrator, prompt: str) -> ChatResponse:
    from backend.contracts import TaskCategory

    request = ChatRequest(prompt=prompt, task_hint=TaskCategory.QA, mode=ExecutionMode.LOCAL_ONLY)
    outcome = asyncio.run(orchestrator.chat(request))
    assert isinstance(outcome, ChatResponse)
    return outcome


def test_rules_mode_identifies_itself_in_response() -> None:
    broker = build_mock_broker(router=RuleRouter())
    response = _run(broker, "Simple factual question about geography.")
    assert response.router_type == "rules"
    assert response.router_version


def test_lightgbm_mode_without_artifact_falls_back_but_declares_type(tmp_path: Path) -> None:
    router = LightGBMRouter.from_root(tmp_path)
    broker = build_mock_broker(router=router)
    response = _run(broker, "Simple factual question about geography.")
    assert response.router_type == "lightgbm"
    assert response.router_version == "rules-fallback"


def test_lightgbm_mode_with_promoted_artifact_declares_version(tmp_path: Path) -> None:
    _save_passing_artifact(tmp_path)
    router = LightGBMRouter.from_root(tmp_path)
    assert router.active_artifact_id == "router-e2e"
    broker = build_mock_broker(router=router)
    response = _run(broker, "Simple factual question about geography.")
    assert response.router_type == "lightgbm"
    assert response.router_version == "router-e2e"
