"""Step 11 benchmark dataset and scoring primitives.

Only ``load_training_data`` is exposed to fitting code; validation access
requires an explicit evaluation call to keep fit/heldout separate.
"""

from backend.learning.benchmarks.splits import (
    SPLIT_SCHEMA_VERSION,
    SplitName,
    assign_split,
    family_disjoint,
    split_hash,
)
from backend.learning.benchmarks.store import (
    BenchmarkManifest,
    load_evaluation_data,
    load_manifest,
    load_split,
    load_training_data,
    write_fixtures,
)
from backend.learning.benchmarks.types import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkTask,
)

__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "BenchmarkManifest",
    "BenchmarkTask",
    "SPLIT_SCHEMA_VERSION",
    "SplitName",
    "assign_split",
    "family_disjoint",
    "load_evaluation_data",
    "load_manifest",
    "load_split",
    "load_training_data",
    "split_hash",
    "write_fixtures",
]
