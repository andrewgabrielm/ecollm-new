"""Validated transport contracts for the Step 07 mock-first API."""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from backend.broker.validators import ResponseConstraints
from backend.contracts import (
    ContractModel,
    ErrorCode,
    ExecutionMode,
    SensitivityScope,
    TaskCategory,
)

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
Version = Annotated[str, Field(min_length=1, max_length=128)]


# One budget chain, because these were set independently and contradicted each
# other: a conversation is composed into a single text, masked, then sent to a
# model with a fixed context window, so every limit below has to fit inside the
# next. Local generation reserves MAX_OUTPUT_TOKENS of REAL_CONTEXT_TOKENS, and
# what remains has to hold the dated header, the history and the new prompt.
# Characters are converted at a deliberately pessimistic 3 per token.
PROMPT_CHARACTER_LIMIT = 8000
HISTORY_TURN_LIMIT = 20
HISTORY_CHARACTER_BUDGET = 12000
COMPOSED_HEADER_SLACK = 480
COMPOSED_CHARACTER_LIMIT = PROMPT_CHARACTER_LIMIT + HISTORY_CHARACTER_BUDGET + COMPOSED_HEADER_SLACK
DEFAULT_MAX_OUTPUT_TOKENS = 1024


class GenerationOptions(ContractModel):
    temperature: float = Field(default=0.0, ge=0, le=2, strict=True)
    max_output_tokens: StrictInt = Field(default=DEFAULT_MAX_OUTPUT_TOKENS, ge=1, le=4096)
    seed: StrictInt | None = Field(default=None, ge=0, le=2**32 - 1)


class ConversationTurn(ContractModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000, repr=False)


class ChatRequest(ContractModel):
    prompt: str = Field(min_length=1, max_length=PROMPT_CHARACTER_LIMIT, repr=False)
    history: tuple[ConversationTurn, ...] = Field(
        default=(), max_length=HISTORY_TURN_LIMIT, repr=False
    )
    mode: ExecutionMode = ExecutionMode.LOCAL_ONLY
    task_hint: TaskCategory | None = None
    options: GenerationOptions = Field(default_factory=GenerationOptions)
    sensitivity_scope: SensitivityScope = SensitivityScope.UNSPECIFIED
    constraints: ResponseConstraints = Field(default_factory=ResponseConstraints, repr=False)

    @field_validator("prompt")
    @classmethod
    def reject_blank_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must contain non-whitespace characters")
        return value

    @model_validator(mode="after")
    def bound_history_size(self) -> Self:
        # Per-turn limits still allow a transcript that overruns the local
        # context window and burns the whole local budget, so cap the total.
        if sum(len(turn.content) for turn in self.history) > HISTORY_CHARACTER_BUDGET:
            raise ValueError("history exceeds the total character budget")
        return self


class CloudFallbackReason(StrEnum):
    LOCAL_ERROR = "local_error"
    VALIDATION_FAILURE = "validation_failure"
    NO_LOCAL_MODEL = "no_local_model"
    CLOUD_PREFERRED = "cloud_preferred"
    # Only valid under ExecutionMode.CLOUD_ONLY; no local attempt was made.
    CLOUD_ONLY = "cloud_only"


class CacheStatus(StrEnum):
    DISABLED = "disabled"
    BYPASS = "bypass"
    MISS = "miss"
    EXACT_HIT = "exact_hit"
    SEMANTIC_HIT = "semantic_hit"


class CacheProvenance(ContractModel):
    model_id: Identifier
    model_version: Version
    policy_version: Version
    schema_version: Version
    source: Literal["local"] = "local"


class PrivacyTypeCount(ContractModel):
    entity_type: str = Field(pattern=r"^[A-Z][A-Z_]{0,63}$")
    count: StrictInt = Field(ge=1)


class ResponseTimings(ContractModel):
    total_ms: float = Field(ge=0, strict=True)
    privacy_ms: float | None = Field(default=None, ge=0, strict=True)
    routing_ms: float | None = Field(default=None, ge=0, strict=True)
    generation_ms: float | None = Field(default=None, ge=0, strict=True)


