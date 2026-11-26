"""Policy-to-HTTP integration: all traffic is intercepted, never sent to endpoints."""

import asyncio
import json
import re
from uuid import uuid4

import httpx
import pytest
import respx

from backend.api.schemas import CloudFallbackReason
from backend.broker.context import RequestContext
from backend.broker.orchestrator import GuardedDispatcher
from backend.broker.policy_gate import (
    ApprovalReceipt,
    CloudProposal,
    PolicyCheck,
    PolicyGate,
)
from backend.contracts import ErrorCode, ExecutionMode
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.recognisers import synthetic_spans
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer
from backend.providers.base import ProviderDescriptor, ProviderFailure, ProviderSuccess
from backend.providers.cloud_openai_compat import CloudDestination, CloudProvider
from backend.providers.ollama_slm import OllamaModel, OllamaProvider

pytestmark = pytest.mark.privacy

CLOUD = "https://synthetic-cloud.invalid/v1/chat/completions"
LOCAL = "http://127.0.0.1:11434"
DIGEST = "a" * 64


class FixtureAnalyzer:
    version = "synthetic-egress-fixture-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        cards = tuple(
            DetectionSpan("CREDIT_CARD", match.start(), match.end())
            for match in re.finditer("CARD-0000", text)
        )
        return (*cards, *synthetic_spans(text))


def sanitized(text: str = "Synthetic contact STU-1234-5678") -> SanitizedPayload:
    return Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), text).payload


def cloud_provider() -> CloudProvider:
    return CloudProvider(
        CloudDestination(
            endpoint=CLOUD,
            model_id="synthetic-cloud",
            model_version="fixture-v1",
            runtime_model="fixture-v1",
            api_key_variable="CLOUD_API_KEY",
        ),
        environment={"CLOUD_API_KEY": "invented-local-test-credential"},
    )


def cloud_response() -> dict[str, object]:
    return {
        "model": "fixture-v1",
        "choices": [{"message": {"content": "Synthetic cloud answer"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 3},
    }


def test_local_only_rejects_cloud_before_any_http() -> None:
    async def scenario() -> None:
        provider = cloud_provider()
        try:
            dispatcher = GuardedDispatcher({"synthetic-cloud": provider})
            session = dispatcher.new_request(sanitized())
            with respx.mock as router:
                result = await dispatcher.dispatch(
                    session, "synthetic-cloud", reason=CloudFallbackReason.LOCAL_ERROR
                )
                assert isinstance(result, ProviderFailure)
                assert result.error_code == ErrorCode.POLICY_BLOCKED
                assert len(router.calls) == 0
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_asked_cloud_preview_is_exact_then_one_approved_post_and_no_replay() -> None:
    async def scenario() -> None:
        provider = cloud_provider()
        try:
            dispatcher = GuardedDispatcher({"synthetic-cloud": provider})
            payload = sanitized()
            session = dispatcher.new_request(payload, mode=ExecutionMode.ASK_BEFORE_CLOUD)
            with respx.mock as router:
                route = router.post(CLOUD).respond(200, json=cloud_response())
                proposal = await dispatcher.dispatch(
                    session, "synthetic-cloud", reason=CloudFallbackReason.LOCAL_ERROR
                )
                assert isinstance(proposal, CloudProposal)
                assert proposal.sanitized_preview == payload.text
                assert "STU-1234-5678" not in proposal.sanitized_preview
                assert route.call_count == 0
                receipt = dispatcher.approve(session, proposal)
                result = await dispatcher.dispatch(
                    session,
                    "synthetic-cloud",
                    reason=CloudFallbackReason.LOCAL_ERROR,
                    approval=receipt,
                )
                assert isinstance(result, ProviderSuccess)
                assert route.call_count == 1
                body = json.loads(route.calls.last.request.content)
                assert body["messages"][0]["content"] == proposal.sanitized_preview
                assert "STU-1234-5678" not in route.calls.last.request.content.decode()
                replay = await dispatcher.dispatch(
                    session,
                    "synthetic-cloud",
                    reason=CloudFallbackReason.LOCAL_ERROR,
                    approval=receipt,
                )
                assert isinstance(replay, ProviderFailure)
                assert replay.error_code == ErrorCode.APPROVAL_CONFLICT
                assert route.call_count == 1
        finally:
            await provider.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("toggle_back", [False, True])
def test_pending_mode_change_cancels_approval_context_and_cannot_escape_to_auto(
    toggle_back: bool,
) -> None:
    async def scenario() -> None:
        provider = cloud_provider()
        gate = PolicyGate()
        try:
            dispatcher = GuardedDispatcher({"synthetic-cloud": provider}, gate=gate)
            raw = "Synthetic contact STU-1234-5678"
            result = Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), raw)
            context = RequestContext(
                result.payload.request_id,
                raw,
                sanitized_payload=result.payload,
                privacy_placeholders=result.placeholders,
            )
            session = dispatcher.new_request(
                result.payload, mode=ExecutionMode.ASK_BEFORE_CLOUD, context=context
            )
            with respx.mock as router:
                proposal = await dispatcher.dispatch(
                    session, "synthetic-cloud", reason=CloudFallbackReason.LOCAL_ERROR
                )
                assert isinstance(proposal, CloudProposal)
                assert gate.pending_count == 1
                receipt = dispatcher.approve(session, proposal) if toggle_back else None
                session.set_mode(ExecutionMode.AUTO_CLOUD)
                if toggle_back:
                    session.set_mode(ExecutionMode.ASK_BEFORE_CLOUD)
                outcome = await dispatcher.dispatch(
                    session,
                    "synthetic-cloud",
                    reason=CloudFallbackReason.LOCAL_ERROR,
                    approval=receipt,
                )
                assert isinstance(outcome, ProviderFailure)
                assert outcome.error_code == ErrorCode.APPROVAL_CONFLICT
                assert gate.pending_count == 0
                assert session.closed
                assert session.payload is None
                assert context.raw_prompt == ""
                assert context.privacy_placeholders is None
                assert result.placeholders.entry_count == 0
                assert len(router.calls) == 0
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_auto_cloud_still_requires_eligible_sanitized_input_and_reason() -> None:
    async def scenario() -> None:
        provider = cloud_provider()
        try:
            dispatcher = GuardedDispatcher({"synthetic-cloud": provider})
            session = dispatcher.new_request(sanitized(), mode=ExecutionMode.AUTO_CLOUD)
            with respx.mock as router:
                route = router.post(CLOUD).respond(200, json=cloud_response())
                result = await dispatcher.dispatch(
                    session, "synthetic-cloud", reason=CloudFallbackReason.LOCAL_ERROR
                )
                assert isinstance(result, ProviderSuccess)
                assert route.call_count == 1
        finally:
            await provider.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", [ExecutionMode.ASK_BEFORE_CLOUD, ExecutionMode.AUTO_CLOUD])
