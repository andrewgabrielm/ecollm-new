"""Provider and runtime-control protocols; no adapters or dispatch implementation."""

from typing import Annotated, Literal, Protocol, Self
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from backend.api.schemas import GenerationOptions, Identifier, Version
from backend.config.endpoints import validate_endpoint
from backend.contracts import ContractModel, ErrorCode
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.versions import canonical_hash


class ProviderDescriptor(ContractModel):
    """Current provider configuration, containing key presence only, never its value."""

    model_id: Identifier
    model_version: Version
    route: Literal["local", "cloud"]
    endpoint: StrictStr
    key_available: StrictBool = False

    @model_validator(mode="after")
    def validate_destination(self) -> Self:
        normalized = validate_endpoint(self.endpoint, local=self.route == "local")
        if normalized != self.endpoint:
            raise ValueError("Provider descriptor requires a canonical destination")
        if self.route == "cloud" and urlsplit(self.endpoint).path in {"", "/"}:
            raise ValueError("Cloud target requires an exact generation path")
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_hash(
            self.model_dump(mode="json", exclude={"key_available"}),
            namespace="generation-target-v1",
        )


class TokenMetrics(ContractModel):
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)


class RuntimeMetrics(ContractModel):
    total_ms: float | None = Field(default=None, ge=0, strict=True)
    load_ms: float | None = Field(default=None, ge=0, strict=True)
    prompt_ms: float | None = Field(default=None, ge=0, strict=True)
    generation_ms: float | None = Field(default=None, ge=0, strict=True)


class ProviderSuccess(ContractModel):
    status: Literal["success"] = "success"
    model_id: Identifier
    model_version: Version
    text: str = Field(min_length=1, max_length=64000, repr=False)
    latency_ms: float = Field(ge=0, strict=True)
    token_metrics: TokenMetrics | None = None
    runtime_metrics: RuntimeMetrics | None = None


class ProviderFailure(ContractModel):
    status: Literal["error"] = "error"
    model_id: Identifier
    model_version: Version
    error_code: ErrorCode
    latency_ms: float = Field(ge=0, strict=True)
    retryable_connection: StrictBool = False

    @model_validator(mode="after")
    def validate_retry_kind(self) -> Self:
        if self.retryable_connection and self.error_code not in {
            ErrorCode.UNAVAILABLE,
            ErrorCode.TIMEOUT,
        }:
            raise ValueError("Only connection failures can request a local retry")
        return self


ProviderResult = Annotated[ProviderSuccess | ProviderFailure, Field(discriminator="status")]


class ProviderHealth(ContractModel):
    available: StrictBool
    model_id: Identifier
    model_version: Version


class RuntimeModel(ContractModel):
    model_id: Identifier
    model_version: Version
    resident: StrictBool | None = None


class RuntimeOperationResult(ContractModel):
    status: Literal["success", "error"]
    model_id: Identifier
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def validate_error_code(self) -> Self:
        if (self.status == "error") != (self.error_code is not None):
            raise ValueError("runtime operation status and error code disagree")
        return self


class Provider(Protocol):
    """Generation methods are internal to the future gated orchestrator."""

    def describe(self, model_id: str) -> ProviderDescriptor: ...

    async def generate(
        self,
        payload: SanitizedPayload,
        model_id: str,
        options: GenerationOptions,
        timeout_seconds: float,
    ) -> ProviderResult: ...

    async def health(self, model_id: str) -> ProviderHealth: ...

    async def warm_up(
        self,
        synthetic_payload: SanitizedPayload,
        model_id: str,
        timeout_seconds: float,
    ) -> ProviderResult: ...


class RuntimeControl(Protocol):
    """Control plane only; approval/manifest checking belongs to lifecycle manager."""

    async def list_models(self) -> tuple[RuntimeModel, ...]: ...

    async def pull(self, manifest_id: str, pinned_digest: str) -> RuntimeOperationResult: ...

    async def unload(self, model_id: str) -> RuntimeOperationResult: ...

    async def remove(self, model_id: str) -> RuntimeOperationResult: ...
