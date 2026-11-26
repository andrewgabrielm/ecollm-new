"""Step 12 per-candidate development observations.

Collector drives the gated evaluation dispatch over the benchmark corpus and
persists one :class:`Observation` row per ``(run_id, task_id, model_id,
model_version)``. Observations are the training input for the Step 13
LightGBM router.
"""

from backend.learning.collection.planner import (
    CandidateIdentity,
    CollectionPlan,
    PlanEntry,
    plan_run,
)
from backend.learning.collection.runner import CollectionRunner, RunSummary
from backend.learning.collection.store import ObservationStore
from backend.learning.collection.types import (
    OBSERVATION_SCHEMA_VERSION,
    Observation,
    ObservationStatus,
)

__all__ = [
    "CandidateIdentity",
    "CollectionPlan",
    "CollectionRunner",
    "OBSERVATION_SCHEMA_VERSION",
    "Observation",
    "ObservationStatus",
    "ObservationStore",
    "PlanEntry",
    "RunSummary",
    "plan_run",
]
