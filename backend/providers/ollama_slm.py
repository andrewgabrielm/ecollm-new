"""Loopback Ollama adapter; only the future guarded orchestrator may dispatch it."""

import asyncio
import hashlib
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from backend.api.schemas import GenerationOptions
from backend.contracts import ContractModel, ErrorCode
from backend.model_management.hardware import HardwareReport, resource_reason
from backend.model_management.initial_install import (
    InitialInstallError,
    InstallPlan,
    trusted_manifest,
)
from backend.model_management.manifest import ModelCatalog, ModelManifest
from backend.model_management.registry import RuntimeInventory, RuntimeObservation
from backend.model_management.runtime_report import RuntimeReport
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.providers.base import (
    ProviderDescriptor,
    ProviderHealth,
    ProviderResult,
    ProviderSuccess,
    RuntimeMetrics,
    RuntimeModel,
    RuntimeOperationResult,
    TokenMetrics,
)
from backend.providers.transport import (
    ProviderTransportError,
    create_client,
    elapsed_ms,
    failure,
    status_error,
    valid_timeout,
    validate_endpoint,
    validate_model_ids,
)
from backend.versions import canonical_hash


class OllamaModel(ContractModel):
    runtime_tag: Annotated[
        StrictStr,
        Field(
            pattern=r"^[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*:[A-Za-z0-9][A-Za-z0-9._-]*$"
        ),
    ]
    pinned_digest: Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]


class _Upstream(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True, allow_inf_nan=False)


class _ListedModel(_Upstream):
    name: StrictStr
    digest: Annotated[StrictStr, Field(pattern=r"^(?:sha256:)?[a-f0-9]{64}$")]


class _ModelList(_Upstream):
    models: tuple[_ListedModel, ...]


class _Generation(_Upstream):
    model: StrictStr
    response: StrictStr = Field(min_length=1, max_length=64000, repr=False)
    done: StrictBool
    total_duration: StrictInt | None = Field(default=None, ge=0)
    load_duration: StrictInt | None = Field(default=None, ge=0)
    prompt_eval_duration: StrictInt | None = Field(default=None, ge=0)
    eval_duration: StrictInt | None = Field(default=None, ge=0)
    prompt_eval_count: StrictInt | None = Field(default=None, ge=0)
    eval_count: StrictInt | None = Field(default=None, ge=0)


