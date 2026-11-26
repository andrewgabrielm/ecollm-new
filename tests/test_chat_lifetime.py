"""Request-level deadline, cleanup and extension boundaries with deterministic fixtures."""

import asyncio
from typing import NoReturn
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.api.schemas import ApprovalRequest, ChatRequest, ErrorResponse, GenerationOptions
from backend.broker import chat as chat_module
from backend.broker.extensions import DisabledExtensions
from backend.broker.orchestrator import ChatOrchestrator, build_mock_broker
from backend.broker.policy_gate import PolicyGate
from backend.contracts import ErrorCode
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import SanitizationResult
from backend.providers.base import ProviderResult
from backend.providers.mock import MockModel, MockProvider

pytestmark = pytest.mark.privacy


class Clock:
    value = 100.0

    def __call__(self) -> float:
        return self.value


def test_privacy_time_counts_toward_compute_deadline_before_any_inference() -> None:
    async def scenario() -> None:
        clock = Clock()

        class Analyzer:
            version = "synthetic-clock-fixture-v1"

            def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
                clock.value += 121
                return ()

        broker = build_mock_broker(gate=PolicyGate(clock=clock), analyzer=Analyzer())
        response = await broker.chat(ChatRequest(prompt="A fixed question"))
        assert isinstance(response, ErrorResponse) and response.error_code == ErrorCode.TIMEOUT
        assert sum(broker.test_call_counts.values()) == 0
        assert not broker.pending_count
        assert broker.registry.active_leases("mock-general") == 0
        await broker.aclose()

    asyncio.run(scenario())


def test_overall_deadline_is_not_reset_for_second_model_or_cloud() -> None:
    async def scenario() -> None:
        clock = Clock()
        broker = build_mock_broker(gate=PolicyGate(clock=clock))
        manifest = broker.registry.snapshot().roster.entries[0].manifest

        class SlowClockProvider(MockProvider):
            async def generate(
                self,
                payload: SanitizedPayload,
                model_id: str,
                options: GenerationOptions,
                timeout_seconds: float,
            ) -> ProviderResult:
                result = await super().generate(payload, model_id, options, timeout_seconds)
                clock.value += 121
                return result

        first = SlowClockProvider(
            {"mock-general": MockModel(response="fixture", version=manifest.version)}
        )
        broker.dispatcher._providers["mock-general"] = first
        response = await broker.chat(
            ChatRequest.model_validate({"prompt": "A fixed question", "mode": "auto_cloud"})
        )
        assert isinstance(response, ErrorResponse) and response.error_code == ErrorCode.TIMEOUT
        assert first.test_call_counts["mock-general"] == 1
        assert broker.test_call_counts["mock-specialist"] == 0
        assert broker.test_call_counts["mock-cloud"] == 0
        assert not broker.pending_count
        assert all(
            broker.registry.active_leases(model) == 0
            for model in ("mock-general", "mock-specialist")
        )
        await broker.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("shutdown", [False, True])
