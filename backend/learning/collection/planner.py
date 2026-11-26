"""Build the ordered list of (task, candidate) pairs the collector will run.

The planner is pure: it combines the benchmark corpus and the current roster,
then subtracts whatever rows already exist for ``run_id``. That gives a
deterministic, resume-friendly plan without touching the dispatcher.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from backend.learning.benchmarks.splits import SplitName
from backend.learning.benchmarks.types import BenchmarkTask
from backend.learning.collection.store import ObservationStore


@dataclass(frozen=True)
class CandidateIdentity:
    model_id: str
    model_version: str

    def key(self) -> tuple[str, str]:
        return (self.model_id, self.model_version)


@dataclass(frozen=True)
class PlanEntry:
    task: BenchmarkTask
    candidate: CandidateIdentity
    split: SplitName


@dataclass(frozen=True)
class CollectionPlan:
    run_id: str
    split: SplitName
    entries: tuple[PlanEntry, ...]
    skipped_existing: int
    total_candidates: int

    @property
    def remaining(self) -> int:
        return len(self.entries)


def plan_run(
    *,
    run_id: str,
    split: SplitName,
    tasks: Sequence[BenchmarkTask],
    candidates: Sequence[CandidateIdentity],
    store: ObservationStore,
    limit: int | None = None,
) -> CollectionPlan:
    if not tasks:
        raise ValueError("no tasks supplied for collection plan")
    if not candidates:
        raise ValueError("no candidates supplied for collection plan")
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    seen_keys = {(cid.model_id, cid.model_version) for cid in candidates}
    if len(seen_keys) != len(candidates):
        raise ValueError("duplicate candidate identities")

    done = store.completed_keys(run_id)
    tasks_in_scope = list(tasks[:limit]) if limit is not None else list(tasks)
    entries: list[PlanEntry] = []
    skipped = 0
    for task in tasks_in_scope:
        for candidate in candidates:
            key = (task.task_id, candidate.model_id, candidate.model_version)
            if key in done:
                skipped += 1
                continue
            entries.append(PlanEntry(task=task, candidate=candidate, split=split))
    total_candidates = len(tasks_in_scope) * len(candidates)
    return CollectionPlan(
        run_id=run_id,
        split=split,
        entries=tuple(entries),
        skipped_existing=skipped,
        total_candidates=total_candidates,
    )


def candidates_from_identities(items: Iterable[tuple[str, str]]) -> tuple[CandidateIdentity, ...]:
    return tuple(CandidateIdentity(model_id=mid, model_version=ver) for mid, ver in items)
