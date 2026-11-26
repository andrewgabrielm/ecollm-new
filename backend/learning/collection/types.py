"""Typed observation record for Step 12 collection.

Observations are immutable records of one gated evaluation dispatch. Fields
that are not meaningful for a failure (quality, token counts, timings beyond
what we observed) are nullable; the status discriminates.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr

from backend.broker.feature_extractor import FeatureRow
from backend.contracts import ContractModel, ErrorCode, TaskCategory
from backend.learning.benchmarks.splits import SplitName

OBSERVATION_SCHEMA_VERSION: Literal["observations-v2"] = "observations-v2"


class ObservationStatus(StrEnum):
    SUCCESS = "success"
    VALIDATION_FAILURE = "validation_failure"
    INSPECTION_BLOCKED = "inspection_blocked"
    PROVIDER_FAILURE = "provider_failure"
    PRIVACY_FAILURE = "privacy_failure"


Quality = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
Latency = Annotated[StrictFloat, Field(ge=0.0)]
TokenCount = Annotated[StrictInt, Field(ge=0)]
RunId = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
Version = Annotated[StrictStr, Field(min_length=1, max_length=128)]
Digest = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]


class Observation(ContractModel):
    schema_version: Literal["observations-v2"] = OBSERVATION_SCHEMA_VERSION
    run_id: RunId
    task_id: Identifier
    model_id: Identifier
    model_version: Version
    split: SplitName
    task_category: TaskCategory
    feature_hash: Digest
    features: FeatureRow | None = None
    status: ObservationStatus
    error_code: ErrorCode | None = None
    quality: Quality | None = None
    format_valid: StrictBool | None = None
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    total_ms: Latency | None = None
    privacy_ms: Latency | None = None
    routing_ms: Latency | None = None
    generation_ms: Latency | None = None
    inspection_ms: Latency | None = None
    load_ms: Latency | None = None
    prompt_ms: Latency | None = None
    resident: StrictBool | None = None
    policy_version: Version
    mock: StrictBool
    created_at: Latency