def test_hard_blocked_types_never_propose_or_send_cloud_http(mode: ExecutionMode) -> None:
    async def scenario() -> None:
        provider = cloud_provider()
        gate = PolicyGate()
        try:
            dispatcher = GuardedDispatcher({"synthetic-cloud": provider}, gate=gate)
            session = dispatcher.new_request(sanitized("Synthetic CARD-0000 request"), mode=mode)
            with respx.mock as router:
                result = await dispatcher.dispatch(
                    session, "synthetic-cloud", reason=CloudFallbackReason.LOCAL_ERROR
                )
                assert isinstance(result, ProviderFailure)
                assert result.error_code == ErrorCode.POLICY_BLOCKED
                assert gate.pending_count == 0
                assert len(router.calls) == 0
        finally:
            await provider.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["privacy_failure", "untrusted_roundtrip", "counts_tampered"])
def test_failed_or_tampered_privacy_makes_zero_provider_http_calls(kind: str) -> None:
    async def scenario() -> None:
        provider = cloud_provider()
        try:
            dispatcher = GuardedDispatcher({"synthetic-cloud": provider})
            original = sanitized("Synthetic CARD-0000 request")
            payload: SanitizedPayload | None
            if kind == "privacy_failure":
                payload = None
            elif kind == "untrusted_roundtrip":
                payload = SanitizedPayload.model_validate_json(original.model_dump_json())
            else:
                payload = original.model_copy(update={"privacy_counts": ()})
            session = dispatcher.new_request(
                payload, request_id=original.request_id, mode=ExecutionMode.AUTO_CLOUD
            )
            with respx.mock as router:
                result = await dispatcher.dispatch(
                    session, "synthetic-cloud", reason=CloudFallbackReason.LOCAL_ERROR
                )
                assert isinstance(result, ProviderFailure)
                assert result.error_code == ErrorCode.POLICY_BLOCKED
                assert len(router.calls) == 0
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_local_connection_retry_is_reauthorized_before_second_attempt() -> None:
    class CountingGate(PolicyGate):
        authorizations = 0

        def authorize(
            self,
            check: PolicyCheck,
            target: ProviderDescriptor,
            reason: CloudFallbackReason | None = None,
            approval: ApprovalReceipt | None = None,
        ) -> None:
            self.authorizations += 1
            return super().authorize(check, target, reason, approval)

    async def scenario() -> None:
        provider = OllamaProvider(
            {"synthetic-local": OllamaModel(runtime_tag="synthetic:fixture", pinned_digest=DIGEST)}
        )
        gate = CountingGate()
        try:
            dispatcher = GuardedDispatcher({"synthetic-local": provider}, gate=gate)
            session = dispatcher.new_request(sanitized())
            with respx.mock as router:
                tags = router.get(f"{LOCAL}/api/tags").mock(
                    side_effect=[
                        httpx.ConnectError("synthetic-connection-fault"),
                        httpx.Response(
                            200, json={"models": [{"name": "synthetic:fixture", "digest": DIGEST}]}
                        ),
                    ]
                )
                generated = router.post(f"{LOCAL}/api/generate").respond(
                    200,
                    json={
                        "model": "synthetic:fixture",
                        "response": "Synthetic local answer",
                        "done": True,
                    },
                )
                result = await dispatcher.dispatch(session, "synthetic-local")
                assert isinstance(result, ProviderSuccess)
                assert tags.call_count == 2
                assert generated.call_count == 1
                assert gate.authorizations == 2
        finally:
            await provider.aclose()

    asyncio.run(scenario())
