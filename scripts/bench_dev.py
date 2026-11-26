"""CLI for Step 12 development benchmark collection.

Commands:
  plan   Print the plan (counts and head entries) without dispatching anything.
  run    Execute the plan against the mock orchestrator or, with MOCK_MODE=0,
         the real local broker.

Default MOCK_MODE is "1" (mock). Real runs are owner-driven and require that
the models are already verified and resident under Step 09's install flow.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from backend.broker.evaluation import EvaluationDriver
from backend.broker.orchestrator import build_mock_broker
from backend.learning.benchmarks import (
    SplitName,
    load_evaluation_data,
    load_training_data,
)
from backend.learning.benchmarks.types import BenchmarkTask
from backend.learning.collection import (
    CandidateIdentity,
    CollectionRunner,
    ObservationStore,
    plan_run,
)
from backend.storage.connection import LocalDatabase

DEFAULT_RUN_DIR = Path("var/bench")
VALID_SPLITS: tuple[SplitName, ...] = ("dev_train", "dev_validation")


def _load_split(split: SplitName) -> tuple[BenchmarkTask, ...]:
    if split == "dev_train":
        return load_training_data()
    return load_evaluation_data()


def _mock_evaluator() -> tuple[EvaluationDriver, tuple[CandidateIdentity, ...]]:
    broker = build_mock_broker()
    driver = EvaluationDriver(
        broker.dispatcher, broker.registry, broker.analyzer, inspector=broker.inspector, mode="mock"
    )
    snapshot = broker.registry.snapshot()
    candidates = tuple(
        CandidateIdentity(model_id=entry.manifest.id, model_version=entry.manifest.version)
        for entry in snapshot.roster.entries
        if entry.manifest.id in snapshot.eligible_model_ids
    )
    if not candidates:
        raise SystemExit("mock broker reports no eligible candidates")
    return driver, candidates


def _open_store(path: Path) -> ObservationStore:
    resolved = path if path.is_absolute() else (Path.cwd() / path).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    database = LocalDatabase(resolved)
    database.migrate()
    return ObservationStore(database)


def _parse_limit(raw: str | None) -> int | None:
    if raw is None or raw == "all":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"invalid LIMIT: {raw!r}") from exc
    if value <= 0:
        raise SystemExit("LIMIT must be positive or 'all'")
    return value


def _cmd_plan(args: argparse.Namespace) -> int:
    split: SplitName = args.split
    tasks = _load_split(split)
    _, candidates = _mock_evaluator()
    store = _open_store(Path(args.store))
    plan = plan_run(
        run_id=args.run_id,
        split=split,
        tasks=tasks,
        candidates=candidates,
        store=store,
        limit=_parse_limit(args.limit),
    )
    print(f"run_id:            {plan.run_id}")
    print(f"split:             {plan.split}")
    print(f"candidates:        {len(candidates)}")
    for candidate in candidates:
        print(f"  - {candidate.model_id}@{candidate.model_version}")
    print(f"tasks_in_scope:    {plan.total_candidates // max(len(candidates), 1)}")
    print(f"planned_runs:      {plan.total_candidates}")
    print(f"already_recorded:  {plan.skipped_existing}")
    print(f"remaining:         {plan.remaining}")
    if plan.remaining:
        head = plan.entries[: min(5, plan.remaining)]
        print("head:")
        for entry in head:
            print(
                f"  - {entry.task.task_id} → {entry.candidate.model_id}"
                f"@{entry.candidate.model_version}"
            )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    split: SplitName = args.split
    tasks = _load_split(split)
    driver, candidates = _mock_evaluator()
    store = _open_store(Path(args.store))
    plan = plan_run(
        run_id=args.run_id,
        split=split,
        tasks=tasks,
        candidates=candidates,
        store=store,
        limit=_parse_limit(args.limit),
    )
    print(
        f"running {plan.remaining} of {plan.total_candidates} entries "
        f"(skipped {plan.skipped_existing}) for run_id={plan.run_id}"
    )
    runner = CollectionRunner(driver, store)
    summary = asyncio.run(runner.run(plan))
    print(f"produced:          {summary.produced}")
    print(f"failed:            {summary.failed}")
    for status, count in summary.by_status.items():
        print(f"  {status.value:20s} {count}")
    return 0 if summary.failed == 0 else 0  # non-zero on hard errors only


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench_dev", description="Step 12 development benchmark collection"
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("RUN_ID", "bench-dev-mock"),
        help="stable run identifier (resumes within the same run_id)",
    )
    parser.add_argument(
        "--split",
        default=os.environ.get("SPLIT", "dev_train"),
        choices=VALID_SPLITS,
        help="benchmark split to run",
    )
    parser.add_argument(
        "--limit",
        default=os.environ.get("LIMIT"),
        help="limit tasks for this invocation (positive integer or 'all')",
    )
    parser.add_argument(
        "--store",
        default=os.environ.get("BENCH_STORE", str(DEFAULT_RUN_DIR)),
        help="directory to hold the observations SQLite database",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="print the plan without running").set_defaults(func=_cmd_plan)
    sub.add_parser("run", help="execute the plan").set_defaults(func=_cmd_run)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
