"""Guarded Step 05 dispatch: synthetic providers, fake clocks and no endpoints."""

import asyncio
import pickle
from collections.abc import Callable
from uuid import uuid4

import pytest

from backend.api.schemas import CloudFallbackReason, GenerationOptions
from backend.broker.context import RequestContext
from backend.broker.orchestrator import GuardedDispatcher
from backend.broker.policy_gate import (
    ApprovalReceipt,
    CloudProposal,
    PolicyCheck,
    PolicyGate,
    PolicyViolation,
)
from backend.config.loader import ComputeBudgets, PolicyConfig
from backend.contracts import ErrorCode, ExecutionMode
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.recognisers import synthetic_spans
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer
from backend.providers.base import (
    ProviderDescriptor,
    ProviderFailure,
    ProviderHealth,
    ProviderResult,
    ProviderSuccess,
)
from backend.providers.mock import MockModel, MockProvider

pytestmark = pytest.mark.privacy
MODEL = "synthetic"
VERSION = "fixture-v1"
REASON = CloudFallbackReason.LOCAL_ERROR


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_cancel_clears_pending_state_even_when_clock_fails() -> None:
    async def scenario() -> None:
        clock = Clock()
        gate = PolicyGate(clock=clock)
        provider = MockProvider(
            {MODEL: MockModel(response="fixture", version=VERSION)},
            route="cloud",
            key_available=True,
        )
        dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
        result = Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), "STU-1234-5678")
        context = RequestContext(
            result.payload.request_id,
            "",
            sanitized_payload=result.payload,
            privacy_placeholders=result.placeholders,
        )
        session = dispatcher.new_request(
            result.payload,
            mode=ExecutionMode.ASK_BEFORE_CLOUD,
            context=context,
        )
        proposal = await dispatcher.dispatch(session, MODEL, reason=REASON)
        assert isinstance(proposal, CloudProposal)
        clock.value = float("nan")
        dispatcher.cancel(session)
        assert session.closed and session.payload is None
        assert not result.placeholders.tokens
        assert context.sanitized_payload is None
        clock.value = 101.0
        assert gate.pending_count == 0
        assert not provider.test_call_counts

    asyncio.run(scenario())


class FixtureAnalyzer:
    version = "guarded-synthetic-fixture-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        return synthetic_spans(text)


def sanitized(text: str = "Explain a fixed synthetic concept") -> SanitizedPayload:
    return Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), text).payload


def success(**changes: object) -> ProviderSuccess:
    return ProviderSuccess.model_validate(
        {"model_id": MODEL, "model_version": VERSION, "text": "fixture", "latency_ms": 0, **changes}
    )


def failed(code: ErrorCode, *, retry: bool = False) -> ProviderFailure:
    return ProviderFailure(
        model_id=MODEL,
        model_version=VERSION,
        latency_ms=0,
        error_code=code,
        retryable_connection=retry,
    )


class CountingGate(PolicyGate):
    def __init__(self, clock: Clock | None = None, *, deny_attempt: int | None = None) -> None:
        if clock is None:
            super().__init__()
        else:
            super().__init__(clock=clock)
        self.authorizations: list[float] = []
        self.deny_attempt = deny_attempt

    def authorize(
        self,
        check: PolicyCheck,
        target: ProviderDescriptor,
        reason: CloudFallbackReason | None = None,
        approval: ApprovalReceipt | None = None,
    ) -> None:
        self.authorizations.append(check.remaining_seconds)
        if len(self.authorizations) == self.deny_attempt:
            raise PolicyViolation()
        super().authorize(check, target, reason, approval)


class ScriptedProvider(MockProvider):
    """Test-only metadata/call counters; never stores incoming payload or options."""

    def __init__(
        self,
        outcomes: list[ProviderResult | Exception],
        *,
        cloud: bool = False,
        hooks: list[Callable[[], None]] | None = None,
    ) -> None:
        super().__init__(
            {MODEL: MockModel(response="fixture", version=VERSION)},
            route="cloud" if cloud else "local",
            key_available=cloud,
        )
        self.outcomes = outcomes
        self.hooks = hooks or []
        self.timeouts: list[float] = []
        self.health_calls = 0
        self.warmup_calls = 0

    async def generate(
        self,
        payload: SanitizedPayload,
        model_id: str,
        options: GenerationOptions,
        timeout_seconds: float,
    ) -> ProviderResult:
        self.timeouts.append(timeout_seconds)
        if self.hooks:
            self.hooks.pop(0)()
        await asyncio.sleep(0)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def warm_up(
        self, synthetic_payload: SanitizedPayload, model_id: str, timeout_seconds: float
    ) -> ProviderResult:
        self.warmup_calls += 1
        return await self.generate(
            synthetic_payload, model_id, GenerationOptions(max_output_tokens=1), timeout_seconds
        )

    async def health(self, model_id: str) -> ProviderHealth:
        self.health_calls += 1
        return await super().health(model_id)


