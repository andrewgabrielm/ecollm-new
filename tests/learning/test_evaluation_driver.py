"""EvaluationDriver — gated evaluation dispatch against the mock broker."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.broker.evaluation import EvaluationDriver, EvaluationResult
from backend.broker.orchestrator import build_mock_broker
from backend.contracts import ErrorCode, TaskCategory
from backend.learning.benchmarks.types import BenchmarkTask, make_task_id
from backend.learning.collection import (
    CandidateIdentity,
    CollectionRunner,
    ObservationStatus,
    ObservationStore,
    plan_run,
)
from backend.storage.connection import LocalDatabase


def _driver() -> EvaluationDriver:
    broker = build_mock_broker()
    return EvaluationDriver(
        broker.dispatcher,
        broker.registry,
        broker.analyzer,
        inspector=broker.inspector,
        mode="mock",
    )


class TestEvaluationDriver:
    def test_success_on_known_candidate(self) -> None:
        driver = _driver()
        result = asyncio.run(driver.evaluate("hello world", "mock-general", task=TaskCategory.QA))
        assert isinstance(result, EvaluationResult)
        assert result.status == ObservationStatus.SUCCESS
        assert result.model_id == "mock-general"
        assert result.answer is not None
        assert result.feature_hash
        assert result.total_ms >= 0.0
        assert result.policy_version
        assert result.mock is True

    def test_unknown_candidate_is_provider_failure(self) -> None:
        driver = _driver()
        result = asyncio.run(
            driver.evaluate("hello world", "model-does-not-exist", task=TaskCategory.QA)
        )
        assert result.status == ObservationStatus.PROVIDER_FAILURE
        assert result.error_code == ErrorCode.UNAVAILABLE

    def test_empty_prompt_rejected(self) -> None:
        from backend.broker.evaluation import EvaluationError

        driver = _driver()
        with pytest.raises(EvaluationError):
            asyncio.run(driver.evaluate("", "mock-general", task=TaskCategory.QA))

    def test_runner_produces_observations_end_to_end(self, tmp_path: Path) -> None:
        driver = _driver()
        database = LocalDatabase(tmp_path / "var")
        database.migrate()
        store = ObservationStore(database)
        tasks = (
            BenchmarkTask(
                task_id=make_task_id("qa-sample", 0),
                family_id="qa-sample",
                category=TaskCategory.QA,
                variant_index=0,
                prompt="Say anything.",
                reference={"answers": ["anything"]},
            ),
        )
        candidates = (
            CandidateIdentity(
                "mock-general",
                "fec3142b968b32266ba87a01baebca3aac291712b63240274d393148df265c7f",
            ),
        )
        plan = plan_run(
            run_id="driver-end-to-end",
            split="dev_train",
            tasks=tasks,
            candidates=candidates,
            store=store,
        )
        summary = asyncio.run(CollectionRunner(driver, store).run(plan))
        assert summary.produced + summary.failed == 1
        rows = store.load_all(run_id="driver-end-to-end")
        assert len(rows) == 1
        assert rows[0].mock is True
        assert rows[0].schema_version == "observations-v2"
        assert rows[0].features is not None
        assert rows[0].features.task.value == TaskCategory.QA.value
