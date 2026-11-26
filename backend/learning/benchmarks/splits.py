"""Family-aware deterministic split assignment and hashing.

Paraphrase families are placed whole into exactly one split so train/validation
never share source templates. Bucketing uses the canonical hash so assignment
depends only on the family id and the split namespace.
"""

from collections.abc import Iterable, Mapping
from typing import Literal

from backend.learning.benchmarks.types import BenchmarkTask
from backend.versions import canonical_hash

SPLIT_SCHEMA_VERSION: Literal["benchmarks-split-v1"] = "benchmarks-split-v1"

SplitName = Literal["dev_train", "dev_validation"]
_SPLIT_NAMES: tuple[SplitName, ...] = ("dev_train", "dev_validation")

# 7 of 10 buckets go to training, 3 to validation (≈200/100 of ~300 tasks).
_TRAIN_BUCKETS = 7
_TOTAL_BUCKETS = 10


def assign_split(family_id: str) -> SplitName:
    if not family_id:
        raise ValueError("family id is required")
    digest = canonical_hash(family_id, namespace=SPLIT_SCHEMA_VERSION)
    bucket = int(digest[:8], 16) % _TOTAL_BUCKETS
    return "dev_train" if bucket < _TRAIN_BUCKETS else "dev_validation"


def family_disjoint(tasks: Iterable[BenchmarkTask]) -> bool:
    """Return True when every family maps to a single split."""
    seen: dict[str, SplitName] = {}
    for task in tasks:
        split = assign_split(task.family_id)
        prior = seen.setdefault(task.family_id, split)
        if prior != split:
            return False
    return True


def partition(tasks: Iterable[BenchmarkTask]) -> Mapping[SplitName, tuple[BenchmarkTask, ...]]:
    """Partition tasks by split name; task order is preserved within each split."""
    buckets: dict[SplitName, list[BenchmarkTask]] = {name: [] for name in _SPLIT_NAMES}
    for task in tasks:
        buckets[assign_split(task.family_id)].append(task)
    return {name: tuple(buckets[name]) for name in _SPLIT_NAMES}


def split_hash(tasks: Iterable[BenchmarkTask]) -> str:
    """Canonical hash of a split; invariant under file ordering."""
    hashes = sorted(task.content_hash for task in tasks)
    return canonical_hash(hashes, namespace=SPLIT_SCHEMA_VERSION)
