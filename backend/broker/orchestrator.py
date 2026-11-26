"""Sole generation boundary and Step 07 request orchestration.

Every generation attempt, including one local connection retry and a fixed
synthetic warm-up, re-enters the policy gate immediately before provider code.
The request broker composes privacy, immutable routing and structural validation.
Cache, persistent metadata and learned routing remain disabled extension seams.
"""

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, NoReturn, SupportsIndex
from uuid import UUID, uuid4

if TYPE_CHECKING:
    import httpx

    from backend.broker.router_interface import Router
    from backend.model_management.hardware import HardwareProbe
    from backend.model_management.initial_install import RuntimeLease
    from backend.model_management.manifest import ModelCatalog

from pydantic import ValidationError

from backend.api.schemas import (
    CloudFallbackReason,
    GenerationOptions,
)
from backend.broker.chat import ChatOrchestrator as ChatOrchestrator
from backend.broker.chat import ChatOutcome as ChatOutcome
from backend.broker.context import RequestContext
from backend.broker.deadline import ComputeBudget, DeadlineError
from backend.broker.policy_gate import (
    ApprovalNeeded,
    ApprovalReceipt,
    CloudProposal,
    PolicyCheck,
    PolicyGate,
    PolicyViolation,
)
from backend.config.loader import ComputeBudgets
from backend.contracts import ErrorCode, ExecutionMode, TaskCategory
from backend.model_management.registry import (
    ModelRegistry,
    RuntimeInventory,
    RuntimeObservation,
)
from backend.privacy.analyzer import Analyzer, DetectionSpan
from backend.privacy.recognisers import synthetic_spans
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer
from backend.providers.base import (
    Provider,
    ProviderDescriptor,
    ProviderFailure,
    ProviderHealth,
    ProviderSuccess,
)
from backend.providers.cloud_openai_compat import CloudProvider
from backend.providers.mock import MockModel, MockProvider
from backend.providers.ollama_slm import OllamaLocalControl, OllamaModel, OllamaProvider

DispatchOutcome = ProviderSuccess | ProviderFailure | CloudProposal
_WARMUP_TEXT = "Reply with the word ready."


class _WarmUpAnalyzer:
    version = "fixed-public-warmup-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        if text != _WARMUP_TEXT:
            raise ValueError("Warm-up requires the fixed synthetic input")
        return ()


class DispatchSession:
    """One request's in-memory state. The caller closes after output inspection."""

    __slots__ = (
        "_request_id",
        "_payload",
        "_mode",
        "_options",
        "_task",
        "_context",
        "_synthetic",
        "_pending",
        "_expiry_handle",
        "_lock",
        "_cloud_used",
        "_retry_used",
        "_approval_invalidated",
        "budget",
    )

    def __init__(
        self,
        request_id: UUID,
        payload: SanitizedPayload | None,
        mode: ExecutionMode,
        options: GenerationOptions,
        task: TaskCategory | None,
        context: RequestContext | None,
        budget: ComputeBudget,
        *,
        synthetic: bool = False,
    ) -> None:
        if context is not None and context.request_id != request_id:
            raise ValueError("Dispatch context request IDs differ")
        self._request_id = request_id
        self._payload = payload
        self._mode = mode
        self._options = options
        self._task = task
        self._context = context
        self._synthetic = synthetic
        self.budget = budget
        self._pending: CloudProposal | None = None
        self._expiry_handle: asyncio.TimerHandle | None = None
        self._lock = Lock()
        self._cloud_used = False
        self._retry_used = False
        self._approval_invalidated = False

    @property
    def request_id(self) -> UUID:
        return self._request_id

    @property
    def payload(self) -> SanitizedPayload | None:
        return self._payload

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    def set_mode(self, mode: ExecutionMode) -> None:
        if not isinstance(mode, ExecutionMode):
            raise ValueError("Invalid execution mode")
        if not self._lock.acquire(blocking=False):
            raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
        try:
            if self._pending is not None and mode != self._mode:
                self._approval_invalidated = True
            self._mode = mode
        finally:
            self._lock.release()

    @property
    def closed(self) -> bool:
        return self.budget.closed

    def _clear(self) -> None:
        self.budget.close()
        if self._expiry_handle is not None:
            self._expiry_handle.cancel()
            self._expiry_handle = None
        self._pending = None
        self._payload = None
        if self._context is not None:
            self._context.clear()
            self._context = None

    def __repr__(self) -> str:
        return "DispatchSession(<redacted>)"

    def __getstate__(self) -> NoReturn:
        raise TypeError("dispatch session serialization is forbidden")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("dispatch session serialization is forbidden")


