"""Immutable task record for the Step 11 development benchmark.

The ``reference`` payload is category-shaped and validated by the per-category
scorer; the record itself only enforces identity and prompt-length invariants
shared with the request contract.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, StrictInt, StrictStr

from backend.contracts import ContractModel, TaskCategory
from backend.versions import canonical_hash

BENCHMARK_SCHEMA_VERSION: Literal["benchmarks-v1"] = "benchmarks-v1"

TaskId = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
FamilyId = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
Prompt = Annotated[StrictStr, Field(min_length=1, max_length=8000)]
VariantIndex = Annotated[StrictInt, Field(ge=0, le=15)]


class BenchmarkTask(ContractModel):
    schema_version: Literal["benchmarks-v1"] = BENCHMARK_SCHEMA_VERSION
    task_id: TaskId
    family_id: FamilyId
    category: TaskCategory
    variant_index: VariantIndex
    prompt: Prompt
    # Reference payloads are category-shaped and validated by the per-category
    # scorer; a recursive JSON alias would trigger Pydantic's eval-forward-ref recursion.
    reference: dict[str, Any]

    @property
    def content_hash(self) -> str:
        return canonical_hash(
            {
                "task_id": self.task_id,
                "family_id": self.family_id,
                "category": self.category.value,
                "variant_index": self.variant_index,
                "prompt": self.prompt,
                "reference": self.reference,
            },
            namespace=BENCHMARK_SCHEMA_VERSION,
        )


def make_task_id(family_id: str, variant_index: int) -> str:
    """Stable task id derived from family and variant; no secret material."""
    if variant_index < 0:
        raise ValueError("variant index must be non-negative")
    return f"{family_id}-v{variant_index}"