class OllamaProvider:
    def __init__(
        self,
        models: dict[str, OllamaModel],
        *,
        endpoint: str = "http://127.0.0.1:11434",
        transport: httpx.AsyncBaseTransport | None = None,
        preflight: Callable[[str], Awaitable[bool]] | None = None,
        keep_alive_seconds: int | None = None,
        context_tokens: int | None = None,
    ) -> None:
        validate_model_ids(list(models))
        self._models = dict(models)
        self._endpoint = validate_endpoint(endpoint, local=True)
        self._client = create_client(transport)
        self._preflight = preflight
        if keep_alive_seconds is not None and (
            type(keep_alive_seconds) is not int or not 0 <= keep_alive_seconds <= 60
        ):
            raise ValueError("Invalid local residency duration")
        self._keep_alive = keep_alive_seconds
        # Upper bound guards against a misconfiguration sizing the KV cache out
        # of memory; it is not the working value. A conversation has to fit the
        # composed prompt plus its output reserve, which 4096 could not hold.
        if context_tokens is not None and (
            type(context_tokens) is not int or not 512 <= context_tokens <= 16384
        ):
            raise ValueError("Invalid local context bound")
        self._context_tokens = context_tokens

    async def aclose(self) -> None:
        await self._client.aclose()

    def __repr__(self) -> str:
        return "OllamaProvider(<configured>)"

    def describe(self, model_id: str) -> ProviderDescriptor:
        model = self._configured(model_id)
        return ProviderDescriptor(
            model_id=model_id,
            model_version=model.pinned_digest,
            route="local",
            endpoint=self._endpoint,
        )

    async def _listed_models(self, timeout: float = 10) -> _ModelList:
        try:
            response = await self._client.get(f"{self._endpoint}/api/tags", timeout=timeout)
            if code := status_error(response):
                raise ProviderTransportError(code)
            return _ModelList.model_validate_json(response.content)
        except httpx.ConnectTimeout:
            raise ProviderTransportError(ErrorCode.TIMEOUT, retryable_connection=True) from None
        except httpx.ConnectError:
            raise ProviderTransportError(ErrorCode.UNAVAILABLE, retryable_connection=True) from None
        except httpx.TimeoutException:
            raise ProviderTransportError(ErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            raise ProviderTransportError(ErrorCode.UNAVAILABLE) from None
        except ValidationError:
            raise ProviderTransportError(ErrorCode.VALIDATION_FAILED) from None

    async def health(self, model_id: str) -> ProviderHealth:
        model = self._configured(model_id)
        try:
            listed = await self._listed_models()
            available = self._matches(listed, model)
        except ProviderTransportError:
            available = False
        return ProviderHealth(
            model_id=model_id, model_version=model.pinned_digest, available=available
        )

    def _configured(self, model_id: str) -> OllamaModel:
        if model_id not in self._models:
            raise ValueError("Unknown configured model")
        return self._models[model_id]

    @staticmethod
    def _matches(listed: _ModelList, model: OllamaModel) -> bool:
        return any(
            item.name == model.runtime_tag
            and item.digest.removeprefix("sha256:") == model.pinned_digest
            for item in listed.models
        )

    async def generate(
        self,
        payload: SanitizedPayload,
        model_id: str,
        options: GenerationOptions,
        timeout_seconds: float,
    ) -> ProviderResult:
        model = self._configured(model_id)
        start = perf_counter()
        if not valid_timeout(timeout_seconds):
            return failure(model_id, model.pinned_digest, ErrorCode.INVALID_REQUEST, start)
        if not isinstance(payload, SanitizedPayload) or not payload.is_trusted:
            return failure(model_id, model.pinned_digest, ErrorCode.POLICY_BLOCKED, start)
        # Revalidate even model_copy/model_construct cannot smuggle altered payloads.
        try:
            SanitizedPayload.model_validate(payload.model_dump())
        except (ValidationError, AttributeError):
            return failure(model_id, model.pinned_digest, ErrorCode.POLICY_BLOCKED, start)
        try:
            async with asyncio.timeout(timeout_seconds):
                if self._preflight is not None and not await self._preflight(model_id):
                    return failure(model_id, model.pinned_digest, ErrorCode.UNAVAILABLE, start)
                listed = await self._listed_models(timeout_seconds)
                if not self._matches(listed, model):
                    return failure(model_id, model.pinned_digest, ErrorCode.UNAVAILABLE, start)
                response = await self._client.post(
                    f"{self._endpoint}/api/generate",
                    json={
                        "model": model.runtime_tag,
                        "prompt": payload.text,
                        "stream": False,
                        **(
                            {"keep_alive": self._keep_alive} if self._keep_alive is not None else {}
                        ),
                        "options": {
                            "temperature": options.temperature,
                            "num_predict": options.max_output_tokens,
                            **(
                                {"num_ctx": self._context_tokens}
                                if self._context_tokens is not None
                                else {}
                            ),
                            **({"seed": options.seed} if options.seed is not None else {}),
                        },
                    },
                    timeout=timeout_seconds,
                )
                if code := status_error(response):
                    return failure(model_id, model.pinned_digest, code, start)
                generated = _Generation.model_validate_json(response.content)
                if not generated.done or generated.model != model.runtime_tag:
                    return failure(
                        model_id, model.pinned_digest, ErrorCode.VALIDATION_FAILED, start
                    )
                return ProviderSuccess(
                    model_id=model_id,
                    model_version=model.pinned_digest,
                    text=generated.response,
                    latency_ms=elapsed_ms(start),
                    token_metrics=TokenMetrics(
                        input_tokens=generated.prompt_eval_count,
                        output_tokens=generated.eval_count,
                    ),
                    runtime_metrics=RuntimeMetrics(
                        total_ms=_milliseconds(generated.total_duration),
                        load_ms=_milliseconds(generated.load_duration),
                        prompt_ms=_milliseconds(generated.prompt_eval_duration),
                        generation_ms=_milliseconds(generated.eval_duration),
                    ),
                )
        except httpx.ConnectTimeout:
            return failure(
                model_id, model.pinned_digest, ErrorCode.TIMEOUT, start, retryable_connection=True
            )
        except httpx.ConnectError:
            return failure(
                model_id,
                model.pinned_digest,
                ErrorCode.UNAVAILABLE,
                start,
                retryable_connection=True,
            )
        except (TimeoutError, httpx.TimeoutException):
            return failure(model_id, model.pinned_digest, ErrorCode.TIMEOUT, start)
        except ProviderTransportError as exc:
            return failure(
                model_id,
                model.pinned_digest,
                exc.code,
                start,
                retryable_connection=exc.retryable_connection,
            )
        except httpx.HTTPError:
            return failure(model_id, model.pinned_digest, ErrorCode.UNAVAILABLE, start)
        except ValidationError:
            return failure(model_id, model.pinned_digest, ErrorCode.VALIDATION_FAILED, start)

    async def warm_up(
        self, synthetic_payload: SanitizedPayload, model_id: str, timeout_seconds: float
    ) -> ProviderResult:
        """Caller must authorize synthetic generation through the future policy gate."""
        return await self.generate(
            synthetic_payload, model_id, GenerationOptions(max_output_tokens=1), timeout_seconds
        )


class OllamaRuntimeControl:
    """Read-only control plane in Step 04; lifecycle mutations stay disabled."""

    def __init__(self, provider: OllamaProvider) -> None:
        self._provider = provider

    async def list_models(self) -> tuple[RuntimeModel, ...]:
        listed = await self._provider._listed_models()
        return tuple(
            RuntimeModel(model_id=model_id, model_version=model.pinned_digest, resident=None)
            for model_id, model in self._provider._models.items()
            if self._provider._matches(listed, model)
        )

    @staticmethod
    def _blocked(model_id: str) -> RuntimeOperationResult:
        validate_model_ids([model_id])
        return RuntimeOperationResult(
            status="error", model_id=model_id, error_code=ErrorCode.POLICY_BLOCKED
        )

    async def pull(self, manifest_id: str, pinned_digest: str) -> RuntimeOperationResult:
        return self._blocked(manifest_id)

    async def unload(self, model_id: str) -> RuntimeOperationResult:
        return self._blocked(model_id)

    async def remove(self, model_id: str) -> RuntimeOperationResult:
        return self._blocked(model_id)


def _milliseconds(nanoseconds: int | None) -> float | None:
    return nanoseconds / 1_000_000 if nanoseconds is not None else None


class OllamaLocalControl:
    """Read-only inventory plus an explicit, bound initial-install operation only."""

    def __init__(
        self,
        catalog: ModelCatalog,
        *,
        endpoint: str = "http://127.0.0.1:11434",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.catalog = ModelCatalog.model_validate(catalog.model_dump(warnings=False))
        self.endpoint = validate_endpoint(endpoint, local=True)
        self._client = create_client(transport)
        self._used_install_approvals: set[str] = set()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _json(self, path: str, *, timeout: float = 5) -> object:
        async with self._client.stream("GET", self.endpoint + path, timeout=timeout) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 1_048_576:
                    raise ValueError("Runtime metadata exceeds bound")
        return json.loads(body)

    async def observe(self) -> RuntimeReport:
        """No /generate, /show warm-up, subprocess or implicit download."""
        try:
            async with asyncio.timeout(12):
                version = await self._json("/api/version")
                tags = await self._json("/api/tags")
                if (
                    not isinstance(version, dict)
                    or not isinstance(tags, dict)
                    or not isinstance(tags.get("models"), list)
                    or len(tags["models"]) > 4096
                ):
                    raise ValueError("Invalid runtime metadata")
                version_text = version.get("version")
                if not isinstance(version_text, str) or not re.fullmatch(
                    r"[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]{0,48}", version_text
                ):
                    raise ValueError("Invalid runtime version")
                residents: dict[str, tuple[str, int]] | None = None
                try:
                    ps = await self._json("/api/ps")
                    if (
                        not isinstance(ps, dict)
                        or not isinstance(ps.get("models"), list)
                        or len(ps["models"]) > 4096
                    ):
                        raise ValueError("Invalid residency metadata")
                    residents = {}
                    for entry in ps["models"]:
                        if (
                            not isinstance(entry, dict)
                            or not isinstance(entry.get("name"), str)
                            or type(entry.get("size")) is not int
                            or entry["size"] <= 0
                            or not isinstance(entry.get("digest"), str)
                            or re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", entry["digest"]) is None
                        ):
                            raise ValueError("Invalid residency metadata")
                        if entry["name"] in residents:
                            raise ValueError("Ambiguous residency")
                        residents[entry["name"]] = (
                            entry["digest"].removeprefix("sha256:"),
                            entry["size"],
                        )
                except (ValueError, httpx.HTTPError):
                    residents = None
                known = {manifest.runtime_tag for manifest in self.catalog.models}
                observations = []
                seen: set[str] = set()
                for entry in tags["models"]:
                    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                        raise ValueError("Invalid installed model metadata")
                    name = entry["name"]
                    if name in seen:
                        raise ValueError("Ambiguous installed identity")
                    seen.add(name)
                    if name not in known:
                        continue
                    digest = entry.get("digest")
                    if not isinstance(digest, str):
                        raise ValueError("Unknown runtime digest")
                    digest = digest.removeprefix("sha256:")
                    identity_conflict = (
                        residents is not None and name in residents and residents[name][0] != digest
                    )
                    resident = (
                        None
                        if residents is None
                        else name in residents and residents[name][0] == digest
                    )
                    observations.append(
                        RuntimeObservation(
                            runtime_tag=name,
                            digest=digest,
                            resident=resident,
                            healthy=not identity_conflict,
                        )
                    )
                return RuntimeReport(
                    available=True,
                    version=version_text,
                    inventory=RuntimeInventory(models=tuple(observations)),
                    installed_count=len(tags["models"]),
                    resident_count=len(residents) if residents is not None else None,
                    resident_bytes=sum(item[1] for item in residents.values())
                    if residents is not None
                    else None,
                    reason="available",
                )
        except (httpx.HTTPError, TimeoutError):
            return RuntimeReport(reason="runtime_unavailable")
        except (ValueError, TypeError, AttributeError):
            return RuntimeReport(reason="invalid_metadata")

    async def install_authorized(
        self,
        manifest: ModelManifest,
        plan: InstallPlan,
        approval_digest: str,
        hardware: HardwareReport,
    ) -> RuntimeInventory:
        """The caller holds RuntimeLease. No delete, prune, copy, unload or generation path."""
        try:
            manifest = trusted_manifest(self.catalog, manifest.id)
            plan = InstallPlan.model_validate(plan.model_dump(warnings=False))
            hardware = HardwareReport.model_validate(hardware.model_dump(warnings=False))
            if hardware.evidence != "observed":
                raise InitialInstallError("synthetic_hardware_forbidden")
            if (
                not secrets.compare_digest(plan.approval_digest, approval_digest)
                or plan.expires_at <= datetime.now(UTC)
                or plan.manifest_version != manifest.version
                or plan.pinned_digest != manifest.pinned_digest
                or plan.model_id != manifest.id
                or plan.endpoint != self.endpoint
                or plan.hardware_version != hardware.context.version
                or plan.model_store_id != hardware.model_store_id
                or manifest.size_bytes is None
                or plan.required_disk_bytes != 2 * manifest.size_bytes + 1024**3
            ):
                raise InitialInstallError("approval_conflict")
            if reason := resource_reason(manifest, hardware, installation=True):
                raise InitialInstallError(reason)
            current = await self.observe()
            if not current.available:
                raise InitialInstallError("runtime_unavailable")
            if (
                canonical_hash(
                    current.inventory.model_dump(mode="json"), namespace="runtime-inventory-v1"
                )
                != plan.inventory_version
            ):
                raise InitialInstallError("approval_conflict")
            existing = next(
                (
                    item
                    for item in current.inventory.models
                    if item.runtime_tag == manifest.runtime_tag
                ),
                None,
            )
            if existing is not None:
                if existing.digest != manifest.pinned_digest:
                    raise InitialInstallError("existing_model_identity_conflict")
                return current.inventory
            # Validate the public registry manifest before asking the local daemon to pull.
            # Runtime tags are mutable: installed identity is checked again afterwards.
            async with self._client.stream("GET", manifest.source.uri, timeout=15) as response:
                response.raise_for_status()
                remote = bytearray()
                async for chunk in response.aiter_bytes():
                    remote.extend(chunk)
                    if len(remote) > 1_048_576:
                        raise InitialInstallError("invalid_registry_manifest")
            if hashlib.sha256(remote).hexdigest() != manifest.pinned_digest:
                raise InitialInstallError("upstream_identity_changed")
            document = json.loads(remote)
            if (
                not isinstance(document, dict)
                or type(document.get("schemaVersion")) is not int
                or document["schemaVersion"] != 2
                or not isinstance(document.get("config"), dict)
                or not isinstance(document.get("layers"), list)
                or not 1 <= len(document["layers"]) <= 128
            ):
                raise InitialInstallError("invalid_registry_manifest")
            layers = [document["config"], *document["layers"]]
            for layer in layers:
                if (
                    not isinstance(layer, dict)
                    or type(layer.get("size")) is not int
                    or layer["size"] < 0
                    or not isinstance(layer.get("digest"), str)
                    or re.fullmatch(r"sha256:[a-f0-9]{64}", layer["digest"]) is None
                ):
                    raise InitialInstallError("invalid_registry_manifest")
            if sum(layer["size"] for layer in layers) > (manifest.size_bytes or 0):
                raise InitialInstallError("catalog_download_size_mismatch")
            if (
                plan.expires_at <= datetime.now(UTC)
                or approval_digest in self._used_install_approvals
                or len(self._used_install_approvals) >= 32
            ):
                raise InitialInstallError("approval_conflict")
            self._used_install_approvals.add(approval_digest)
            response = await self._client.post(
                self.endpoint + "/api/pull",
                json={"model": manifest.runtime_tag, "stream": False, "insecure": False},
                timeout=600,
            )
            if response.status_code != 200 or response.json() != {"status": "success"}:
                raise InitialInstallError("install_failed")
            updated = await self.observe()
            actual = next(
                (
                    item
                    for item in updated.inventory.models
                    if item.runtime_tag == manifest.runtime_tag
                ),
                None,
            )
            if actual is None or actual.digest != manifest.pinned_digest:
                raise InitialInstallError("installed_identity_unverified")
            return updated.inventory
        except InitialInstallError:
            raise
        except Exception:
            raise InitialInstallError("install_failed") from None
