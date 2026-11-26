"""Configured OpenAI-compatible adapter with environment-only credentials.

This module does not authorize cloud usage. The future orchestrator/policy gate
must authorize every call, including synthetic warm-up, before invoking it.
"""

import asyncio
import re
from collections.abc import Mapping
from os import environ
from time import perf_counter
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from backend.api.schemas import GenerationOptions, Identifier, Version
from backend.contracts import ContractModel, ErrorCode
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.providers.base import (
    ProviderDescriptor,
    ProviderHealth,
    ProviderResult,
    ProviderSuccess,
    TokenMetrics,
)
from backend.providers.transport import (
    create_client,
    elapsed_ms,
    failure,
    status_error,
    valid_timeout,
    validate_endpoint,
)


class CloudDestination(ContractModel):
    endpoint: StrictStr
    health_endpoint: StrictStr | None = None
    model_id: Identifier
    model_version: Version
    # Optional: provider model names change often enough that pinning one here
    # ships a name that 404s later. The owner supplies it at runtime instead.
    runtime_model: (
        Annotated[StrictStr, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")]
        | None
    ) = None
    display_name: StrictStr = Field(default="Cloud provider", min_length=1, max_length=64)
    api_key_variable: Annotated[StrictStr, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")] | None = None

    @model_validator(mode="after")
    def validate_destinations(self) -> Self:
        validate_endpoint(self.endpoint, local=False)
        if urlsplit(self.endpoint).path in {"", "/"}:
            raise ValueError("Cloud endpoint must configure the exact completion path")
        if self.health_endpoint is not None:
            validate_endpoint(self.health_endpoint, local=False)
            destination = urlsplit(self.endpoint)
            health = urlsplit(self.health_endpoint)
            if (destination.scheme, destination.hostname, destination.port or 443) != (
                health.scheme,
                health.hostname,
                health.port or 443,
            ):
                raise ValueError("Cloud health and generation destinations must share an origin")
        return self


class _Upstream(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True, allow_inf_nan=False)


class _Message(_Upstream):
    content: StrictStr = Field(min_length=1, max_length=64000, repr=False)


class _Choice(_Upstream):
    message: _Message
    finish_reason: StrictStr


class _Usage(_Upstream):
    prompt_tokens: StrictInt | None = Field(default=None, ge=0)
    completion_tokens: StrictInt | None = Field(default=None, ge=0)


class _ModelEntry(_Upstream):
    id: StrictStr = Field(min_length=1, max_length=256)


class _ModelListing(_Upstream):
    data: list[_ModelEntry] = Field(default_factory=list, max_length=500)


class _Completion(_Upstream):
    model: StrictStr
    choices: tuple[_Choice, ...] = Field(min_length=1, max_length=1)
    usage: _Usage | None = None


class CloudProvider:
    def __init__(
        self,
        destination: CloudDestination,
        *,
        environment: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._destination = CloudDestination.model_validate(destination.model_dump())
        source = environ if environment is None else environment
        key_value = ""
        if destination.api_key_variable is not None:
            key_value = source.get(destination.api_key_variable, "")
        self._api_key = SecretStr(key_value)
        self._key_source: Literal["env", "runtime", "none"] = "env" if key_value else "none"
        self._runtime_model = self._destination.runtime_model
        self._client = create_client(transport)

    @property
    def destination(self) -> CloudDestination:
        return self._destination

    @property
    def key_source(self) -> "Literal['env', 'runtime', 'none']":
        return self._key_source

    @property
    def runtime_model(self) -> str | None:
        return self._runtime_model

    def set_runtime_model(self, value: str) -> None:
        """Replace the in-memory model name, validated like the configured one."""
        validated = CloudDestination.model_validate(
            {**self._destination.model_dump(), "runtime_model": value}
        )
        self._runtime_model = validated.runtime_model

    def clear_runtime_model(self) -> None:
        self._runtime_model = self._destination.runtime_model

    def set_api_key(self, value: str) -> None:
        """Replace the in-memory key. Rejects empty/non-ASCII/whitespace values."""
        if not isinstance(value, str) or not value:
            raise ValueError("Cloud API key must be a non-empty string")
        if not value.isascii() or any(char.isspace() for char in value):
            raise ValueError("Cloud API key must be ASCII without whitespace")
        if len(value) > 512:
            raise ValueError("Cloud API key exceeds 512 characters")
        self._api_key = SecretStr(value)
        self._key_source = "runtime"

    def clear_api_key(self) -> None:
        self._api_key = SecretStr("")
        self._key_source = "none"

    @classmethod
    def from_environment(
        cls,
        destination: CloudDestination,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> Self:
        return cls(destination, transport=transport)

    def __repr__(self) -> str:
        return "CloudProvider(<redacted>)"

    def describe(self, model_id: str) -> ProviderDescriptor:
        self._check_model(model_id)
        key = self._api_key.get_secret_value()
        return ProviderDescriptor(
            model_id=model_id,
            model_version=self._destination.model_version,
            route="cloud",
            endpoint=self._destination.endpoint,
            key_available=bool(key) and key.isascii() and not any(char.isspace() for char in key),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _check_model(self, model_id: str) -> None:
        if model_id != self._destination.model_id:
            raise ValueError("Unknown configured cloud model")

    def _authorization(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self._api_key.get_secret_value()}

    async def health(self, model_id: str) -> ProviderHealth:
        self._check_model(model_id)
        available = False
        endpoint = self._destination.health_endpoint
        if self._api_key.get_secret_value() and endpoint is not None:
            try:
                async with asyncio.timeout(10):
                    response = await self._client.get(
                        endpoint, headers=self._authorization(), timeout=10
                    )
                available = status_error(response) is None
            except (TimeoutError, httpx.HTTPError):
                pass
        return ProviderHealth(
            model_id=model_id, model_version=self._destination.model_version, available=available
        )

    async def available_models(self, model_id: str) -> tuple[str, ...]:
        """Model ids this key can reach, from the destination's own listing.

        Carries the key and nothing else: no prompt, payload or user data. The
        endpoint is the owner-declared health URL, already validated to share an
        origin with the generation endpoint, so this cannot reach a new host.
        Returns empty rather than raising; the caller treats that as "unknown"
        and leaves the owner free to type a name instead.
        """
        self._check_model(model_id)
        endpoint = self._destination.health_endpoint
        if not self._api_key.get_secret_value() or endpoint is None:
            return ()
        try:
            async with asyncio.timeout(10):
                response = await self._client.get(
                    endpoint, headers=self._authorization(), timeout=10
                )
            if status_error(response) is not None:
                return ()
            listing = _ModelListing.model_validate_json(response.content)
        except (TimeoutError, httpx.HTTPError, ValidationError, ValueError):
            return ()
        names = {
            item.id for item in listing.data if re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", item.id)
        }
        return tuple(sorted(names))

    async def generate(
        self,
        payload: SanitizedPayload,
        model_id: str,
        options: GenerationOptions,
        timeout_seconds: float,
    ) -> ProviderResult:
        self._check_model(model_id)
        start = perf_counter()
        version = self._destination.model_version
        if not valid_timeout(timeout_seconds):
            return failure(model_id, version, ErrorCode.INVALID_REQUEST, start)
        if not self._api_key.get_secret_value():
            return failure(model_id, version, ErrorCode.POLICY_BLOCKED, start)
        runtime_model = self._runtime_model
        if runtime_model is None:
            return failure(model_id, version, ErrorCode.UNAVAILABLE, start)
        if not isinstance(payload, SanitizedPayload) or not payload.is_trusted:
            return failure(model_id, version, ErrorCode.POLICY_BLOCKED, start)
        try:
            SanitizedPayload.model_validate(payload.model_dump())
        except (ValidationError, AttributeError):
            return failure(model_id, version, ErrorCode.POLICY_BLOCKED, start)
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await self._client.post(
                    self._destination.endpoint,
                    headers=self._authorization(),
                    json={
                        "model": runtime_model,
                        "messages": [{"role": "user", "content": payload.text}],
                        "stream": False,
                        "temperature": options.temperature,
                        "max_tokens": options.max_output_tokens,
                        **({"seed": options.seed} if options.seed is not None else {}),
                    },
                    timeout=timeout_seconds,
                )
                if code := status_error(response):
                    return failure(model_id, version, code, start)
                completion = _Completion.model_validate_json(response.content)
                if (
                    completion.model != runtime_model
                    or completion.choices[0].finish_reason != "stop"
                ):
                    return failure(model_id, version, ErrorCode.VALIDATION_FAILED, start)
                return ProviderSuccess(
                    model_id=model_id,
                    model_version=version,
                    text=completion.choices[0].message.content,
                    latency_ms=elapsed_ms(start),
                    token_metrics=TokenMetrics(
                        input_tokens=completion.usage.prompt_tokens,
                        output_tokens=completion.usage.completion_tokens,
                    )
                    if completion.usage is not None
                    else None,
                )
        except (TimeoutError, httpx.TimeoutException):
            return failure(model_id, version, ErrorCode.TIMEOUT, start)
        except httpx.HTTPError:
            return failure(model_id, version, ErrorCode.UNAVAILABLE, start)
        except ValidationError:
            return failure(model_id, version, ErrorCode.VALIDATION_FAILED, start)

    async def warm_up(
        self, synthetic_payload: SanitizedPayload, model_id: str, timeout_seconds: float
    ) -> ProviderResult:
        return await self.generate(
            synthetic_payload, model_id, GenerationOptions(max_output_tokens=1), timeout_seconds
        )