class GenerationMetrics(ContractModel):
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    runtime_total_ms: float | None = Field(default=None, ge=0, strict=True)
    load_ms: float | None = Field(default=None, ge=0, strict=True)
    prompt_ms: float | None = Field(default=None, ge=0, strict=True)
    generation_ms: float | None = Field(default=None, ge=0, strict=True)
    output_tokens_per_second: float | None = Field(default=None, ge=0, strict=True)


class ChatResponse(ContractModel):
    request_id: UUID
    status: Literal["success"] = "success"
    answer: str = Field(min_length=1, max_length=64000, repr=False)
    route: Literal["local", "cloud"]
    model_id: Identifier
    model_version: Version
    reason: Identifier
    privacy_counts: tuple[PrivacyTypeCount, ...] = ()
    restored: StrictBool = False
    cache_status: CacheStatus = CacheStatus.DISABLED
    cache_provenance: CacheProvenance | None = None
    timings: ResponseTimings
    estimated_cost_usd: float | None = Field(default=None, ge=0, strict=True)
    manifest_version: Version | None = None
    task: TaskCategory | None = None
    router_type: Literal["rules", "lightgbm"] = "rules"
    router_version: Version | None = None
    mock: StrictBool = False
    generation_metrics: GenerationMetrics | None = None

    @model_validator(mode="after")
    def validate_cache_provenance(self) -> Self:
        is_hit = self.cache_status in {CacheStatus.EXACT_HIT, CacheStatus.SEMANTIC_HIT}
        if is_hit:
            if self.route != "local" or self.cache_provenance is None:
                raise ValueError("cache hits require local answer provenance")
            if (
                self.cache_provenance.model_id != self.model_id
                or self.cache_provenance.model_version != self.model_version
            ):
                raise ValueError("cache provenance must match the producing model/version")
        elif self.cache_provenance is not None:
            raise ValueError("cache provenance is only valid for a cache hit")
        if len({item.entity_type for item in self.privacy_counts}) != len(self.privacy_counts):
            raise ValueError("privacy type counts must be unique")
        return self


class ApprovalRequiredResponse(ContractModel):
    request_id: UUID
    status: Literal["approval_required"] = "approval_required"
    sanitized_preview: str = Field(min_length=1, max_length=64000, repr=False)
    expires_at: AwareDatetime
    reason: CloudFallbackReason
    best_local_answer: str | None = Field(default=None, max_length=64000, repr=False)
    proposal_id: UUID | None = None
    digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    mode: ExecutionMode = ExecutionMode.ASK_BEFORE_CLOUD
    model_id: Identifier | None = None
    model_version: Version | None = None
    policy_version: Version | None = None


class ErrorResponse(ContractModel):
    request_id: UUID
    status: Literal["error"] = "error"
    error_code: ErrorCode
    retryable: StrictBool = False
    answer: (
        Literal["[Response withheld: privacy inspection could not confirm safe output.]"] | None
    ) = Field(default=None, repr=False)


class ApprovalRequest(ContractModel):
    proposal_id: UUID
    decision: Literal["approve", "decline", "cancel"]
    sanitized_preview: str | None = Field(default=None, min_length=1, max_length=64000, repr=False)
    digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def approval_requires_exact_preview(self) -> Self:
        if self.decision == "approve" and (self.sanitized_preview is None or self.digest is None):
            raise ValueError("Approval requires the exact preview and digest")
        return self


class CancelledResponse(ContractModel):
    request_id: UUID
    status: Literal["cancelled"] = "cancelled"
    reason: Literal["decline", "cancel"]


class HealthResponse(ContractModel):
    status: Literal["ok", "degraded"]
    mode: Literal["mock", "injected", "real"]
    privacy_analyzer: str
    eligible_local_models: StrictInt = Field(ge=0, le=3)
    worker_limit: Literal[1] = 1
    notice: Literal["ollama_unavailable_using_mock"] | None = None


