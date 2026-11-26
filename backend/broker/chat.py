"""Request workflow; generation in the guarded dispatcher, exact cache in Step 10."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

from backend.api.schemas import (
    ApprovalRequest,
    ApprovalRequiredResponse,
    CacheProvenance,
    CacheStatus,
    CancelledResponse,
    ChatRequest,
    ChatResponse,
    CloudFallbackReason,
    ErrorResponse,
    GenerationMetrics,
    HealthResponse,
    MetricsResponse,
    ResponseTimings,
)
from backend.broker.classifier import classify_task
from backend.broker.context import RequestContext, RoutingPlan
from backend.broker.deadline import DeadlineError
from backend.broker.extensions import Extensions
from backend.broker.lightgbm_router import LightGBMRouter
from backend.broker.pending import PendingChat
from backend.broker.policy_gate import CloudProposal, PolicyViolation
from backend.broker.prompting import constraint_directives
from backend.broker.router_interface import Router
from backend.broker.rule_router import RuleRouter
from backend.broker.validators import ResponseConstraints, ResponseFormat, validate_response
from backend.cache.eligibility import cache_eligible_request
from backend.cache.exact_cache import ExactCache
from backend.cache.key import exact_cache_key
from backend.contracts import ErrorCode, ExecutionMode, TaskCategory
from backend.model_management.registry import ModelRegistry, RosterLease
from backend.privacy.analyzer import Analyzer
from backend.privacy.inspector import MASKED_WARNING, OutputInspector
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer
from backend.profiles.preferences import PreferenceProfile
from backend.providers.base import ProviderFailure, ProviderSuccess
from backend.versions import POLICY_VERSION, SCHEMA_VERSION

if TYPE_CHECKING:
    from backend.broker.orchestrator import DispatchSession, GuardedDispatcher

ChatOutcome = ChatResponse | ApprovalRequiredResponse | ErrorResponse | CancelledResponse


def compose_prompt(request: ChatRequest, *, now: datetime | None = None) -> str:
    """Flatten the dated header and prior turns into the single text the sanitizer masks.

    History joins the prompt before masking so no earlier turn reaches a model
    unmasked and a repeated value keeps one placeholder across the transcript.
    The clock reading changes every minute, so an enabled exact cache misses far
    more often; answering "what time is it" at all is worth that.
    """
    moment = now or datetime.now().astimezone()
    stamp = moment.strftime("%A, %d %B %Y, %H:%M")
    # Framed as background rather than content: stated plainly, small models
    # copy the timestamp into their answers, including into example code.
    header = (
        f"(Background, use only if asked: today is {stamp} {moment.tzname()}. "
        "Do not mention this unless the question is about the date or time.)"
    )
    directives = constraint_directives(request.constraints)
    preamble = f"{header}\n\n{directives}\n" if directives else header
    if not request.history:
        return f"{preamble}\n\n{request.prompt}"
    transcript = "\n".join(
        f"{'User' if turn.role == 'user' else 'Assistant'}: {turn.content}"
        for turn in request.history
    )
    return f"{preamble}\n\nConversation so far:\n{transcript}\n\nUser: {request.prompt}"


class ChatOrchestrator:
    """One worker, bounded in-memory approvals; history arrives per request, never stored."""

    def __init__(
        self,
        dispatcher: GuardedDispatcher,
        registry: ModelRegistry,
        analyzer: Analyzer,
        *,
        router: Router | None = None,
        inspector: OutputInspector | None = None,
        preferences: PreferenceProfile | None = None,
        cloud_model_ids: tuple[str, ...] = (),
        max_pending: int = 128,
        max_active: int = 8,
        extensions: Extensions | None = None,
        cache: ExactCache | None = None,
        mode: Literal["mock", "injected", "real"] = "injected",
    ) -> None:
        if (
            isinstance(max_pending, bool)
            or not isinstance(max_pending, int)
            or not 1 <= max_pending <= 4096
            or isinstance(max_active, bool)
            or not isinstance(max_active, int)
            or not 1 <= max_active <= 128
        ):
            raise ValueError("Invalid request capacity")
        self.dispatcher = dispatcher
        self.registry = registry
        self.analyzer = analyzer
        self._analyzer = analyzer
        self._sanitizer = Sanitizer(analyzer)
        self._inspector = inspector or OutputInspector(analyzer)
        self.inspector = self._inspector
        self._router = router or RuleRouter()
        if not isinstance(self._router, (RuleRouter, LightGBMRouter)):
            raise ValueError("Unsupported router type")
        self._preferences = PreferenceProfile.model_validate(
            (preferences or PreferenceProfile()).model_dump(warnings=False)
        )
        self.extensions = Extensions.model_validate(
            (extensions or Extensions()).model_dump(warnings=False)
        )
        if len(set(cloud_model_ids)) != len(cloud_model_ids):
            raise ValueError("Duplicate cloud destination binding")
        if any(dispatcher._target(item).route != "cloud" for item in cloud_model_ids):
            raise ValueError("Cloud binding must use a configured cloud destination")
        self._seed_cloud_candidates = cloud_model_ids
        self._cache = cache
        self._pending: dict[UUID, PendingChat] = {}
        self._pending_lock = Lock()
        self._max_pending = max_pending
        self._max_active = max_active
        self._active = 0
        self._running: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._mode = mode
        self.runtime_notice: Literal["ollama_unavailable_using_mock"] | None = None
        self._requests = 0
        self._completed = 0
        self._errors = 0
        self._cache_hits = 0

    def __repr__(self) -> str:
        return "ChatOrchestrator(<redacted>)"

    @property
    def _cloud_model_id(self) -> str | None:
        """First declared destination holding a key; the first one otherwise.

        Keys are pasted into the running process, so the active destination
        cannot be fixed at startup: keying only the third provider must still
        give a fallback, and clearing that key must take it away again. When no
        destination holds a key the first is still returned, so the gate reports
        a destination that cannot be used rather than one that looks unconfigured.
        """
        candidates = self._cloud_candidates
        for model_id in candidates:
            if self.dispatcher._target(model_id).key_available:
                return model_id
        return candidates[0] if candidates else None

    @property
    def _cloud_candidates(self) -> tuple[str, ...]:
        """Seeded destinations first, then any the owner added while running."""
        registered = self.dispatcher.cloud_model_ids()
        seeded = tuple(item for item in self._seed_cloud_candidates if item in registered)
        return seeded + tuple(item for item in registered if item not in seeded)

    @property
    def preferences(self) -> PreferenceProfile:
        return self._preferences

    @property
    def cache(self) -> ExactCache | None:
        return self._cache

    @property
    def router(self) -> Router:
        return self._router

    def update_preferences(self, profile: PreferenceProfile) -> None:
        self._preferences = PreferenceProfile.model_validate(profile.model_dump(warnings=False))
        self.extensions = Extensions.model_validate(
            Extensions(cache_enabled=profile.exact_cache_enabled).model_dump(warnings=False)
        )

    @property
    def pending_count(self) -> int:
        self._purge()
        with self._pending_lock:
            return len(self._pending)

    @property
    def test_call_counts(self) -> dict[str, int]:
        """Mock-only instrumentation, never payload content or production telemetry."""
        return self.dispatcher.test_call_counts

    def cloud_providers(self) -> dict[str, Any]:
        """CloudProvider registry keyed by model_id; delegated to the dispatcher."""
        return self.dispatcher.cloud_providers()

    def health_status(self) -> HealthResponse:
        eligible = len(self.registry.snapshot().eligible_model_ids)
        return HealthResponse(
            status="ok" if eligible and not self._closed else "degraded",
            mode=self._mode,
            privacy_analyzer="synthetic_fixture"
            if self._mode == "mock"
            else "local_preloaded"
            if self._mode == "real"
            else "injected",
            eligible_local_models=eligible,
            notice=self.runtime_notice,
        )

    def metrics_status(self) -> MetricsResponse:
        return MetricsResponse(
            requests=self._requests,
            completed=self._completed,
            errors=self._errors,
            pending_approvals=self.pending_count,
            cache_enabled=self.extensions.cache_enabled,
            cache_hits=self._cache_hits if self.extensions.cache_enabled else 0,
            cache_entries=(
                self._cache.count() if self._cache and self.extensions.cache_enabled else 0
            ),
        )

    def _router_type(self) -> Literal["rules", "lightgbm"]:
        return self._router.router_type

    def _error(self, request_id: UUID, code: ErrorCode, *, masked: bool = False) -> ErrorResponse:
        self._errors += 1
        return ErrorResponse.model_validate(
            {
                "request_id": request_id,
                "error_code": code,
                "answer": MASKED_WARNING if masked else None,
            }
        )

    def _dispose(self, state: PendingChat) -> None:
        if state.timer is not None:
            state.timer.cancel()
            state.timer = None
        try:
            self.dispatcher.cancel(state.session)
        finally:
            state.context.clear()
            if state.lease is not None:
                state.lease.release()
                state.lease = None
            state.best_local = None

    def _expire_pending(self, request_id: UUID) -> None:
        with self._pending_lock:
            state = self._pending.pop(request_id, None)
        if state is not None:
            self._dispose(state)

    def _purge(self) -> None:
        try:
            now = self.dispatcher.gate.now()
        except PolicyViolation:
            now = float("inf")
        with self._pending_lock:
            expired = [
                self._pending.pop(key)
                for key, value in tuple(self._pending.items())
                if now >= value.cloud_proposal.expires_at
            ]
        for state in expired:
            self._dispose(state)

    def _finalize(
        self,
        state: PendingChat,
        result: ProviderSuccess,
        *,
        route: Literal["local", "cloud"],
        reason: str,
        manifest_version: str | None,
        timings: ResponseTimings,
        cache_status: CacheStatus = CacheStatus.DISABLED,
    ) -> ChatResponse | ErrorResponse:
        if not validate_response(result.text, state.constraints).valid:
            return self._error(state.session.request_id, ErrorCode.VALIDATION_FAILED)
        placeholders = state.context.privacy_placeholders
        if placeholders is None:
            return self._error(state.session.request_id, ErrorCode.OUTPUT_BLOCKED, masked=True)
        inspection = self._inspector.inspect(state.session.request_id, result.text, placeholders)
        if inspection.warning:
            return self._error(state.session.request_id, ErrorCode.OUTPUT_BLOCKED, masked=True)
        if not validate_response(inspection.answer, state.constraints).valid:
            return self._error(state.session.request_id, ErrorCode.VALIDATION_FAILED)
        payload = state.session.payload
        token_metrics = result.token_metrics
        runtime_metrics = result.runtime_metrics
        generation_metrics = None
        if token_metrics is not None or runtime_metrics is not None:
            generation_ms = runtime_metrics.generation_ms if runtime_metrics is not None else None
            output_tokens = token_metrics.output_tokens if token_metrics is not None else None
            generation_metrics = GenerationMetrics(
                input_tokens=token_metrics.input_tokens if token_metrics else None,
                output_tokens=output_tokens,
                runtime_total_ms=runtime_metrics.total_ms if runtime_metrics else None,
                load_ms=runtime_metrics.load_ms if runtime_metrics else None,
                prompt_ms=runtime_metrics.prompt_ms if runtime_metrics else None,
                generation_ms=generation_ms,
                output_tokens_per_second=1000 * output_tokens / generation_ms
                if output_tokens is not None and generation_ms is not None and generation_ms > 0
                else None,
            )
        return ChatResponse(
            request_id=state.session.request_id,
            answer=inspection.answer,
            route=route,
            model_id=result.model_id,
            model_version=result.model_version,
            reason=reason,
            privacy_counts=payload.privacy_counts if payload else (),
            restored=inspection.restored,
            cache_status=cache_status,
            timings=timings,
            manifest_version=manifest_version,
            task=state.task,
            router_type=self._router_type(),
            router_version=self._router.router_version,
            mock=self._mode == "mock",
            generation_metrics=generation_metrics,
        )

    @staticmethod
    def _constraints(
        request: ChatRequest, task: TaskCategory, payload: SanitizedPayload
    ) -> ResponseConstraints:
        spec = request.constraints
        if task == TaskCategory.FORMAT and spec == ResponseConstraints():
            if re.search(r"\bjson\b", payload.text, re.IGNORECASE):
                return ResponseConstraints(response_format=ResponseFormat.JSON)
            bullets = re.search(
                r"\b(?:exactly|return|use)\s+([0-9]{1,3})\s+bullets?\b",
                payload.text,
                re.IGNORECASE,
            )
            if bullets:
                return ResponseConstraints(min_bullets=int(bullets[1]), max_bullets=int(bullets[1]))
        return spec

    async def chat(self, request: ChatRequest) -> ChatOutcome:
        request_id = uuid4()
        self._requests += 1
        if self._closed or self._active >= self._max_active:
            return self._error(request_id, ErrorCode.UNAVAILABLE)
        self._purge()
        self._active += 1
        active_task = asyncio.current_task()
        if active_task is not None:
            self._running.add(active_task)
        started = perf_counter()
        session: DispatchSession | None = None
        context: RequestContext | None = None
        lease: RosterLease | None = None
        retained = False
        try:
            try:
                request = ChatRequest.model_validate(request.model_dump(warnings=False))
            except (AttributeError, TypeError, ValueError):
                return self._error(request_id, ErrorCode.INVALID_REQUEST)
            session = self.dispatcher.new_request(
                None, request_id=request_id, mode=request.mode, options=request.options
            )
            privacy_started = perf_counter()
            try:
                sanitized = self._sanitizer.sanitize(request_id, compose_prompt(request))
                # Routing must read the new message alone. Classifying the whole
                # transcript meant one earlier code question sent every later
                # turn to the code model. Masked here so classification still
                # only ever sees trusted text; the extra map is discarded below.
                classified = (
                    self._sanitizer.sanitize(request_id, request.prompt)
                    if request.history
                    else sanitized
                )
            except Exception:
                return self._error(request_id, ErrorCode.PRIVACY_FAILED)
            privacy_ms = (perf_counter() - privacy_started) * 1000
            context = RequestContext(
                request_id,
                "",
                sanitized_payload=sanitized.payload,
                privacy_placeholders=sanitized.placeholders,
            )
            session._payload = sanitized.payload
            session._context = context
            if session.budget.remaining_seconds <= 0:
                return self._error(request_id, ErrorCode.TIMEOUT)
            routing_started = perf_counter()
            task = classify_task(classified.payload, request.task_hint, request.constraints).task
            if classified is not sanitized:
                classified.placeholders.clear()
            session._task = task
            constraints = self._constraints(request, task, sanitized.payload)
            snapshot = self.registry.snapshot()
            plan = self._router.rank(sanitized.payload, task, snapshot, self._preferences)
            plan = RoutingPlan.model_validate(plan.model_dump(warnings=False))
            entries = {entry.manifest.id: entry for entry in snapshot.roster.entries}
            if plan.model_ids:
                lease = self.registry.lease(plan.model_ids, snapshot=snapshot)
            routing_ms = (perf_counter() - routing_started) * 1000
            pii_count = sanitized.placeholders.entry_count
            is_cache_eligible = (
                self._cache is not None
                and self.extensions.cache_enabled
                and cache_eligible_request(
                    self._preferences,
                    request.sensitivity_scope,
                    pii_count,
                    has_history=bool(request.history),
                )
            )
            generation_ms = 0.0
            last_error = ErrorCode.UNAVAILABLE
            failed_validation = False
            failed_local = False
            best_local: ChatResponse | None = None
            # A provisional state is kept locally, never inserted in the pending store.
            state: PendingChat | None = None
            cloud_only = request.mode == ExecutionMode.CLOUD_ONLY
            local_plan = (
                ()
                if cloud_only
                else tuple(zip(plan.model_ids, plan.manifest_versions, strict=True))
            )
            for model_id, version in local_plan:
                entry = entries[model_id]
                if version != entry.manifest.version:
                    return self._error(request_id, ErrorCode.VALIDATION_FAILED)
                target = self.dispatcher._target(model_id)
                expected = (
                    entry.manifest.version if snapshot.mock_mode else entry.manifest.pinned_digest
                )
                if target.route != "local" or target.model_version != expected:
                    return self._error(request_id, ErrorCode.VALIDATION_FAILED)
                if is_cache_eligible and self._cache is not None:
                    cache_key = exact_cache_key(
                        prompt=request.prompt,
                        task=task,
                        model_id=model_id,
                        model_version=version,
                        mode=request.mode,
                        sensitivity_scope=request.sensitivity_scope,
                        options=request.options,
                    )
                    hit = self._cache.lookup(cache_key)
                    if hit is not None:
                        self._cache_hits += 1
                        self._completed += 1
                        timings = ResponseTimings(
                            total_ms=(perf_counter() - started) * 1000,
                            privacy_ms=privacy_ms,
                            routing_ms=routing_ms,
                            generation_ms=0.0,
                        )
                        return ChatResponse(
                            request_id=request_id,
                            answer=hit.answer,
                            route="local",
                            model_id=model_id,
                            model_version=version,
                            reason="exact_cache_hit",
                            cache_status=CacheStatus.EXACT_HIT,
                            cache_provenance=CacheProvenance(
                                model_id=model_id,
                                model_version=version,
                                policy_version=POLICY_VERSION,
                                schema_version=SCHEMA_VERSION,
                            ),
                            timings=timings,
                            task=task,
                            router_type=self._router_type(),
                            router_version=self._router.router_version,
                            mock=self._mode == "mock",
                        )
                tick = perf_counter()
                local = await self.dispatcher.dispatch(session, model_id)
                generation_ms += (perf_counter() - tick) * 1000
                if isinstance(local, ProviderFailure):
                    last_error = local.error_code
                    failed_local = True
                    if local.error_code in {
                        ErrorCode.POLICY_BLOCKED,
                        ErrorCode.APPROVAL_CONFLICT,
                        ErrorCode.INTERNAL,
                    }:
                        return self._error(request_id, local.error_code)
                    continue
                if not isinstance(local, ProviderSuccess):
                    return self._error(request_id, ErrorCode.INTERNAL)
                if not validate_response(local.text, constraints).valid:
                    failed_validation = True
                    last_error = ErrorCode.VALIDATION_FAILED
                    continue
                timings = ResponseTimings(
                    total_ms=(perf_counter() - started) * 1000,
                    privacy_ms=privacy_ms,
                    routing_ms=routing_ms,
                    generation_ms=generation_ms,
                )
                # This internal record never becomes a real cloud proposal.
                state = self._local_state(session, context, constraints, task, timings, lease)
                finalized = self._finalize(
                    state,
                    local,
                    route="local",
                    reason="local_primary" if model_id == plan.model_ids[0] else "local_cascade",
                    manifest_version=version,
                    timings=timings,
                    cache_status=CacheStatus.MISS if is_cache_eligible else CacheStatus.DISABLED,
                )
                if isinstance(finalized, ErrorResponse):
                    return finalized
                if (
                    is_cache_eligible
                    and self._cache is not None
                    and isinstance(finalized, ChatResponse)
                ):
                    cache_key = exact_cache_key(
                        prompt=request.prompt,
                        task=task,
                        model_id=model_id,
                        model_version=version,
                        mode=request.mode,
                        sensitivity_scope=request.sensitivity_scope,
                        options=request.options,
                    )
                    self._cache.store(
                        cache_key,
                        finalized.answer,
                        model_id,
                        version,
                        task.value,
                    )
                policy = self.dispatcher.gate.policy
                if (
                    request.mode == ExecutionMode.LOCAL_ONLY
                    or self._cloud_model_id is None
                    or task not in policy.cloud_preferred_categories
                    or CloudFallbackReason.CLOUD_PREFERRED not in policy.allowed_fallback_reasons
                ):
                    self._completed += 1
                    return finalized
                best_local = finalized
                break
            if request.mode == ExecutionMode.LOCAL_ONLY:
                return self._error(request_id, last_error)
            if self._cloud_model_id is None:
                # Cloud-only with nothing configured must say so rather than
                # report a local failure that was never attempted.
                return self._error(request_id, ErrorCode.UNAVAILABLE if cloud_only else last_error)
            policy = self.dispatcher.gate.policy
            reason: CloudFallbackReason | None = None
            if cloud_only:
                reason = CloudFallbackReason.CLOUD_ONLY
            elif best_local is not None:
                reason = CloudFallbackReason.CLOUD_PREFERRED
            elif not plan.model_ids and policy.allow_no_local_model_cloud:
                reason = CloudFallbackReason.NO_LOCAL_MODEL
            elif (
                failed_validation
                and CloudFallbackReason.VALIDATION_FAILURE in policy.allowed_fallback_reasons
            ):
                reason = CloudFallbackReason.VALIDATION_FAILURE
            elif (
                failed_local and CloudFallbackReason.LOCAL_ERROR in policy.allowed_fallback_reasons
            ):
                reason = CloudFallbackReason.LOCAL_ERROR
            if reason is None:
                return self._error(request_id, last_error)
            tick = perf_counter()
            cloud = await self.dispatcher.dispatch(session, self._cloud_model_id, reason=reason)
            generation_ms += (perf_counter() - tick) * 1000
            timings = ResponseTimings(
                total_ms=(perf_counter() - started) * 1000,
                privacy_ms=privacy_ms,
                routing_ms=routing_ms,
                generation_ms=generation_ms,
            )
            if isinstance(cloud, CloudProposal):
                remaining = max(0.0, cloud.expires_at - self.dispatcher.gate.now())
                response = ApprovalRequiredResponse(
                    request_id=request_id,
                    sanitized_preview=cloud.sanitized_preview,
                    expires_at=datetime.now(UTC) + timedelta(seconds=remaining),
                    reason=reason,
                    best_local_answer=best_local.answer if best_local else None,
                    proposal_id=cloud.proposal_id,
                    digest=cloud.digest,
                    mode=cloud.mode,
                    model_id=cloud.model_id,
                    model_version=cloud.model_version,
                    policy_version=cloud.policy_version,
                )
                pending = PendingChat(
                    session,
                    context,
                    cloud,
                    constraints,
                    task,
                    timings,
                    self._cloud_model_id,
                    lease=lease,
                    best_local=best_local,
                )
                with self._pending_lock:
                    if len(self._pending) >= self._max_pending:
                        return self._error(request_id, ErrorCode.UNAVAILABLE)
                    # Publish only after response validation and timer setup succeed.
                    pending.timer = asyncio.get_running_loop().call_later(
                        remaining, self._expire_pending, request_id
                    )
                    self._pending[request_id] = pending
                retained = True
                return response
            if isinstance(cloud, ProviderFailure):
                if best_local:
                    self._completed += 1
                    return best_local
                return self._error(request_id, cloud.error_code)
            state = self._local_state(session, context, constraints, task, timings, lease)
            finalized = self._finalize(
                state,
                cloud,
                route="cloud",
                reason=reason.value,
                manifest_version=None,
                timings=timings,
            )
            if isinstance(finalized, ChatResponse):
                self._completed += 1
            return finalized
        except asyncio.CancelledError:
            raise
        except DeadlineError:
            return self._error(request_id, ErrorCode.TIMEOUT)
        except PolicyViolation as exc:
            return self._error(request_id, exc.code)
        except Exception:
            return self._error(request_id, ErrorCode.INTERNAL)
        finally:
            self._active -= 1
            if active_task is not None:
                self._running.discard(active_task)
            if not retained:
                try:
                    if session is not None:
                        self.dispatcher.cancel(session)
                finally:
                    if context is not None:
                        context.clear()
                    if lease is not None:
                        lease.release()

    def _local_state(
        self,
        session: DispatchSession,
        context: RequestContext,
        constraints: ResponseConstraints,
        task: TaskCategory,
        timings: ResponseTimings,
        lease: RosterLease | None,
    ) -> PendingChat:
        # A non-proposal state shares the finalization/cleanup contract. It is never retained.
        return PendingChat(
            session,
            context,
            None,
            constraints,
            task,
            timings,
            self._cloud_model_id or "unavailable",
            lease=lease,
        )

    async def approval(self, request_id: UUID, decision: ApprovalRequest) -> ChatOutcome:
        try:
            decision = ApprovalRequest.model_validate(decision.model_dump(warnings=False))
        except (AttributeError, TypeError, ValueError):
            return self._error(request_id, ErrorCode.INVALID_REQUEST)
        if self._closed or self._active >= self._max_active:
            return self._error(request_id, ErrorCode.UNAVAILABLE)
        self._purge()
        with self._pending_lock:
            state = self._pending.get(request_id)
            if state is None or state.cloud_proposal.proposal_id != decision.proposal_id:
                return self._error(request_id, ErrorCode.APPROVAL_CONFLICT)
            if decision.decision == "approve" and (
                decision.sanitized_preview != state.cloud_proposal.sanitized_preview
                or decision.digest != state.cloud_proposal.digest
            ):
                return self._error(request_id, ErrorCode.APPROVAL_CONFLICT)
            self._pending.pop(request_id)
        self._active += 1
        active_task = asyncio.current_task()
        if active_task is not None:
            self._running.add(active_task)
        if state.timer:
            state.timer.cancel()
            state.timer = None
        resumed = perf_counter()
        try:
            if decision.decision != "approve":
                if state.best_local is not None:
                    self._completed += 1
                    return state.best_local
                return CancelledResponse(request_id=request_id, reason=decision.decision)
            receipt = self.dispatcher.approve(state.session, state.cloud_proposal)
            result = await self.dispatcher.dispatch(
                state.session,
                state.cloud_model_id,
                reason=state.cloud_proposal.reason,
                approval=receipt,
            )
            if isinstance(result, ProviderFailure):
                if state.best_local is not None:
                    self._completed += 1
                    return state.best_local
                return self._error(request_id, result.error_code)
            if not isinstance(result, ProviderSuccess):
                return self._error(request_id, ErrorCode.APPROVAL_CONFLICT)
            elapsed = (perf_counter() - resumed) * 1000
            timings = ResponseTimings(
                total_ms=state.timings.total_ms + elapsed,
                privacy_ms=state.timings.privacy_ms,
                routing_ms=state.timings.routing_ms,
                generation_ms=(state.timings.generation_ms or 0) + elapsed,
            )
            finalized = self._finalize(
                state,
                result,
                route="cloud",
                reason=state.cloud_proposal.reason.value,
                manifest_version=None,
                timings=timings,
            )
            if isinstance(finalized, ChatResponse):
                self._completed += 1
            return finalized
        except asyncio.CancelledError:
            raise
        except (PolicyViolation, DeadlineError):
            return self._error(request_id, ErrorCode.APPROVAL_CONFLICT)
        except Exception:
            return self._error(request_id, ErrorCode.INTERNAL)
        finally:
            self._active -= 1
            if active_task is not None:
                self._running.discard(active_task)
            self._dispose(state)

    async def aclose(self) -> None:
        self._closed = True
        active = [task for task in self._running if task is not asyncio.current_task()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        with self._pending_lock:
            states = list(self._pending.values())
            self._pending.clear()
        for state in states:
            self._dispose(state)
        await self.dispatcher.aclose()
