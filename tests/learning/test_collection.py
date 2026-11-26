"""Step 12 collector tests — all mock-based, no real inference."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.contracts import ErrorCode, TaskCategory
from backend.learning.benchmarks.types import BenchmarkTask, make_task_id
from backend.learning.collection import (
    CandidateIdentity,
    CollectionRunner,
    Observation,
    ObservationStatus,
    ObservationStore,
    plan_run,
)
from backend.storage.connection import LocalDatabase
from backend.versions import POLICY_VERSION

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> LocalDatabase:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    return database


@pytest.fixture()
def store(db: LocalDatabase) -> ObservationStore:
    return ObservationStore(db)


@pytest.fixture()
def tasks() -> tuple[BenchmarkTask, ...]:
    return tuple(
        BenchmarkTask(
            task_id=make_task_id(f"qa-example-{idx}", 0),
            family_id=f"qa-example-{idx}",
            category=TaskCategory.QA,
            variant_index=0,
            prompt=f"Capital of country {idx}?",
            reference={"answers": [f"Answer{idx}"]},
        )
        for idx in range(3)
    )


@pytest.fixture()
def candidates() -> tuple[CandidateIdentity, ...]:
    return (
        CandidateIdentity("mock-general", "v-general-1"),
        CandidateIdentity("mock-specialist", "v-specialist-1"),
    )


@dataclass
class _FakeResult:
    status: ObservationStatus
    model_id: str
    model_version: str
    task: TaskCategory
    feature_hash: str = "0" * 64
    answer: str | None = None
    error_code: ErrorCode | None = None
    total_ms: float = 1.0
    privacy_ms: float = 0.1
    routing_ms: float = 0.1
    generation_ms: float = 0.5
    inspection_ms: float = 0.1
    load_ms: float | None = None
    prompt_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    resident: bool = False
    policy_version: str = POLICY_VERSION
    mock: bool = True


class _FakeEvaluator:
    """Returns pre-programmed results keyed by ``(task_id, model_id)``.

    Tracks every evaluated pair so tests can assert ordering and resumption.
    """

    mock = True

    def __init__(self, program: dict[tuple[str, str], _FakeResult]) -> None:
        self._program = program
        self.seen: list[tuple[str, str]] = []

    async def evaluate(
        self, prompt: str, model_id: str, *, task: TaskCategory, **_: object
    ) -> _FakeResult:
        del prompt
        # The runner passes the task's prompt here. We look up by matching the first
        # programmed result whose task matches the category — simpler than recomputing
        # the task id from the prompt, because the runner is the only caller.
        for (task_id, mid), result in self._program.items():
            if mid == model_id and result.task == task and (task_id, mid) not in self.seen:
                self.seen.append((task_id, mid))
                return result
        raise AssertionError(f"evaluator saw an unexpected pair: model={model_id} task={task}")


def _success_result(task: TaskCategory, model_id: str, version: str) -> _FakeResult:
    return _FakeResult(
        status=ObservationStatus.SUCCESS,
        model_id=model_id,
        model_version=version,
        task=task,
        answer="Answer0",
        input_tokens=4,
        output_tokens=1,
        load_ms=0.0,
        prompt_ms=0.2,
    )


def _failure_result(task: TaskCategory, model_id: str, version: str) -> _FakeResult:
    return _FakeResult(
        status=ObservationStatus.PROVIDER_FAILURE,
        model_id=model_id,
        model_version=version,
        task=task,
        error_code=ErrorCode.UNAVAILABLE,
    )


# ---------------------------------------------------------------------------
# Store + schema
# ---------------------------------------------------------------------------


class TestObservationStore:
    def test_insert_and_load_roundtrip(self, store: ObservationStore) -> None:
        observation = Observation(
            run_id="run-a",
            task_id="qa-example-0-v0",
            model_id="mock-general",
            model_version="v1",
            split="dev_train",
            task_category=TaskCategory.QA,
            feature_hash="f" * 64,
            status=ObservationStatus.SUCCESS,
            quality=1.0,
            format_valid=True,
            total_ms=1.0,
            privacy_ms=0.1,
            routing_ms=0.1,
            generation_ms=0.8,
            inspection_ms=0.0,
            resident=False,
            policy_version=POLICY_VERSION,
            mock=True,
            created_at=123.0,
        )
        store.insert(observation)
        loaded = store.load_all(run_id="run-a")
        assert len(loaded) == 1
        assert loaded[0].task_id == "qa-example-0-v0"
        assert loaded[0].quality == 1.0

    def test_duplicate_primary_key_rejected(self, store: ObservationStore) -> None:
        observation = Observation(
            run_id="run-a",
            task_id="qa-example-0-v0",
            model_id="mock-general",
            model_version="v1",
            split="dev_train",
            task_category=TaskCategory.QA,
            feature_hash="f" * 64,
            status=ObservationStatus.SUCCESS,
            total_ms=1.0,
            privacy_ms=0.1,
            routing_ms=0.1,
            generation_ms=0.8,
            inspection_ms=0.0,
            resident=False,
            policy_version=POLICY_VERSION,
            mock=True,
            created_at=123.0,
        )
        from backend.storage.connection import StorageError

        store.insert(observation)
        with pytest.raises(StorageError):
            store.insert(observation)

    def test_contains_and_completed_keys(self, store: ObservationStore) -> None:
        observation = Observation(
            run_id="run-a",
            task_id="qa-example-0-v0",
            model_id="mock-general",
            model_version="v1",
            split="dev_train",
            task_category=TaskCategory.QA,
            feature_hash="a" * 64,
            status=ObservationStatus.SUCCESS,
            total_ms=1.0,
            privacy_ms=0.1,
            routing_ms=0.1,
            generation_ms=0.8,
            inspection_ms=0.0,
            resident=False,
            policy_version=POLICY_VERSION,
            mock=True,
            created_at=123.0,
        )
        store.insert(observation)
        assert store.contains(
            run_id="run-a",
            task_id="qa-example-0-v0",
            model_id="mock-general",
            model_version="v1",
        )
        assert not store.contains(
            run_id="run-a",
            task_id="qa-example-0-v0",
            model_id="mock-general",
            model_version="v2",
        )
        assert store.completed_keys("run-a") == {("qa-example-0-v0", "mock-general", "v1")}

    def test_mismatched_schema_version_rejected(self, store: ObservationStore) -> None:
        with pytest.raises(ValidationError):
            Observation.model_validate(
                {
                    "schema_version": "observations-v99",
                    "run_id": "run-a",
                    "task_id": "qa-example-0-v0",
                    "model_id": "mock-general",
                    "model_version": "v1",
                    "split": "dev_train",
                    "task_category": "qa",
                    "feature_hash": "a" * 64,
                    "status": "success",
                    "total_ms": 1.0,
                    "privacy_ms": 0.1,
                    "routing_ms": 0.1,
                    "generation_ms": 0.8,
                    "inspection_ms": 0.0,
                    "resident": False,
                    "policy_version": POLICY_VERSION,
                    "mock": True,
                    "created_at": 1.0,
                }
            )

    def test_status_counts(self, store: ObservationStore) -> None:
        def _observation(task_id: str, status: ObservationStatus) -> Observation:
            return Observation(
                run_id="run-a",
                task_id=task_id,
                model_id="mock-general",
                model_version="v1",
                split="dev_train",
                task_category=TaskCategory.QA,
                feature_hash="a" * 64,
                status=status,
                total_ms=1.0,
                privacy_ms=0.1,
                routing_ms=0.1,
                generation_ms=0.0,
                inspection_ms=0.0,
                resident=False,
                policy_version=POLICY_VERSION,
                mock=True,
                created_at=1.0,
            )

        store.insert(_observation("task-a", ObservationStatus.SUCCESS))
        store.insert(_observation("task-b", ObservationStatus.PROVIDER_FAILURE))
        counts = store.status_counts("run-a")
        assert counts[ObservationStatus.SUCCESS] == 1
        assert counts[ObservationStatus.PROVIDER_FAILURE] == 1
        assert counts[ObservationStatus.PRIVACY_FAILURE] == 0


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class TestPlanner:
    def test_covers_all_task_candidate_pairs(
        self,
        store: ObservationStore,
        tasks: tuple[BenchmarkTask, ...],
        candidates: tuple[CandidateIdentity, ...],
    ) -> None:
        plan = plan_run(
            run_id="plan-a",
            split="dev_train",
            tasks=tasks,
            candidates=candidates,
            store=store,
        )
        assert plan.total_candidates == len(tasks) * len(candidates)
        assert plan.skipped_existing == 0
        assert plan.remaining == plan.total_candidates

    def test_limit_restricts_tasks(
        self,
        store: ObservationStore,
        tasks: tuple[BenchmarkTask, ...],
        candidates: tuple[CandidateIdentity, ...],
    ) -> None:
        plan = plan_run(
            run_id="plan-a",
            split="dev_train",
            tasks=tasks,
            candidates=candidates,
            store=store,
            limit=1,
        )
        assert plan.total_candidates == len(candidates)
        assert plan.remaining == len(candidates)

    def test_resume_skips_existing(
        self,
        store: ObservationStore,
        tasks: tuple[BenchmarkTask, ...],
        candidates: tuple[CandidateIdentity, ...],
    ) -> None:
        store.insert(
            Observation(
                run_id="plan-a",
                task_id=tasks[0].task_id,
                model_id=candidates[0].model_id,
                model_version=candidates[0].model_version,
                split="dev_train",
                task_category=tasks[0].category,
                feature_hash="a" * 64,
                status=ObservationStatus.SUCCESS,
                total_ms=1.0,
                privacy_ms=0.1,
                routing_ms=0.1,
                generation_ms=0.0,
                inspection_ms=0.0,
                resident=False,
                policy_version=POLICY_VERSION,
                mock=True,
                created_at=1.0,
            )
        )
        plan = plan_run(
            run_id="plan-a",
            split="dev_train",
            tasks=tasks,
            candidates=candidates,
            store=store,
        )
        assert plan.skipped_existing == 1
        assert plan.remaining == plan.total_candidates - 1

    def test_duplicate_candidates_rejected(
        self,
        store: ObservationStore,
        tasks: tuple[BenchmarkTask, ...],
    ) -> None:
        duplicates = (
            CandidateIdentity("mock-general", "v1"),
            CandidateIdentity("mock-general", "v1"),
        )
        with pytest.raises(ValueError):
            plan_run(
                run_id="plan-a",
                split="dev_train",
                tasks=tasks,
                candidates=duplicates,
                store=store,
            )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class TestRunner:
    def test_run_produces_one_row_per_entry(
        self,
        store: ObservationStore,
        tasks: tuple[BenchmarkTask, ...],
        candidates: tuple[CandidateIdentity, ...],
    ) -> None:
        program: dict[tuple[str, str], _FakeResult] = {}
        for task in tasks:
            for candidate in candidates:
                program[(task.task_id, candidate.model_id)] = _success_result(
                    task.category, candidate.model_id, candidate.model_version
                )
        evaluator = _FakeEvaluator(program)
        plan = plan_run(
            run_id="run-runner",
            split="dev_train",
            tasks=tasks,
            candidates=candidates,
            store=store,
        )
        summary = asyncio.run(CollectionRunner(evaluator, store).run(plan))
        assert summary.produced == len(tasks) * len(candidates)
        assert summary.failed == 0
        assert summary.by_status[ObservationStatus.SUCCESS] == summary.produced
        assert store.count(run_id="run-runner") == summary.produced

    def test_failure_is_retained_as_row(
        self,
        store: ObservationStore,
        tasks: tuple[BenchmarkTask, ...],
        candidates: tuple[CandidateIdentity, ...],
    ) -> None:
        program: dict[tuple[str, str], _FakeResult] = {}
        for task in tasks:
            for candidate in candidates:
                if candidate.model_id == "mock-specialist":
                    program[(task.task_id, candidate.model_id)] = _failure_result(
                        task.category, candidate.model_id, candidate.model_version
                    )
                else:
                    program[(task.task_id, candidate.model_id)] = _success_result(
                        task.category, candidate.model_id, candidate.model_version
                    )
        evaluator = _FakeEvaluator(program)
        plan = plan_run(
            run_id="run-mixed",
            split="dev_train",
            tasks=tasks,
            candidates=candidates,
            store=store,
        )
        summary = asyncio.run(CollectionRunner(evaluator, store).run(plan))
        assert summary.produced == len(tasks)
        assert summary.failed == len(tasks)
        rows = store.load_all(run_id="run-mixed")
        failures = [r for r in rows if r.status == ObservationStatus.PROVIDER_FAILURE]
        assert len(failures) == len(tasks)
        for row in failures:
            assert row.error_code == ErrorCode.UNAVAILABLE
            assert row.quality is None
            assert row.format_valid is None

    def test_resume_only_runs_remaining_entries(
        self,
        store: ObservationStore,
        tasks: tuple[BenchmarkTask, ...],
        candidates: tuple[CandidateIdentity, ...],
    ) -> None:
        # Seed the first pair as already done.
        store.insert(
            Observation(
                run_id="run-resume",
                task_id=tasks[0].task_id,
                model_id=candidates[0].model_id,
                model_version=candidates[0].model_version,
                split="dev_train",
                task_category=tasks[0].category,
                feature_hash="a" * 64,
                status=ObservationStatus.SUCCESS,
                quality=1.0,
                format_valid=True,
                total_ms=1.0,
                privacy_ms=0.1,
                routing_ms=0.1,
                generation_ms=0.0,
                inspection_ms=0.0,
                resident=False,
                policy_version=POLICY_VERSION,
                mock=True,
                created_at=1.0,
            )
        )
        program: dict[tuple[str, str], _FakeResult] = {}
        for task in tasks:
            for candidate in candidates:
                program[(task.task_id, candidate.model_id)] = _success_result(
                    task.category, candidate.model_id, candidate.model_version
                )
        evaluator = _FakeEvaluator(program)
        plan = plan_run(
            run_id="run-resume",
            split="dev_train",
            tasks=tasks,
            candidates=candidates,
            store=store,
        )
        expected_new = len(tasks) * len(candidates) - 1
        assert plan.remaining == expected_new
        summary = asyncio.run(CollectionRunner(evaluator, store).run(plan))
        assert summary.produced == expected_new
        assert len(evaluator.seen) == expected_new
        assert store.count(run_id="run-resume") == expected_new + 1