class PreferencesResponse(ContractModel):
    exact_cache_enabled: StrictBool = False
    persistent_analytics_enabled: StrictBool = False
    local_learning_enabled: StrictBool = False
    workload_persistence_enabled: StrictBool = False
    mode: ExecutionMode = ExecutionMode.LOCAL_ONLY


class PreferencesUpdateRequest(ContractModel):
    exact_cache_enabled: StrictBool | None = None
    persistent_analytics_enabled: StrictBool | None = None
    local_learning_enabled: StrictBool | None = None
    workload_persistence_enabled: StrictBool | None = None


class CatalogModel(ContractModel):
    model_id: Identifier
    manifest_version: Version
    family: StrictStr
    quantization: StrictStr
    verified: StrictBool
    installed: StrictBool
    resident: StrictBool | None = None
    eligible: StrictBool = False


class CatalogResponse(ContractModel):
    models: tuple[CatalogModel, ...] = ()
    hardware_known: StrictBool = False
    mock_mode: StrictBool = False


class CloudDestinationInfo(ContractModel):
    model_id: Identifier
    model_version: Version
    display_name: StrictStr = Field(min_length=1, max_length=64)
    endpoint: StrictStr
    runtime_model: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    key_available: StrictBool
    key_source: Literal["env", "runtime", "none"]


class CloudDestinationsResponse(ContractModel):
    destinations: tuple[CloudDestinationInfo, ...] = ()


class CloudApiKeyUpdate(ContractModel):
    api_key: StrictStr = Field(min_length=1, max_length=512, repr=False)


class CloudPresetInfo(ContractModel):
    model_id: Identifier
    display_name: StrictStr = Field(min_length=1, max_length=64)
    endpoint: StrictStr
    health_endpoint: StrictStr
    note: StrictStr = ""


class CloudPresetsResponse(ContractModel):
    presets: tuple[CloudPresetInfo, ...] = ()


class CloudDestinationCreate(ContractModel):
    model_id: Identifier
    display_name: StrictStr = Field(min_length=1, max_length=64)
    endpoint: StrictStr = Field(min_length=1, max_length=2048)
    health_endpoint: StrictStr | None = Field(default=None, max_length=2048)


class CloudModelOptions(ContractModel):
    model_id: Identifier
    runtime_model: StrictStr | None = None
    models: tuple[StrictStr, ...] = ()
    reachable: StrictBool = False


class CloudModelUpdate(ContractModel):
    runtime_model: StrictStr = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")


class ConversationSummary(ContractModel):
    id: UUID
    title: StrictStr = Field(min_length=1, max_length=80, repr=False)
    created_at: float = Field(ge=0, strict=True)
    updated_at: float = Field(ge=0, strict=True)
    message_count: StrictInt = Field(ge=0)


class ConversationListResponse(ContractModel):
    enabled: StrictBool = False
    conversations: tuple[ConversationSummary, ...] = ()


class ConversationMessage(ContractModel):
    role: Literal["user", "assistant"]
    content: StrictStr = Field(min_length=1, max_length=64000, repr=False)
    model_id: Identifier | None = None


class ConversationDetail(ContractModel):
    id: UUID
    messages: tuple[ConversationMessage, ...] = ()


class ConversationSaveRequest(ContractModel):
    id: UUID
    messages: tuple[ConversationMessage, ...] = Field(min_length=1, max_length=500)


class CacheClearedResponse(ContractModel):
    deleted: StrictInt = Field(ge=0)


class MetricsResponse(ContractModel):
    status: Literal["ephemeral_only"] = "ephemeral_only"
    requests: StrictInt = Field(ge=0)
    completed: StrictInt = Field(ge=0)
    errors: StrictInt = Field(ge=0)
    pending_approvals: StrictInt = Field(ge=0)
    cache_enabled: StrictBool = False
    cache_hits: StrictInt = Field(default=0, ge=0)
    cache_entries: StrictInt = Field(default=0, ge=0)
    persistence_enabled: Literal[False] = False
    learned_router_enabled: Literal[False] = False
    estimated_cost_usd: None = None