class GuardedDispatcher:
    def __init__(
        self,
        providers: Mapping[str, Provider],
        *,
        gate: PolicyGate | None = None,
        budgets: ComputeBudgets | None = None,
    ) -> None:
        self._budgets = ComputeBudgets.model_validate((budgets or ComputeBudgets()).model_dump())
        self.gate = gate or PolicyGate(approval_seconds=self._budgets.approval_seconds)
        self._providers = dict(providers)
        self._targets: dict[str, ProviderDescriptor] = {}
        self._health: dict[str, tuple[float, ProviderHealth]] = {}
        for model_id, provider in self._providers.items():
            target = ProviderDescriptor.model_validate(provider.describe(model_id).model_dump())
            if target.model_id != model_id:
                raise ValueError("Provider descriptor model IDs differ")
            self._targets[model_id] = target

    def __repr__(self) -> str:
        return "GuardedDispatcher(<configured>)"

    def register(self, model_id: str, provider: Provider) -> None:
        """Add a provider after construction, with the same descriptor checks."""
        target = ProviderDescriptor.model_validate(provider.describe(model_id).model_dump())
        if target.model_id != model_id:
            raise ValueError("Provider descriptor model IDs differ")
        self._providers[model_id] = provider
        self._targets[model_id] = target
        self._health.pop(model_id, None)

    def unregister(self, model_id: str) -> Provider | None:
        self._targets.pop(model_id, None)
        self._health.pop(model_id, None)
        return self._providers.pop(model_id, None)

    def cloud_model_ids(self) -> tuple[str, ...]:
        """Cloud destinations in registration order; packaged ones come first."""
        return tuple(
            model_id for model_id, target in self._targets.items() if target.route == "cloud"
        )

    @property
    def test_call_counts(self) -> dict[str, int]:
        """Mock-only counters; no payload or response content is retained."""
        return {
            model_id: provider.test_call_counts.get(model_id, 0)
            for model_id, provider in self._providers.items()
            if isinstance(provider, MockProvider)
        }

    def cloud_providers(self) -> dict[str, "CloudProvider"]:
        """CloudProvider registry keyed by model_id; used by /api/cloud routes."""
        return {
            model_id: provider
            for model_id, provider in self._providers.items()
            if isinstance(provider, CloudProvider)
        }

    def new_request(
        self,
        payload: SanitizedPayload | None,
        *,
        request_id: UUID | None = None,
        mode: ExecutionMode = ExecutionMode.LOCAL_ONLY,
        options: GenerationOptions | None = None,
        task: TaskCategory | None = None,
        context: RequestContext | None = None,
    ) -> DispatchSession:
        identity = request_id or (
            payload.request_id if isinstance(payload, SanitizedPayload) else uuid4()
        )
        return DispatchSession(
            identity,
            payload,
            mode,
            options or GenerationOptions(),
            task,
            context,
            ComputeBudget(self._budgets, clock=self.gate.now),
        )

    def new_warmup(self, *, mode: ExecutionMode = ExecutionMode.LOCAL_ONLY) -> DispatchSession:
        result = Sanitizer(_WarmUpAnalyzer()).sanitize(uuid4(), _WARMUP_TEXT)
        context = RequestContext(
            result.payload.request_id,
            "",
            sanitized_payload=result.payload,
            privacy_placeholders=result.placeholders,
        )
        session = self.new_request(
            result.payload,
            mode=mode,
            options=GenerationOptions(max_output_tokens=1),
            context=context,
        )
        session._synthetic = True
        return session

    def _target(self, model_id: str) -> ProviderDescriptor:
        provider = self._providers.get(model_id)
        expected = self._targets.get(model_id)
        if provider is None or expected is None:
            raise PolicyViolation(ErrorCode.UNAVAILABLE)
        try:
            current = ProviderDescriptor.model_validate(provider.describe(model_id).model_dump())
            if current.fingerprint != expected.fingerprint:
                raise PolicyViolation()
            return current
        except PolicyViolation:
            raise
        except Exception:
            raise PolicyViolation() from None

    @staticmethod
    def _check(session: DispatchSession) -> PolicyCheck:
        return PolicyCheck(
            session.request_id,
            session.payload,
            session.mode,
            session._options,
            session._task,
            session.budget.remaining_seconds,
        )

    def approve(self, session: DispatchSession, proposal: CloudProposal) -> ApprovalReceipt:
        if not session._lock.acquire(blocking=False):
            raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
        try:
            if session._approval_invalidated:
                self._cancel_locked(session)
                raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
            if (
                session.closed
                or session.budget.remaining_seconds <= 0
                or session._pending is None
                or proposal.request_id != session.request_id
                or session.mode != proposal.mode
                or session._pending.proposal_id != proposal.proposal_id
            ):
                raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
            # Gate rechecks exact preview/binding/policy/expiry against its stored record.
            return self.gate.approve(proposal)
        except DeadlineError:
            self._cancel_locked(session)
            raise PolicyViolation(ErrorCode.TIMEOUT) from None
        finally:
            session._lock.release()

    def cancel(self, session: DispatchSession) -> None:
        if not session._lock.acquire(blocking=False):
            raise PolicyViolation(ErrorCode.APPROVAL_CONFLICT)
        try:
            self._cancel_locked(session)
        finally:
            session._lock.release()

    def _cancel_locked(self, session: DispatchSession) -> None:
        try:
            self.gate.cancel(session.request_id)
        finally:
            session._clear()

    def _schedule_expiry(self, session: DispatchSession, proposal: CloudProposal) -> None:
        if session._expiry_handle is not None:
            return
        delay = max(0.0, proposal.expires_at - self.gate.now())
        session._expiry_handle = asyncio.get_running_loop().call_later(delay, self._expire, session)

    def _expire(self, session: DispatchSession) -> None:
        session._expiry_handle = None
        if not session._lock.acquire(blocking=False):
            if session.budget.paused:
                session._expiry_handle = asyncio.get_running_loop().call_later(
                    0.05, self._expire, session
                )
            return
        try:
            if session.budget.paused:
                if session.budget.remaining_seconds <= 0:
                    self._cancel_locked(session)
                elif session._pending is not None:
                    self._schedule_expiry(session, session._pending)
        except (DeadlineError, PolicyViolation):
            session._clear()
        finally:
            session._lock.release()

    def _failure(self, model_id: str, code: ErrorCode) -> ProviderFailure:
        target = self._targets.get(model_id)
        return ProviderFailure(
            model_id=model_id if target is not None else "unavailable",
            model_version=target.model_version if target else "unavailable",
            error_code=code,
            latency_ms=0,
        )

    async def dispatch(
        self,
        session: DispatchSession,
        model_id: str,
        *,
        reason: CloudFallbackReason | None = None,
        approval: ApprovalReceipt | None = None,
    ) -> DispatchOutcome:
        return await self._dispatch(
            session, model_id, reason=reason, approval=approval, warm_up=False
        )

    async def warm_up(
        self,
        session: DispatchSession,
        model_id: str,
        *,
        reason: CloudFallbackReason | None = None,
        approval: ApprovalReceipt | None = None,
    ) -> DispatchOutcome:
        if (
            not session._synthetic
            or session.payload is None
            or session.payload.text != _WARMUP_TEXT
        ):
            return self._failure(model_id, ErrorCode.POLICY_BLOCKED)
        return await self._dispatch(
            session, model_id, reason=reason, approval=approval, warm_up=True
        )

    async def _dispatch(
        self,
        session: DispatchSession,
        model_id: str,
        *,
        reason: CloudFallbackReason | None,
        approval: ApprovalReceipt | None,
        warm_up: bool,
    ) -> DispatchOutcome:
        if not session._lock.acquire(blocking=False):
            return self._failure(model_id, ErrorCode.APPROVAL_CONFLICT)
        try:
            dispatch_deadline: float | None = None
            for attempt in range(2):
                target = self._target(model_id)
                if session._approval_invalidated:
                    self._cancel_locked(session)
                    return self._failure(model_id, ErrorCode.APPROVAL_CONFLICT)
                if dispatch_deadline is None:
                    dispatch_deadline = self.gate.now() + (
                        self._budgets.local_seconds
                        if target.route == "local"
                        else self._budgets.cloud_seconds
                    )
                if session.budget.remaining_seconds <= 0:
                    self._cancel_locked(session)
                    return self._failure(model_id, ErrorCode.TIMEOUT)
                if target.route == "cloud" and session._cloud_used:
                    return self._failure(model_id, ErrorCode.APPROVAL_CONFLICT)
                if session.budget.paused and target.route != "cloud":
                    return self._failure(model_id, ErrorCode.APPROVAL_CONFLICT)
                try:
                    # No await occurs between this authorization and provider entry.
                    self.gate.authorize(self._check(session), target, reason, approval)
                except ApprovalNeeded:
                    if approval is not None or reason is None:
                        return self._failure(model_id, ErrorCode.APPROVAL_CONFLICT)
                    proposal = self.gate.propose(self._check(session), target, reason)
                    session.budget.pause(proposal.expires_at)
                    session._pending = proposal
                    self._schedule_expiry(session, proposal)
                    return proposal
                if session.budget.paused:
                    session.budget.resume()
                    session._pending = None
                    if session._expiry_handle is not None:
                        session._expiry_handle.cancel()
                        session._expiry_handle = None
                if target.route == "cloud":
                    session._cloud_used = True
                timeout = min(
                    dispatch_deadline - self.gate.now(),
                    session.budget.remaining_seconds,
                )
                payload = session.payload
                if payload is None or timeout <= 0:
                    return self._failure(model_id, ErrorCode.TIMEOUT)
                provider = self._providers[model_id]
                try:
                    async with asyncio.timeout(timeout):
                        if warm_up:
                            result = await provider.warm_up(payload, model_id, timeout)
                        else:
                            result = await provider.generate(
                                payload, model_id, session._options, timeout
                            )
                    if session.budget.remaining_seconds <= 0:
                        self._cancel_locked(session)
                        return self._failure(model_id, ErrorCode.TIMEOUT)
                    if self.gate.now() >= dispatch_deadline:
                        return self._failure(model_id, ErrorCode.TIMEOUT)
                    result = (
                        ProviderSuccess.model_validate(result.model_dump())
                        if isinstance(result, ProviderSuccess)
                        else ProviderFailure.model_validate(result.model_dump())
                    )
                    if result.model_id != model_id or result.model_version != target.model_version:
                        result = self._failure(model_id, ErrorCode.VALIDATION_FAILED)
                except TimeoutError:
                    result = self._failure(model_id, ErrorCode.TIMEOUT)
                except (ValidationError, AttributeError, TypeError, ValueError):
                    result = self._failure(model_id, ErrorCode.VALIDATION_FAILED)
                except asyncio.CancelledError:
                    self._cancel_locked(session)
                    raise
                except Exception:
                    result = self._failure(model_id, ErrorCode.INTERNAL)
                if isinstance(result, ProviderFailure):
                    self._health.pop(model_id, None)
                    if (
                        target.route == "local"
                        and result.retryable_connection
                        and attempt == 0
                        and not session._retry_used
                        and self._budgets.local_connection_retries == 1
                    ):
                        session._retry_used = True
                        continue
                return result
            return self._failure(model_id, ErrorCode.INTERNAL)
        except PolicyViolation as exc:
            return self._failure(model_id, exc.code)
        except DeadlineError:
            self._cancel_locked(session)
            return self._failure(model_id, ErrorCode.TIMEOUT)
        finally:
            session._lock.release()

    async def health(self, model_id: str) -> ProviderHealth:
        """Metadata only: no warm-up/generation side effects and a bounded snapshot."""
        target = self._target(model_id)
        now = self.gate.now()
        cached = self._health.get(model_id)
        if cached is not None and now - cached[0] < self._budgets.health_ttl_seconds:
            return cached[1]
        try:
            async with asyncio.timeout(self._budgets.health_ttl_seconds):
                health = await self._providers[model_id].health(model_id)
            health = ProviderHealth.model_validate(health.model_dump())
            if health.model_id != model_id or health.model_version != target.model_version:
                raise ValueError("Provider health identity mismatch")
        except asyncio.CancelledError:
            raise
        except Exception:
            health = ProviderHealth(
                model_id=model_id, model_version=target.model_version, available=False
            )
        self._health[model_id] = (self.gate.now(), health)
        return health

    async def aclose(self) -> None:
        """Close owned transport resources; no inference or runtime mutations."""
        from collections.abc import Awaitable, Callable
        from typing import cast

        seen: set[int] = set()
        for provider in self._providers.values():
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            closer = getattr(provider, "aclose", None)
            if callable(closer):
                await cast(Callable[[], Awaitable[None]], closer)()