def test_active_request_cancellation_or_shutdown_clears_all_request_state(
    shutdown: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        broker = build_mock_broker()
        manifest = broker.registry.snapshot().roster.entries[0].manifest
        entered = asyncio.Event()

        class WaitingProvider(MockProvider):
            async def generate(
                self,
                payload: SanitizedPayload,
                model_id: str,
                options: GenerationOptions,
                timeout_seconds: float,
            ) -> ProviderResult:
                entered.set()
                await asyncio.Event().wait()
                return await super().generate(payload, model_id, options, timeout_seconds)

        broker.dispatcher._providers["mock-general"] = WaitingProvider(
            {"mock-general": MockModel(response="fixture", version=manifest.version)}
        )
        original = broker._sanitizer.sanitize
        captured: list[SanitizationResult] = []

        def capture(request_id: object, text: str) -> SanitizationResult:
            from uuid import UUID

            assert isinstance(request_id, UUID)
            result = original(request_id, text)
            captured.append(result)
            return result

        monkeypatch.setattr(broker._sanitizer, "sanitize", capture)
        task = asyncio.create_task(broker.chat(ChatRequest(prompt="Hello STU-1234-5678")))
        await entered.wait()
        assert broker.registry.active_leases("mock-general") == 1
        if shutdown:
            await broker.aclose()
        else:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert captured and not captured[0].placeholders.tokens
        assert not broker.pending_count and broker._active == 0
        assert all(
            broker.registry.active_leases(model) == 0
            for model in ("mock-general", "mock-specialist")
        )
        await broker.aclose()

    asyncio.run(scenario())


def test_restoration_cannot_turn_a_valid_masked_json_response_into_invalid_output() -> None:
    async def scenario() -> None:
        name = 'Invented "Person"'

        class Analyzer:
            version = "synthetic-quoted-span-v1"

            def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
                start = text.find(name)
                return () if start < 0 else (DetectionSpan("PERSON", start, start + len(name)),)

        broker = build_mock_broker(analyzer=Analyzer())
        response = await broker.chat(
            ChatRequest.model_validate(
                {
                    "prompt": f"Return JSON for {name}",
                    "mode": "auto_cloud",
                    "constraints": {"required_fields": ["result"]},
                }
            )
        )
        assert (
            isinstance(response, ErrorResponse)
            and response.error_code == ErrorCode.VALIDATION_FAILED
        )
        assert name not in response.model_dump_json()
        assert broker.test_call_counts["mock-cloud"] == 1
        assert not broker.pending_count
        await broker.aclose()

    asyncio.run(scenario())


def test_malformed_approval_input_does_not_consume_the_real_pending_request() -> None:
    async def scenario() -> None:
        broker = build_mock_broker()
        await broker.chat(
            ChatRequest.model_validate(
                {
                    "prompt": "Return JSON",
                    "mode": "ask_before_cloud",
                    "constraints": {"required_fields": ["result"]},
                }
            )
        )
        identity = next(iter(broker._pending))
        bad = ApprovalRequest.model_construct(proposal_id=uuid4(), decision="invalid")
        response = await broker.approval(identity, bad)
        assert (
            isinstance(response, ErrorResponse) and response.error_code == ErrorCode.INVALID_REQUEST
        )
        assert broker.pending_count == 1 and broker.test_call_counts["mock-cloud"] == 0
        await broker.aclose()
        assert broker.pending_count == 0

    asyncio.run(scenario())


def test_disabled_extensions_and_unknown_factory_configuration_are_rejected() -> None:
    broker = build_mock_broker()
    with pytest.raises(ValidationError):
        ChatOrchestrator(
            broker.dispatcher,
            broker.registry,
            broker._analyzer,
            extensions=DisabledExtensions.model_construct(persistence_enabled=True),
        )
    with pytest.raises(ValueError):
        build_mock_broker(installed_ids=("unknown",))
    with pytest.raises(ValueError):
        build_mock_broker(installed_ids=("mock-general", "mock-general"))
    with pytest.raises(ValueError):
        build_mock_broker(model_overrides={"unknown": MockModel(response="fixture")})
    asyncio.run(broker.aclose())


def test_failed_approval_response_creation_releases_unpublished_request_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_response(**fields: object) -> NoReturn:
        raise ValueError("Synthetic response construction failure")

    async def scenario() -> None:
        broker = build_mock_broker()
        original = broker._sanitizer.sanitize
        captured: list[SanitizationResult] = []

        def capture(request_id: object, text: str) -> SanitizationResult:
            from uuid import UUID

            assert isinstance(request_id, UUID)
            result = original(request_id, text)
            captured.append(result)
            return result

        monkeypatch.setattr(broker._sanitizer, "sanitize", capture)
        monkeypatch.setattr(chat_module, "ApprovalRequiredResponse", fail_response)
        response = await broker.chat(
            ChatRequest.model_validate(
                {
                    "prompt": "Return JSON about STU-1234-5678",
                    "mode": "ask_before_cloud",
                    "constraints": {"required_fields": ["result"]},
                }
            )
        )
        assert isinstance(response, ErrorResponse) and response.error_code == ErrorCode.INTERNAL
        assert captured and not captured[0].placeholders.tokens
        assert not broker.pending_count and not broker.dispatcher.gate.pending_count
        assert all(
            broker.registry.active_leases(model) == 0
            for model in ("mock-general", "mock-specialist")
        )
        assert broker.test_call_counts["mock-cloud"] == 0
        await broker.aclose()

    asyncio.run(scenario())