def test_each_connection_retry_is_reauthorized_and_shares_one_local_timeout() -> None:
    clock = Clock()
    gate = CountingGate(clock)
    provider = ScriptedProvider(
        [failed(ErrorCode.UNAVAILABLE, retry=True), success()],
        hooks=[lambda: clock.advance(12)],
    )
    dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
    session = dispatcher.new_request(sanitized())
    outcome = asyncio.run(dispatcher.dispatch(session, MODEL))
    assert isinstance(outcome, ProviderSuccess)
    assert provider.timeouts == [60, 48]
    assert gate.authorizations == [120, 108]
    assert session.budget.remaining_seconds == 108
    dispatcher.cancel(session)


def test_second_connection_failure_does_not_gain_a_third_attempt() -> None:
    gate = CountingGate(Clock())
    provider = ScriptedProvider([failed(ErrorCode.UNAVAILABLE, retry=True)] * 3)
    dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
    outcome = asyncio.run(dispatcher.dispatch(dispatcher.new_request(sanitized()), MODEL))
    assert isinstance(outcome, ProviderFailure)
    assert len(provider.timeouts) == len(gate.authorizations) == 2
    assert len(provider.outcomes) == 1


@pytest.mark.parametrize("code", [ErrorCode.UNAVAILABLE, ErrorCode.TIMEOUT, ErrorCode.INTERNAL])
def test_nonconnection_local_failures_are_never_retried(code: ErrorCode) -> None:
    gate = CountingGate(Clock())
    provider = ScriptedProvider([failed(code), success()])
    dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
    outcome = asyncio.run(dispatcher.dispatch(dispatcher.new_request(sanitized()), MODEL))
    assert isinstance(outcome, ProviderFailure)
    assert outcome.error_code == code
    assert len(provider.timeouts) == len(gate.authorizations) == 1


def test_cloud_connection_failure_never_retries_even_if_provider_marks_it_retryable() -> None:
    gate = CountingGate(Clock())
    provider = ScriptedProvider([failed(ErrorCode.UNAVAILABLE, retry=True), success()], cloud=True)
    dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
    session = dispatcher.new_request(sanitized(), mode=ExecutionMode.AUTO_CLOUD)
    outcome = asyncio.run(dispatcher.dispatch(session, MODEL, reason=REASON))
    assert isinstance(outcome, ProviderFailure)
    assert len(provider.timeouts) == len(gate.authorizations) == 1


def test_gate_can_revoke_permission_before_connection_retry() -> None:
    gate = CountingGate(Clock(), deny_attempt=2)
    provider = ScriptedProvider([failed(ErrorCode.UNAVAILABLE, retry=True), success()])
    dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
    outcome = asyncio.run(dispatcher.dispatch(dispatcher.new_request(sanitized()), MODEL))
    assert isinstance(outcome, ProviderFailure)
    assert outcome.error_code == ErrorCode.POLICY_BLOCKED
    assert len(gate.authorizations) == 2
    assert len(provider.timeouts) == 1


def test_exhausted_local_attempt_window_blocks_retry_without_resetting_overall_budget() -> None:
    clock = Clock()
    gate = CountingGate(clock)
    provider = ScriptedProvider(
        [failed(ErrorCode.UNAVAILABLE, retry=True), success()], hooks=[lambda: clock.advance(61)]
    )
    dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
    session = dispatcher.new_request(sanitized())
    outcome = asyncio.run(dispatcher.dispatch(session, MODEL))
    assert isinstance(outcome, ProviderFailure)
    assert outcome.error_code == ErrorCode.TIMEOUT
    assert provider.timeouts == [60]
    assert session.budget.remaining_seconds == 59


def test_expired_request_has_no_gate_or_provider_calls_and_closes_context() -> None:
    clock = Clock()
    gate = CountingGate(clock)
    provider = ScriptedProvider([success()])
    dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
    payload = sanitized()
    context = RequestContext(
        payload.request_id, "invented-private-marker", sanitized_payload=payload
    )
    session = dispatcher.new_request(payload, context=context)
    clock.advance(120)
    result = asyncio.run(dispatcher.dispatch(session, MODEL))
    assert isinstance(result, ProviderFailure) and result.error_code == ErrorCode.TIMEOUT
    assert not provider.timeouts
    assert not gate.authorizations
    assert session.closed
    assert context.raw_prompt == ""


