"""Benchmark fixture persistence and access.

Fixtures are committed JSON files under ``evaluation/datasets``. The manifest
records split counts, category counts and split hashes; ``load_training_data``
is the single entry fitting code should use so training never touches the
validation partition. Reads reject any content whose hash disagrees with the
committed manifest.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, StrictStr

from backend.contracts import ContractModel, TaskCategory
from backend.learning.benchmarks.generators import GENERATOR_VERSION, generate_all_tasks
from backend.learning.benchmarks.splits import SplitName, partition, split_hash
from backend.learning.benchmarks.types import BENCHMARK_SCHEMA_VERSION, BenchmarkTask
from backend.versions import canonical_hash

DATASETS_DIR = Path(__file__).resolve().parents[3] / "evaluation" / "datasets"
MANIFEST_FILENAME = "manifest.json"
SPLIT_FILENAMES: Mapping[SplitName, str] = {
    "dev_train": "dev_train.json",
    "dev_validation": "dev_validation.json",
}

CategoryCounts = Mapping[TaskCategory, int]


class BenchmarkManifest(ContractModel):
    schema_version: Literal["benchmarks-v1"] = BENCHMARK_SCHEMA_VERSION
    generator_version: StrictStr = Field(min_length=1, max_length=128)
    task_count: StrictInt = Field(ge=1, le=1024)
    family_count: StrictInt = Field(ge=1, le=1024)
    train_count: StrictInt = Field(ge=0)
    validation_count: StrictInt = Field(ge=0)
    train_hash: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    validation_hash: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    tasks_hash: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    category_counts: dict[TaskCategory, StrictInt]


class BenchmarkCorruptionError(RuntimeError):
    """Raised when on-disk fixtures disagree with the committed manifest."""


def _ordered_category_counts(tasks: Iterable[BenchmarkTask]) -> dict[TaskCategory, int]:
    counts: dict[TaskCategory, int] = {category: 0 for category in TaskCategory}
    for task in tasks:
        counts[task.category] += 1
    return counts


def _tasks_hash(tasks: Iterable[BenchmarkTask]) -> str:
    hashes = sorted(task.content_hash for task in tasks)
    return canonical_hash(hashes, namespace=BENCHMARK_SCHEMA_VERSION)


def build_manifest(tasks: Iterable[BenchmarkTask]) -> BenchmarkManifest:
    materialized = tuple(tasks)
    partitions = partition(materialized)
    family_ids = {task.family_id for task in materialized}
    return BenchmarkManifest(
        generator_version=GENERATOR_VERSION,
        task_count=len(materialized),
        family_count=len(family_ids),
        train_count=len(partitions["dev_train"]),
        validation_count=len(partitions["dev_validation"]),
        train_hash=split_hash(partitions["dev_train"]),
        validation_hash=split_hash(partitions["dev_validation"]),
        tasks_hash=_tasks_hash(materialized),
        category_counts=_ordered_category_counts(materialized),
    )


def _serialize_task(task: BenchmarkTask) -> dict[str, object]:
    return task.model_dump(mode="json")


def _deserialize_task(payload: object) -> BenchmarkTask:
    if not isinstance(payload, dict):
        raise BenchmarkCorruptionError("task payload must be a mapping")
    return BenchmarkTask.model_validate(payload)


def write_fixtures(
    tasks: Iterable[BenchmarkTask],
    *,
    output_dir: Path = DATASETS_DIR,
) -> BenchmarkManifest:
    """Overwrite the fixture files and manifest atomically."""
    materialized = tuple(tasks)
    manifest = build_manifest(materialized)
    partitions = partition(materialized)
    output_dir.mkdir(parents=True, exist_ok=True)
    for split_name, filename in SPLIT_FILENAMES.items():
        payload = {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "split": split_name,
            "tasks": [_serialize_task(task) for task in partitions[split_name]],
        }
        _atomic_write_json(output_dir / filename, payload)
    _atomic_write_json(output_dir / MANIFEST_FILENAME, manifest.model_dump(mode="json"))
    return manifest


def _atomic_write_json(path: Path, payload: object) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_manifest(*, source_dir: Path = DATASETS_DIR) -> BenchmarkManifest:
    path = source_dir / MANIFEST_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BenchmarkManifest.model_validate(payload)


def load_split(
    split: SplitName,
    *,
    source_dir: Path = DATASETS_DIR,
    verify: bool = True,
) -> tuple[BenchmarkTask, ...]:
    """Load one split; verify hashes against the manifest unless ``verify=False``.

    Fitting code should call :func:`load_training_data` so the choice of split
    is explicit at the call site rather than hidden behind an argument.
    """
    filename = SPLIT_FILENAMES[split]
    raw = json.loads((source_dir / filename).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BenchmarkCorruptionError("split payload must be a mapping")
    if raw.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise BenchmarkCorruptionError("split schema version does not match")
    if raw.get("split") != split:
        raise BenchmarkCorruptionError("split identifier does not match filename")
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list):
        raise BenchmarkCorruptionError("split payload missing tasks list")
    tasks = tuple(_deserialize_task(entry) for entry in tasks_raw)
    if verify:
        manifest = load_manifest(source_dir=source_dir)
        expected_hash = manifest.train_hash if split == "dev_train" else manifest.validation_hash
        if split_hash(tasks) != expected_hash:
            raise BenchmarkCorruptionError(f"{split} hash does not match manifest")
    return tasks


def load_training_data(*, source_dir: Path = DATASETS_DIR) -> tuple[BenchmarkTask, ...]:
    """Only entry point fitting code may use; validation data is never returned."""
    return load_split("dev_train", source_dir=source_dir)


def load_evaluation_data(*, source_dir: Path = DATASETS_DIR) -> tuple[BenchmarkTask, ...]:
    """Explicit validation accessor; callers assert heldout intent at the call site."""
    return load_split("dev_validation", source_dir=source_dir)


def verify_splits(*, source_dir: Path = DATASETS_DIR) -> BenchmarkManifest:
    """Regenerate from source and confirm the committed fixtures match."""
    generated = generate_all_tasks()
    expected_manifest = build_manifest(generated)
    stored_manifest = load_manifest(source_dir=source_dir)
    if stored_manifest.model_dump(mode="json") != expected_manifest.model_dump(mode="json"):
        raise BenchmarkCorruptionError(
            "manifest disagrees with generator output; run `make fixtures`"
        )
    stored_train = load_split("dev_train", source_dir=source_dir, verify=False)
    stored_validation = load_split("dev_validation", source_dir=source_dir, verify=False)
    expected_partitions = partition(generated)
    if tuple(stored_train) != expected_partitions["dev_train"]:
        raise BenchmarkCorruptionError("dev_train content drifted from generator output")
    if tuple(stored_validation) != expected_partitions["dev_validation"]:
        raise BenchmarkCorruptionError("dev_validation content drifted from generator output")
    return stored_manifest
