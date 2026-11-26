"""Deterministic synthetic-only provider; counters contain no request content."""

import asyncio
import json
from collections import Counter
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictStr

from backend.api.schemas import GenerationOptions, Version
from backend.contracts import ContractModel, ErrorCode
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.providers.base import (
    ProviderDescriptor,
    ProviderFailure,
    ProviderHealth,
    ProviderResult,
    ProviderSuccess,
)
from backend.providers.transport import valid_timeout, validate_model_ids


class MockModel(ContractModel):
    response: StrictStr = Field(min_length=1, max_length=64000, repr=False)
    version: Version = "synthetic-v1"
    available: StrictBool = True
    failure: ErrorCode | None = None
    latency_ms: Annotated[float, Field(ge=0, le=120_000, strict=True)] = 0
    response_mode: Literal["fixed", "echo", "json_echo"] = "fixed"


class MockProvider:
    def __init__(
        self,
        models: dict[str, MockModel],
        *,
        route: Literal["local", "cloud"] = "local",
        endpoint: str | None = None,
        key_available: bool = False,
    ) -> None:
        validate_model_ids(list(models))
        self._models = dict(models)
        self._test_calls: Counter[str] = Counter()
        self._route = route
        self._endpoint = endpoint or (
            "http://127.0.0.1:11434"
            if route == "local"
            else "https://mock.invalid/v1/chat/completions"
        )
        self._key_available = key_available
        for model_id in self._models:
            self.describe(model_id)

    def __repr__(self) -> str:
        return "MockProvider(<synthetic>)"

    def describe(self, model_id: str) -> ProviderDescriptor:
        model = self._configured(model_id)
        return ProviderDescriptor(
            model_id=model_id,
            model_version=model.version,
            route=self._route,
            endpoint=self._endpoint,
            key_available=self._key_available,
        )

    @property
    def test_call_counts(self) -> Mapping[str, int]:
        """Test instrumentation only; no prompts, payloads or outputs are retained."""
        return MappingProxyType(dict(self._test_calls))

    async def health(self, model_id: str) -> ProviderHealth:
        model = self._configured(model_id)
        return ProviderHealth(
            model_id=model_id, model_version=model.version, available=model.available
        )

    def _configured(self, model_id: str) -> MockModel:
        if model_id not in self._models:
            raise ValueError("Unknown configured mock model")
        return self._models[model_id]

    async def generate(
        self,
        payload: SanitizedPayload,
        model_id: str,
        options: GenerationOptions,
        timeout_seconds: float,
    ) -> ProviderResult:
        model = self._configured(model_id)
        code: ErrorCode | None = None
        if not isinstance(payload, SanitizedPayload) or not payload.is_trusted:
            code = ErrorCode.POLICY_BLOCKED
        elif not valid_timeout(timeout_seconds):
            code = ErrorCode.INVALID_REQUEST
        elif not model.available:
            code = ErrorCode.UNAVAILABLE
        if code is not None:
            return ProviderFailure(
                model_id=model_id, model_version=model.version, error_code=code, latency_ms=0
            )
        self._test_calls[model_id] += 1
        if model.latency_ms > timeout_seconds * 1000:
            return ProviderFailure(
                model_id=model_id,
                model_version=model.version,
                error_code=ErrorCode.TIMEOUT,
                latency_ms=timeout_seconds * 1000,
            )
        await asyncio.sleep(0)  # Yield fairly; configured latency is simulated, never slept.
        if model.failure is not None:
            return ProviderFailure(
                model_id=model_id,
                model_version=model.version,
                error_code=model.failure,
                latency_ms=model.latency_ms,
            )
        return ProviderSuccess(
            model_id=model_id,
            model_version=model.version,
            text=(
                payload.text
                if model.response_mode == "echo"
                else json.dumps({"result": payload.text})
                if model.response_mode == "json_echo"
                else model.response
            ),
            latency_ms=model.latency_ms,
        )

    async def warm_up(
        self, synthetic_payload: SanitizedPayload, model_id: str, timeout_seconds: float
    ) -> ProviderResult:
        return await self.generate(
            synthetic_payload, model_id, GenerationOptions(max_output_tokens=1), timeout_seconds
        )