def test_approval_pause_excludes_human_wait_but_resume_uses_only_bounded_remainder() -> None:
    async def scenario() -> None:
        clock = Clock()
        gate = CountingGate(clock)
        provider = ScriptedProvider([success()], cloud=True, hooks=[lambda: clock.advance(10)])
        dispatcher = GuardedDispatcher(
            {MODEL: provider}, gate=gate, budgets=ComputeBudgets(resume_seconds=30)
        )
        session = dispatcher.new_request(sanitized(), mode=ExecutionMode.ASK_BEFORE_CLOUD)
        clock.advance(20)
        proposal = await dispatcher.dispatch(session, MODEL, reason=REASON)
        assert isinstance(proposal, CloudProposal)
        assert session.budget.remaining_seconds == 100
        clock.advance(100)
        receipt = dispatcher.approve(session, proposal)
        outcome = await dispatcher.dispatch(session, MODEL, reason=REASON, approval=receipt)
        assert isinstance(outcome, ProviderSuccess)
        assert provider.timeouts == [30]
        assert session.budget.remaining_seconds == 20
        replay = await dispatcher.dispatch(session, MODEL, reason=REASON, approval=receipt)
        assert isinstance(replay, ProviderFailure)
        assert replay.error_code == ErrorCode.APPROVAL_CONFLICT
        assert len(provider.timeouts) == 1
        dispatcher.cancel(session)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["mode", "policy"])
def test_approved_receipt_cannot_survive_mode_or_policy_change(change: str) -> None:
    async def scenario() -> None:
        gate = CountingGate(Clock())
        provider = ScriptedProvider([success()], cloud=True)
        dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
        session = dispatcher.new_request(sanitized(), mode=ExecutionMode.ASK_BEFORE_CLOUD)
        proposal = await dispatcher.dispatch(session, MODEL, reason=REASON)
        assert isinstance(proposal, CloudProposal)
        receipt = dispatcher.approve(session, proposal)
        if change == "mode":
            session.set_mode(ExecutionMode.AUTO_CLOUD)
        else:
            gate.set_policy(
                PolicyConfig(hard_block_entity_types=("CREDIT_CARD", "US_SSN", "PERSON"))
            )
        result = await dispatcher.dispatch(session, MODEL, reason=REASON, approval=receipt)
        assert isinstance(result, ProviderFailure)
        assert result.error_code == ErrorCode.APPROVAL_CONFLICT
        assert not provider.timeouts
        dispatcher.cancel(session)

    asyncio.run(scenario())


def test_concurrent_same_receipt_dispatch_has_exactly_one_provider_call() -> None:
    async def scenario() -> None:
        provider = MockProvider(
            {MODEL: MockModel(response="fixture", version=VERSION)},
            route="cloud",
            key_available=True,
        )
        dispatcher = GuardedDispatcher({MODEL: provider})
        session = dispatcher.new_request(sanitized(), mode=ExecutionMode.ASK_BEFORE_CLOUD)
        proposal = await dispatcher.dispatch(session, MODEL, reason=REASON)
        assert isinstance(proposal, CloudProposal)
        receipt = dispatcher.approve(session, proposal)
        results = await asyncio.gather(
            dispatcher.dispatch(session, MODEL, reason=REASON, approval=receipt),
            dispatcher.dispatch(session, MODEL, reason=REASON, approval=receipt),
        )
        assert sum(isinstance(result, ProviderSuccess) for result in results) == 1
        failures = [result for result in results if isinstance(result, ProviderFailure)]
        assert len(failures) == 1 and failures[0].error_code == ErrorCode.APPROVAL_CONFLICT
        assert provider.test_call_counts == {MODEL: 1}
        dispatcher.cancel(session)

    asyncio.run(scenario())


@pytest.mark.parametrize("expire", [False, True])
def test_pending_cancel_or_scheduled_expiry_removes_private_request_state(expire: bool) -> None:
    async def scenario() -> None:
        result = Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), "Invented STU-1234-5678")
        context = RequestContext(
            result.payload.request_id,
            "Invented STU-1234-5678",
            sanitized_payload=result.payload,
            privacy_placeholders=result.placeholders,
        )
        budgets = ComputeBudgets(approval_seconds=0.02)
        provider = MockProvider(
            {MODEL: MockModel(response="fixture")}, route="cloud", key_available=True
        )
        dispatcher = GuardedDispatcher({MODEL: provider}, budgets=budgets)
        session = dispatcher.new_request(
            result.payload, mode=ExecutionMode.ASK_BEFORE_CLOUD, context=context
        )
        proposal = await dispatcher.dispatch(session, MODEL, reason=REASON)
        assert isinstance(proposal, CloudProposal)
        if expire:
            await asyncio.sleep(0.05)
        else:
            dispatcher.cancel(session)
        assert session.closed
        assert session.payload is None
        assert context.raw_prompt == ""
        assert context.privacy_placeholders is None
        assert result.placeholders.entry_count == 0
        assert dispatcher.gate.pending_count == 0
        assert not provider.test_call_counts

    asyncio.run(scenario())