class MockAnalyzer:
    """Fixture scanner only: invented IDs/card markers and simple email patterns."""

    version = "mock-synthetic-analyzer-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        spans = list(synthetic_spans(text))
        for kind, pattern in (
            ("EMAIL_ADDRESS", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            ("CREDIT_CARD", r"\bCARD-[0-9]{4}\b"),
        ):
            spans.extend(
                DetectionSpan(kind, match.start(), match.end())
                for match in re.finditer(pattern, text)
            )
        return tuple(spans)


def attach_cloud_destination(broker: "ChatOrchestrator", config: object) -> str:
    """Register an owner-added destination on a running broker.

    Lives here because concrete providers are only constructed in this module;
    the API layer passes validated configuration and never the provider itself.
    """
    from backend.providers.cloud_openai_compat import CloudDestination, CloudProvider

    resolved = CloudDestination.model_validate(
        config.model_dump() if hasattr(config, "model_dump") else config
    )
    broker.dispatcher.register(resolved.model_id, CloudProvider(resolved, environment={}))
    return resolved.model_id


async def detach_cloud_destination(broker: "ChatOrchestrator", model_id: str) -> None:
    provider = broker.dispatcher.unregister(model_id)
    closer = getattr(provider, "aclose", None)
    if closer is not None:
        await closer()


def build_mock_broker(
    *,
    model_overrides: Mapping[str, MockModel] | None = None,
    installed_ids: tuple[str, ...] = ("mock-general", "mock-specialist"),
    analyzer: Analyzer | None = None,
    gate: PolicyGate | None = None,
    budgets: ComputeBudgets | None = None,
    router: "Router | None" = None,
    cloud_destinations: tuple[object, ...] = (),
) -> ChatOrchestrator:
    """No HTTP transports, credentials, model assets or runtime are constructed."""
    from backend.model_management.fixtures import build_mock_catalog

    catalog = build_mock_catalog()
    if (
        len(set(installed_ids)) != len(installed_ids)
        or not set(installed_ids) <= {model.id for model in catalog.models}
        or not set(model_overrides or {}) <= {"mock-cloud", *(model.id for model in catalog.models)}
    ):
        raise ValueError("Unknown or duplicate mock model configuration")
    registry = ModelRegistry(catalog, mock_mode=True)
    defaults = {
        "mock-general": MockModel(response="Synthetic general response", response_mode="echo"),
        "mock-specialist": MockModel(response='{"answer":"Synthetic specialist response"}'),
    }
    overrides = dict(model_overrides or {})
    configured: dict[str, MockModel] = {}
    for manifest in catalog.models:
        if manifest.id in installed_ids:
            original = overrides.get(
                manifest.id,
                defaults.get(manifest.id, MockModel(response="Synthetic third response")),
            )
            configured[manifest.id] = MockModel.model_validate(
                {**original.model_dump(warnings=False), "version": manifest.version}
            )
    local = MockProvider(configured) if configured else None
    providers: dict[str, Provider] = {model_id: local for model_id in configured} if local else {}
    cloud_model = overrides.get(
        "mock-cloud",
        MockModel(
            response="Synthetic cloud response",
            version="synthetic-cloud-v1",
            response_mode="json_echo",
        ),
    )
    providers["mock-cloud"] = MockProvider(
        {"mock-cloud": cloud_model}, route="cloud", key_available=True
    )
    # Optional extra CloudProvider destinations for exercising the BYO-key UI
    # paths in mock mode. These providers never dispatch successfully because
    # tests never set a real key on them; they exist to populate /api/cloud.
    cloud_primary: str | None = None
    for destination in cloud_destinations:
        from backend.providers.cloud_openai_compat import CloudDestination, CloudProvider

        resolved = CloudDestination.model_validate(
            destination.model_dump() if hasattr(destination, "model_dump") else destination
        )
        providers[resolved.model_id] = CloudProvider(resolved, environment={})
        if cloud_primary is None:
            cloud_primary = resolved.model_id
    registry.reconcile(
        RuntimeInventory(
            models=tuple(
                RuntimeObservation(
                    runtime_tag=manifest.runtime_tag,
                    resident=False,
                    healthy=configured[manifest.id].available,
                )
                for manifest in catalog.models
                if manifest.id in configured
            )
        )
    )
    return ChatOrchestrator(
        GuardedDispatcher(providers, gate=gate, budgets=budgets),
        registry,
        analyzer or MockAnalyzer(),
        cloud_model_ids=(
            "mock-cloud",
            *(item for item in providers if item != "mock-cloud" and item not in configured),
        ),
        mode="mock",
        router=router,
    )


def build_local_control(
    catalog: "ModelCatalog",
    *,
    endpoint: str = "http://127.0.0.1:11434",
    transport: "httpx.AsyncBaseTransport | None" = None,
) -> OllamaLocalControl:
    return OllamaLocalControl(catalog, endpoint=endpoint, transport=transport)


async def build_real_broker(
    *,
    catalog: "ModelCatalog | None" = None,
    endpoint: str = "http://127.0.0.1:11434",
    hardware_probe: "HardwareProbe | None" = None,
    analyzer: Analyzer | None = None,
    transport: "httpx.AsyncBaseTransport | None" = None,
    lease: "RuntimeLease | None" = None,
    model_store: "Path | None" = None,
    target_model_id: str | None = None,
    allow_mock_fallback: bool = True,
    background_refresh: bool = True,
) -> ChatOrchestrator:
    """Owner-started real setup. No installs or warm-ups.

    A cloud provider is wired only when ``settings.yaml`` declares a
    ``cloud_destination`` *and* the corresponding environment variable
    supplies a non-empty API key. When either is missing the broker runs
    local-only, matching earlier behaviour.
    """
    from backend.broker.local_runtime import (
        REAL_CONTEXT_TOKENS,
        LocalResourceMonitor,
        RealLocalBroker,
    )
    from backend.config.loader import load_catalog, load_settings
    from backend.model_management.hardware import probe_hardware, resident_limit, resource_reason
    from backend.model_management.initial_install import (
        ROOT,
        InitialInstallError,
        RuntimeLease,
        trusted_manifest,
    )
    from backend.privacy.analyzer import PresidioAnalyzer
    from backend.providers.cloud_openai_compat import CloudDestination, CloudProvider

    approved_catalog = catalog or load_catalog()
    operation = lease or RuntimeLease()
    control = build_local_control(approved_catalog, endpoint=endpoint, transport=transport)
    provider: OllamaProvider | None = None
    try:
        operation.acquire()
        report = await control.observe()
        if not report.available:
            if not allow_mock_fallback:
                raise InitialInstallError("runtime_unavailable")
            await control.aclose()
            operation.release()
            mock = build_mock_broker()
            mock.runtime_notice = "ollama_unavailable_using_mock"
            return mock
        for manifest in approved_catalog.models:
            if manifest.verification == "verified":
                trusted_manifest(approved_catalog, manifest.id)
        probe = hardware_probe or (lambda: probe_hardware(ROOT, model_store))
        hardware = await asyncio.to_thread(probe)
        if hardware.evidence != "observed":
            raise InitialInstallError("synthetic_hardware_forbidden")
        registry = ModelRegistry(approved_catalog, hardware=hardware.context)
        registry.reconcile(report.inventory)
        eligible = tuple(
            model
            for model in approved_catalog.models
            if model.id in registry.snapshot().eligible_model_ids
        )
        if len(eligible) < 2:
            raise InitialInstallError("two_verified_eligible_local_models_required")
        if any(resource_reason(model, hardware) for model in eligible):
            raise InitialInstallError("insufficient_or_unknown_resources")
        scanner = analyzer if analyzer is not None else PresidioAnalyzer.preload()
        monitor = LocalResourceMonitor(control, registry, approved_catalog, probe, hardware, report)
        capacity = resident_limit(eligible, hardware)
        provider = OllamaProvider(
            {
                model.id: OllamaModel(
                    runtime_tag=model.runtime_tag, pinned_digest=model.pinned_digest or ""
                )
                for model in eligible
            },
            endpoint=endpoint,
            transport=transport,
            preflight=monitor.permitted,
            keep_alive_seconds=60 if capacity >= len(eligible) else 0,
            context_tokens=REAL_CONTEXT_TOKENS,
        )
        router = None
        if target_model_id is not None:
            from backend.broker.real_smoke import TargetModelRules

            if target_model_id not in registry.snapshot().eligible_model_ids:
                raise InitialInstallError("unknown_smoke_target")
            router = TargetModelRules(target_model_id)
        providers_by_id: dict[str, Provider] = {model.id: provider for model in eligible}
        cloud_model_ids: list[str] = []
        settings = load_settings()
        for destination_config in settings.cloud_destinations:
            destination = CloudDestination.model_validate(destination_config.model_dump())
            cloud_provider = CloudProvider.from_environment(destination, transport=transport)
            providers_by_id[destination.model_id] = cloud_provider
            # Declaration order is the fallback preference order; which one is
            # active depends on where a key has been supplied.
            cloud_model_ids.append(destination.model_id)
        broker = RealLocalBroker(
            GuardedDispatcher(providers_by_id),
            registry,
            scanner,
            router=router,
            mode="real",
            max_active=1,
            cloud_model_ids=tuple(cloud_model_ids),
        )
        broker.monitor = monitor
        broker.runtime_lease = operation
        if background_refresh:
            monitor.start()
        return broker
    except BaseException:
        try:
            if provider is not None:
                await provider.aclose()
        finally:
            try:
                await control.aclose()
            finally:
                operation.release()
        raise
