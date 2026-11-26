"""Step 11 generator, split and manifest invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.contracts import TaskCategory
from backend.learning.benchmarks import (
    BenchmarkManifest,
    BenchmarkTask,
    assign_split,
    load_evaluation_data,
    load_manifest,
    load_split,
    load_training_data,
    split_hash,
    write_fixtures,
)
from backend.learning.benchmarks.generators import generate_all_tasks
from backend.learning.benchmarks.splits import family_disjoint, partition
from backend.learning.benchmarks.store import (
    DATASETS_DIR,
    BenchmarkCorruptionError,
    build_manifest,
    verify_splits,
)


@pytest.fixture(scope="module")
def tasks() -> tuple[BenchmarkTask, ...]:
    return generate_all_tasks()


@pytest.fixture()
def fresh_datasets_dir(tmp_path: Path) -> Path:
    tasks = generate_all_tasks()
    out = tmp_path / "datasets"
    write_fixtures(tasks, output_dir=out)
    return out


class TestGeneration:
    def test_total_count_in_plan_range(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        assert len(tasks) == 300

    def test_covers_every_task_category(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        present = {task.category for task in tasks}
        assert present == set(TaskCategory)

    def test_task_ids_unique(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        ids = [task.task_id for task in tasks]
        assert len(ids) == len(set(ids))

    def test_family_ids_grouped(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        families: dict[str, set[TaskCategory]] = {}
        for task in tasks:
            families.setdefault(task.family_id, set()).add(task.category)
        # Every family stays in a single category.
        for family, categories in families.items():
            assert len(categories) == 1, family

    def test_prompts_are_unique(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        # Paraphrase variants vary wording within families, so no two task prompts
        # should collide byte-for-byte.
        prompts = [task.prompt for task in tasks]
        assert len(prompts) == len(set(prompts))

    def test_regeneration_is_deterministic(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        again = generate_all_tasks()
        assert tasks == again


class TestSplits:
    def test_family_disjoint(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        assert family_disjoint(tasks)

    def test_counts_match_plan_targets(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        partitions = partition(tasks)
        assert len(partitions["dev_train"]) + len(partitions["dev_validation"]) == 300
        # Allow modest drift around the ~200/~100 target; both splits must be non-empty.
        assert 170 <= len(partitions["dev_train"]) <= 230
        assert 70 <= len(partitions["dev_validation"]) <= 130

    def test_split_assignment_is_stable_for_fixed_family_id(self) -> None:
        for family in ("qa-capital-france", "code-fib", "format-greeting"):
            assert assign_split(family) == assign_split(family)

    def test_no_family_crosses_splits(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        partitions = partition(tasks)
        train_families = {task.family_id for task in partitions["dev_train"]}
        validation_families = {task.family_id for task in partitions["dev_validation"]}
        assert train_families.isdisjoint(validation_families)

    def test_split_hash_is_deterministic(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        partitions = partition(tasks)
        hash_one = split_hash(partitions["dev_train"])
        hash_two = split_hash(tuple(reversed(partitions["dev_train"])))
        assert hash_one == hash_two

    def test_split_hashes_differ(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        partitions = partition(tasks)
        assert split_hash(partitions["dev_train"]) != split_hash(partitions["dev_validation"])


class TestManifest:
    def test_manifest_matches_generation(self, tasks: tuple[BenchmarkTask, ...]) -> None:
        manifest = build_manifest(tasks)
        assert manifest.task_count == len(tasks)
        counts = {cat.value: count for cat, count in manifest.category_counts.items()}
        total = sum(counts.values())
        assert total == manifest.task_count
        assert manifest.train_count + manifest.validation_count == manifest.task_count
        assert manifest.family_count == len({task.family_id for task in tasks})


class TestPersistence:
    def test_write_then_load_roundtrip(self, fresh_datasets_dir: Path) -> None:
        manifest = load_manifest(source_dir=fresh_datasets_dir)
        train = load_split("dev_train", source_dir=fresh_datasets_dir)
        validation = load_split("dev_validation", source_dir=fresh_datasets_dir)
        assert len(train) == manifest.train_count
        assert len(validation) == manifest.validation_count

    def test_verify_splits_ok(self, fresh_datasets_dir: Path) -> None:
        manifest = verify_splits(source_dir=fresh_datasets_dir)
        assert isinstance(manifest, BenchmarkManifest)

    def test_tampered_split_hash_rejected(self, fresh_datasets_dir: Path) -> None:
        manifest_path = fresh_datasets_dir / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["train_hash"] = "0" * 64
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(BenchmarkCorruptionError):
            load_split("dev_train", source_dir=fresh_datasets_dir)

    def test_mismatched_split_identifier_rejected(self, fresh_datasets_dir: Path) -> None:
        path = fresh_datasets_dir / "dev_train.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["split"] = "dev_validation"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(BenchmarkCorruptionError):
            load_split("dev_train", source_dir=fresh_datasets_dir, verify=False)

    def test_training_data_is_dev_train_only(self, fresh_datasets_dir: Path) -> None:
        train = load_training_data(source_dir=fresh_datasets_dir)
        validation = load_evaluation_data(source_dir=fresh_datasets_dir)
        train_families = {task.family_id for task in train}
        validation_families = {task.family_id for task in validation}
        assert train_families.isdisjoint(validation_families)


class TestCommittedFixtures:
    def test_committed_dataset_matches_generators(self) -> None:
        if not (DATASETS_DIR / "manifest.json").exists():
            pytest.skip("committed fixtures absent; run `make fixtures`")
        manifest = verify_splits(source_dir=DATASETS_DIR)
        assert manifest.task_count == 300
        assert manifest.family_count == 100