def test_fixed_synthetic_warmup_is_gated_and_normal_request_cannot_use_probe_path() -> None:
    gate = CountingGate(Clock())
    provider = ScriptedProvider([success()])
    dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
    ordinary = dispatcher.new_request(sanitized())
    blocked = asyncio.run(dispatcher.warm_up(ordinary, MODEL))
    assert isinstance(blocked, ProviderFailure) and blocked.error_code == ErrorCode.POLICY_BLOCKED
    assert not gate.authorizations and not provider.timeouts
    warmup = dispatcher.new_warmup()
    outcome = asyncio.run(dispatcher.warm_up(warmup, MODEL))
    assert isinstance(outcome, ProviderSuccess)
    assert provider.warmup_calls == 1
    assert len(gate.authorizations) == 1
    dispatcher.cancel(ordinary)
    dispatcher.cancel(warmup)


def test_cloud_warmup_cannot_bypass_local_only_mode() -> None:
    gate = CountingGate(Clock())
    provider = ScriptedProvider([success()], cloud=True)
    dispatcher = GuardedDispatcher({MODEL: provider}, gate=gate)
    warmup = dispatcher.new_warmup()
    result = asyncio.run(dispatcher.warm_up(warmup, MODEL, reason=REASON))
    assert isinstance(result, ProviderFailure) and result.error_code == ErrorCode.POLICY_BLOCKED
    assert len(gate.authorizations) == 1 and not provider.timeouts
    dispatcher.cancel(warmup)


def test_health_snapshot_ttl_and_generation_failure_invalidation() -> None:
    async def scenario() -> None:
        clock = Clock()
        provider = ScriptedProvider([failed(ErrorCode.UNAVAILABLE)])
        dispatcher = GuardedDispatcher({MODEL: provider}, gate=CountingGate(clock))
        assert (await dispatcher.health(MODEL)).available
        clock.advance(9)
        assert (await dispatcher.health(MODEL)).available
        assert provider.health_calls == 1
        clock.advance(1)
        assert (await dispatcher.health(MODEL)).available
        assert provider.health_calls == 2
        outcome = await dispatcher.dispatch(dispatcher.new_request(sanitized()), MODEL)
        assert isinstance(outcome, ProviderFailure)
        assert (await dispatcher.health(MODEL)).available
        assert provider.health_calls == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (RuntimeError("invented-private-provider-marker"), ErrorCode.INTERNAL),
        (success(model_id="other"), ErrorCode.VALIDATION_FAILED),
        (success(model_version="other"), ErrorCode.VALIDATION_FAILED),
    ],
)
def test_provider_failures_or_wrong_result_identity_are_safe_typed_failures(
    outcome: ProviderResult | Exception, code: ErrorCode, caplog: pytest.LogCaptureFixture
) -> None:
    provider = ScriptedProvider([outcome])
    dispatcher = GuardedDispatcher({MODEL: provider})
    result = asyncio.run(dispatcher.dispatch(dispatcher.new_request(sanitized()), MODEL))
    assert isinstance(result, ProviderFailure) and result.error_code == code
    assert "invented-private" not in repr(result)
    assert "invented-private" not in caplog.text


def test_changed_descriptor_or_removed_cloud_credentials_blocks_provider_entry() -> None:
    async def scenario() -> None:
        for change in ("destination", "credential"):
            provider = MockProvider(
                {MODEL: MockModel(response="fixture")}, route="cloud", key_available=True
            )
            dispatcher = GuardedDispatcher({MODEL: provider})
            session = dispatcher.new_request(sanitized(), mode=ExecutionMode.AUTO_CLOUD)
            if change == "destination":
                provider._endpoint = "https://other.invalid/v1/chat/completions"
            else:
                provider._key_available = False
            result = await dispatcher.dispatch(session, MODEL, reason=REASON)
            assert (
                isinstance(result, ProviderFailure)
                and result.error_code == ErrorCode.POLICY_BLOCKED
            )
            assert not provider.test_call_counts

    asyncio.run(scenario())


def test_dispatch_session_repr_and_serialization_reject_private_state() -> None:
    dispatcher = GuardedDispatcher({MODEL: MockProvider({MODEL: MockModel(response="fixture")})})
    session = dispatcher.new_request(sanitized("invented-private-session-marker"))
    assert "invented-private" not in repr(session)
    with pytest.raises(TypeError) as caught:
        pickle.dumps(session)
    assert "invented-private" not in str(caught.value)
    dispatcher.cancel(session)
