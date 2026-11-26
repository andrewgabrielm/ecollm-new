"""Mock-only Step 07 HTTP integration, privacy faults and write-boundary regressions."""

import asyncio
import json
import logging
import pickle
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from backend.api.main import create_app
from backend.api.schemas import ApprovalRequest
from backend.api.security import ApiSettings
from backend.broker.extensions import DisabledExtensions
from backend.broker.orchestrator import ChatOrchestrator, build_mock_broker
from backend.broker.policy_gate import PolicyGate
from backend.config.loader import ComputeBudgets, PolicyConfig
from backend.contracts import TaskCategory
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.inspector import MASKED_WARNING, OutputInspector
from backend.providers.mock import MockModel

pytestmark = pytest.mark.privacy
TOKEN = "synthetic-local-write-" + "x" * 40
ENV = {"MOCK_MODE": "1", "LOCAL_WRITE_TOKEN": TOKEN}
PRIVATE = "STU-1234-5678"


@asynccontextmanager
async def client(
    broker: ChatOrchestrator | None = None,
    *,
    transport_client: tuple[str, int] = ("127.0.0.1", 123),
) -> AsyncIterator[tuple[ChatOrchestrator, httpx.AsyncClient]]:
    app = create_app(broker=broker, environment=ENV)
    service: ChatOrchestrator = app.state.broker
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=transport_client),
            base_url="http://127.0.0.1:8000",
            headers={"X-Local-Token": TOKEN},
        ) as http,
    ):
        yield service, http


def cloud_body() -> dict[str, object]:
    return {
        "prompt": f"Return JSON describing {PRIVATE}",
        "mode": "ask_before_cloud",
        "constraints": {"required_fields": ["result"]},
    }


def approve_body(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": "approve",
        "proposal_id": proposal["proposal_id"],
        "sanitized_preview": proposal["sanitized_preview"],
        "digest": proposal["digest"],
    }


def assert_clean(broker: ChatOrchestrator) -> None:
    assert broker.pending_count == 0
    assert broker.dispatcher.gate.pending_count == 0
    assert all(
        broker.registry.active_leases(model) == 0 for model in ("mock-general", "mock-specialist")
    )


@pytest.mark.parametrize(("task", "model"), [("qa", "mock-general"), ("code", "mock-specialist")])
def test_local_a_b_routing_and_honest_metadata(task: str, model: str) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            response = await http.post(
                "/api/chat", json={"prompt": "Explain a fixed concept", "task_hint": task}
            )
            assert response.status_code == 200
            body = response.json()
            assert body["model_id"] == model and body["route"] == "local"
            assert body["mock"] is True and body["router_type"] == "rules"
            assert body["cache_status"] == "disabled"
            assert body["model_version"] == body["manifest_version"]
            assert body["timings"]["total_ms"] >= 0
            assert broker.test_call_counts["mock-cloud"] == 0
            assert_clean(broker)

    asyncio.run(scenario())


def test_pii_masks_before_dispatch_and_restores_only_to_owner_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            with caplog.at_level(logging.DEBUG):
                response = await http.post("/api/chat", json={"prompt": f"Hello {PRIVATE}"})
            assert response.status_code == 200
            assert PRIVATE in response.json()["answer"]
            assert response.json()["restored"]
            assert response.json()["privacy_counts"] == [
                {"entity_type": "SYNTHETIC_STUDENT_ID", "count": 1}
            ]
            assert PRIVATE not in caplog.text and TOKEN not in caplog.text
            assert_clean(broker)

    asyncio.run(scenario())


@pytest.mark.parametrize("explicit", [False, True])
def test_structured_validation_cascades_to_second_model(explicit: bool) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            body: dict[str, Any] = {"prompt": "Return JSON with an answer"}
            if explicit:
                body["constraints"] = {"required_fields": ["answer"]}
            response = await http.post("/api/chat", json=body)
            assert response.status_code == 200
            assert response.json()["reason"] == "local_cascade"
            assert response.json()["model_id"] == "mock-specialist"
            assert "answer" in json.loads(response.json()["answer"])
            assert broker.test_call_counts == {
                "mock-general": 1,
                "mock-specialist": 1,
                "mock-cloud": 0,
            }
            assert_clean(broker)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["unavailable", "missing", "timeout"])
def test_local_failures_and_missing_model_are_typed_and_never_silently_cloud(kind: str) -> None:
    async def scenario() -> None:
        from backend.contracts import ErrorCode

        override = {
            "mock-general": MockModel(response="fixture", failure=ErrorCode.UNAVAILABLE),
        }
        if kind == "missing":
            override = {"mock-general": MockModel(response="fixture", available=False)}
        elif kind == "timeout":
            override = {
                name: MockModel(response="fixture", latency_ms=120000)
                for name in ("mock-general", "mock-specialist")
            }
        broker = build_mock_broker(model_overrides=override)
        async with client(broker) as (_, http):
            response = await http.post("/api/chat", json={"prompt": "A fixed synthetic question"})
            assert response.status_code == (504 if kind == "timeout" else 200)
            if kind != "timeout":
                assert response.json()["model_id"] == "mock-specialist"
            if kind == "missing":
                assert broker.test_call_counts["mock-general"] == 0
            assert broker.test_call_counts["mock-cloud"] == 0
            assert_clean(broker)

    asyncio.run(scenario())


class BrokenAnalyzer:
    version = "synthetic-failure-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        raise RuntimeError("invented-private-scanner-marker")


def test_sanitization_failure_invokes_zero_providers_and_returns_no_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        broker = build_mock_broker(analyzer=BrokenAnalyzer())
        async with client(broker) as (_, http):
            response = await http.post(
                "/api/chat", json={"prompt": "invented-private-input-marker", "mode": "auto_cloud"}
            )
            assert response.status_code == 503
            assert response.json()["error_code"] == "privacy_failed"
            assert "invented-private" not in response.text + caplog.text
            assert sum(broker.test_call_counts.values()) == 0
            assert_clean(broker)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["scanner_fault", "new_pii", "unknown_token"])
def test_inspection_failures_return_masked_warning_and_stop_generation(kind: str) -> None:
    async def scenario() -> None:
        response = "Synthetic clean answer"
        if kind == "new_pii":
            response = "New contact STU-9876-4321"
        elif kind == "unknown_token":
            response = "Answer [[PII_forged_PERSON_1]]"
        broker = build_mock_broker(model_overrides={"mock-general": MockModel(response=response)})
        if kind == "scanner_fault":
            broker._inspector = OutputInspector(BrokenAnalyzer())
        prompt = (
            "A clean question" if kind == "unknown_token" else "Contact user@example.com about this"
        )
        async with client(broker) as (_, http):
            result = await http.post("/api/chat", json={"prompt": prompt, "mode": "auto_cloud"})
            assert result.status_code == 502
            assert result.json()["error_code"] == "output_blocked"
            assert result.json()["answer"] == MASKED_WARNING
            assert broker.test_call_counts["mock-cloud"] == 0
            assert broker.test_call_counts["mock-specialist"] == 0
            assert_clean(broker)

    asyncio.run(scenario())


def test_approval_exact_preview_map_lifetime_resume_and_replay() -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            response = await http.post("/api/chat", json=cloud_body())
            assert response.status_code == 202
            proposal = response.json()
            assert PRIVATE not in proposal["sanitized_preview"]
            assert "[[PII_" in proposal["sanitized_preview"]
            identity = next(iter(broker._pending))
            state = broker._pending[identity]
            assert state.context.raw_prompt == ""
            placeholders = state.context.privacy_placeholders
            assert placeholders is not None and placeholders.entry_count == 1
            assert broker.test_call_counts["mock-cloud"] == 0
            with pytest.raises(TypeError):
                pickle.dumps(state)
            resumed = await http.post(
                f"/api/chat/{proposal['request_id']}/approval", json=approve_body(proposal)
            )
            assert resumed.status_code == 200
            assert resumed.json()["route"] == "cloud" and resumed.json()["restored"]
            assert PRIVATE in json.loads(resumed.json()["answer"])["result"]
            assert resumed.json()["cache_status"] == "disabled"
            assert not placeholders.tokens and state.session.payload is None
            replay = await http.post(
                f"/api/chat/{proposal['request_id']}/approval", json=approve_body(proposal)
            )
            assert replay.status_code == 409
            assert broker.test_call_counts["mock-cloud"] == 1
            assert_clean(broker)

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["sanitized_preview", "digest", "proposal_id", "wrong_request"])
def test_tampered_approval_does_not_consume_a_valid_pending_request(field: str) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            proposal = (await http.post("/api/chat", json=cloud_body())).json()
            path = f"/api/chat/{proposal['request_id']}/approval"
            bad = approve_body(proposal)
            if field == "wrong_request":
                path = f"/api/chat/{uuid4()}/approval"
            else:
                bad[field] = {
                    "digest": "a" * 64,
                    "proposal_id": str(uuid4()),
                    "sanitized_preview": "tampered",
                }[field]
            assert (await http.post(path, json=bad)).status_code == 409
            assert broker.pending_count == 1 and broker.test_call_counts["mock-cloud"] == 0
            assert (
                await http.post(
                    f"/api/chat/{proposal['request_id']}/approval", json=approve_body(proposal)
                )
            ).status_code == 200
            assert_clean(broker)

    asyncio.run(scenario())


def test_concurrent_approval_consumes_one_record_and_dispatches_once() -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            proposal = (await http.post("/api/chat", json=cloud_body())).json()
            path = f"/api/chat/{proposal['request_id']}/approval"
            responses = await asyncio.gather(
                *(http.post(path, json=approve_body(proposal)) for _ in range(2))
            )
            assert sorted(response.status_code for response in responses) == [200, 409]
            assert broker.test_call_counts["mock-cloud"] == 1
            assert_clean(broker)

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["decline", "cancel"])
def test_decline_cancel_cleanup_and_zero_cloud_calls(decision: str) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            proposal = (await http.post("/api/chat", json=cloud_body())).json()
            state = next(iter(broker._pending.values()))
            placeholders = state.context.privacy_placeholders
            result = await http.post(
                f"/api/chat/{proposal['request_id']}/approval",
                json={"proposal_id": proposal["proposal_id"], "decision": decision},
            )
            assert result.status_code == 200 and result.json()["status"] == "cancelled"
            assert broker.test_call_counts["mock-cloud"] == 0
            assert placeholders is not None and not placeholders.tokens
            assert_clean(broker)

    asyncio.run(scenario())


def test_pending_expiry_clears_map_lease_and_both_stores_without_resume() -> None:
    async def scenario() -> None:
        broker = build_mock_broker(
            gate=PolicyGate(approval_seconds=0.03), budgets=ComputeBudgets(approval_seconds=0.03)
        )
        async with client(broker) as (_, http):
            proposal = (await http.post("/api/chat", json=cloud_body())).json()
            state = next(iter(broker._pending.values()))
            placeholders = state.context.privacy_placeholders
            await asyncio.sleep(0.06)
            assert state.session.closed and state.session.payload is None
            assert placeholders is not None and not placeholders.tokens
            assert_clean(broker)
            assert (
                await http.post(
                    f"/api/chat/{proposal['request_id']}/approval", json=approve_body(proposal)
                )
            ).status_code == 409
            assert broker.test_call_counts["mock-cloud"] == 0

    asyncio.run(scenario())


def test_policy_change_invalidates_pending_approval_and_cleans_context() -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            proposal = (await http.post("/api/chat", json=cloud_body())).json()
            broker.dispatcher.gate.set_policy(
                PolicyConfig(hard_block_entity_types=("SYNTHETIC_STUDENT_ID",))
            )
            assert (
                await http.post(
                    f"/api/chat/{proposal['request_id']}/approval", json=approve_body(proposal)
                )
            ).status_code == 409
            assert broker.test_call_counts["mock-cloud"] == 0
            assert_clean(broker)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["local_only", "ask_before_cloud", "auto_cloud"])
def test_hard_blocks_and_modes_never_leak_to_cloud(mode: str) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            body = cloud_body()
            body.update(prompt="Return JSON for CARD-0000", mode=mode)
            response = await http.post("/api/chat", json=body)
            assert response.status_code == (502 if mode == "local_only" else 403)
            assert broker.pending_count == 0 and broker.test_call_counts["mock-cloud"] == 0
            assert_clean(broker)

    asyncio.run(scenario())


@pytest.mark.parametrize("allowed", [False, True])
def test_no_local_model_cloud_requires_explicit_policy_opt_in(allowed: bool) -> None:
    from backend.api.schemas import CloudFallbackReason

    async def scenario() -> None:
        policy = PolicyConfig(
            allow_no_local_model_cloud=allowed,
            allowed_fallback_reasons=(CloudFallbackReason.NO_LOCAL_MODEL,) if allowed else (),
        )
        broker = build_mock_broker(installed_ids=(), gate=PolicyGate(policy))
        async with client(broker) as (_, http):
            response = await http.post(
                "/api/chat", json={"prompt": "A fixed question", "mode": "auto_cloud"}
            )
            assert response.status_code == (200 if allowed else 503)
            assert broker.test_call_counts["mock-cloud"] == int(allowed)
            assert_clean(broker)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["missing_key", "disallowed_reason"])
def test_cloud_ineligibility_never_proposes(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            if failure == "missing_key":
                monkeypatch.setattr(
                    broker.dispatcher._providers["mock-cloud"], "_key_available", False
                )
            else:
                broker.dispatcher.gate.set_policy(PolicyConfig(allowed_fallback_reasons=()))
            response = await http.post("/api/chat", json=cloud_body())
            assert response.status_code == (403 if failure == "missing_key" else 502)
            assert broker.pending_count == 0 and broker.test_call_counts["mock-cloud"] == 0
            assert_clean(broker)

    asyncio.run(scenario())


def test_cloud_failure_is_not_retried_and_pending_map_is_cleared() -> None:
    from backend.contracts import ErrorCode

    async def scenario() -> None:
        broker = build_mock_broker(
            model_overrides={"mock-cloud": MockModel(response="fixture", failure=ErrorCode.TIMEOUT)}
        )
        async with client(broker) as (_, http):
            proposal = (await http.post("/api/chat", json=cloud_body())).json()
            response = await http.post(
                f"/api/chat/{proposal['request_id']}/approval", json=approve_body(proposal)
            )
            assert response.status_code == 504
            assert broker.test_call_counts["mock-cloud"] == 1
            assert_clean(broker)

    asyncio.run(scenario())


def test_preferred_cloud_decline_returns_inspected_local_answer_without_new_calls() -> None:
    from backend.api.schemas import CloudFallbackReason

    async def scenario() -> None:
        policy = PolicyConfig(
            allowed_fallback_reasons=(CloudFallbackReason.CLOUD_PREFERRED,),
            cloud_preferred_categories=(TaskCategory.QA,),
        )
        broker = build_mock_broker(gate=PolicyGate(policy))
        async with client(broker) as (_, http):
            response = await http.post(
                "/api/chat", json={"prompt": f"Hello {PRIVATE}", "mode": "ask_before_cloud"}
            )
            assert response.status_code == 202
            proposal = response.json()
            assert PRIVATE in proposal["best_local_answer"]
            assert PRIVATE not in proposal["sanitized_preview"]
            result = await http.post(
                f"/api/chat/{proposal['request_id']}/approval",
                json={"proposal_id": proposal["proposal_id"], "decision": "decline"},
            )
            assert result.status_code == 200 and result.json()["route"] == "local"
            assert broker.test_call_counts["mock-cloud"] == 0
            assert_clean(broker)

    asyncio.run(scenario())


def test_pending_shutdown_erases_request_state_and_disabled_extensions_stay_disabled() -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            await http.post("/api/chat", json=cloud_body())
            state = next(iter(broker._pending.values()))
            await broker.aclose()
            assert state.context.sanitized_payload is None and state.session.payload is None
            assert_clean(broker)
            metrics = (await http.get("/api/metrics")).json()
            assert metrics["status"] == "ephemeral_only"
            assert metrics["cache_enabled"] is False and metrics["persistence_enabled"] is False
            assert (
                metrics["learned_router_enabled"] is False and metrics["estimated_cost_usd"] is None
            )
            assert (await http.post("/api/chat", json={"prompt": "hello"})).status_code == 503
        assert DisabledExtensions.model_validate({"cache_enabled": True}).cache_enabled is True
        with pytest.raises(ValidationError):
            DisabledExtensions.model_validate({"persistence_enabled": True})

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "body",
    [
        {"prompt": ""},
        {"prompt": " "},
        {"prompt": "invented-private-" * 1000},
        {"prompt": "private", "unknown": "invented-private-marker"},
        {"prompt": "private", "mode": "invalid"},
        {"prompt": "private", "options": {"max_output_tokens": True}},
        {"prompt": "private", "constraints": {"max_chars": -1}},
    ],
)
def test_safe_request_validation_errors_without_provider_calls(
    body: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            response = await http.post("/api/chat", json=body)
            assert (
                response.status_code == 422 and response.json()["error_code"] == "invalid_request"
            )
            assert "invented-private" not in response.text + caplog.text
            assert sum(broker.test_call_counts.values()) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"X-Local-Token": ""}, 403),
        ({"X-Local-Token": "wrong"}, 403),
        ({"Origin": "https://evil.invalid"}, 403),
        ({"Origin": "null"}, 403),
        ({"Host": "evil.invalid:8000"}, 400),
        ({"Host": "localhost.evil:8000"}, 400),
        ({"Host": "127.0.0.1:9999"}, 400),
        ({"Content-Type": "text/plain"}, 415),
        ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
        ({"Content-Length": "999999"}, 413),
        ({"Content-Length": "-1"}, 413),
        ({"Content-Length": "1"}, 400),
    ],
)
def test_host_origin_write_auth_and_body_boundary(headers: dict[str, str], status: int) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            response = await http.post(
                "/api/chat", json={"prompt": "invented-private-marker"}, headers=headers
            )
            assert response.status_code == status
            assert "invented-private" not in response.text and TOKEN not in response.text
            assert sum(broker.test_call_counts.values()) == 0

    asyncio.run(scenario())


def test_nonlocal_client_and_oversized_body_are_rejected() -> None:
    async def scenario() -> None:
        async with client(transport_client=("192.0.2.10", 123)) as (broker, http):
            assert (await http.get("/api/health")).status_code == 403
            assert sum(broker.test_call_counts.values()) == 0
        async with client() as (_, http):
            response = await http.post(
                "/api/chat", content=b"x" * 65537, headers={"Content-Type": "application/json"}
            )
            assert response.status_code == 413

    asyncio.run(scenario())


def test_cors_openapi_security_schema_and_no_secrets() -> None:
    async def scenario() -> None:
        async with client() as (_, http):
            response = await http.options(
                "/api/chat",
                headers={
                    "Origin": "http://127.0.0.1:8000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,x-local-token",
                },
            )
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8000"
            schema = (await http.get("/openapi.json")).json()
            assert set(schema["paths"]) == {
                "/api/chat",
                "/api/chat/{request_id}/approval",
                "/api/health",
                "/api/metrics",
                "/api/preferences",
                "/api/preferences/reset",
                "/api/catalog",
                "/api/cache",
                "/api/telemetry",
                "/api/cloud",
                "/api/cloud/presets",
                "/api/cloud/{model_id}",
                "/api/cloud/{model_id}/key",
                "/api/cloud/{model_id}/model",
                "/api/cloud/{model_id}/models",
                "/api/conversations",
                "/api/conversations/{conversation_id}",
            }
            assert schema["paths"]["/api/chat"]["post"]["security"]
            assert schema["paths"]["/api/chat/{request_id}/approval"]["post"]["security"]
            assert schema["paths"]["/api/preferences"]["put"]["security"]
            assert schema["paths"]["/api/cache"]["delete"]["security"]
            serialized = json.dumps(schema)
            assert TOKEN not in serialized and "placeholder_map" not in serialized
            assert "issuance_seal" not in serialized

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        (b"invented-private-json-marker", 422),
        (b'{"prompt":"invented-private-marker","options":{"temperature":NaN}}', 422),
        (b"\xff", 400),
    ],
)
def test_malformed_json_is_rejected_without_echoing_content(
    body: bytes, expected_status: int
) -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            response = await http.post(
                "/api/chat", content=body, headers={"Content-Type": "application/json"}
            )
            assert response.status_code == expected_status
            assert "invented-private" not in response.text
            assert sum(broker.test_call_counts.values()) == 0

    asyncio.run(scenario())


def test_body_upload_timeout_is_bounded_before_broker_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.api.security.MAX_UPLOAD_SECONDS", 0.01)

    async def stream() -> AsyncIterator[bytes]:
        await asyncio.sleep(0.03)
        yield b'{"prompt":"fixture"}'

    async def scenario() -> None:
        async with client() as (broker, http):
            response = await http.post(
                "/api/chat", content=stream(), headers={"Content-Type": "application/json"}
            )
            assert response.status_code == 408
            assert sum(broker.test_call_counts.values()) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://evil.invalid:5173",
        "http://127.0.0.1:5173/",
        "http://localhost:5173?",
        "http://local\nhost:5173",
    ],
)
def test_invalid_server_origins_fail_configuration(origin: str) -> None:
    with pytest.raises(ValidationError):
        ApiSettings(write_token=SecretStr(TOKEN), allowed_origins=(origin,))


def test_missing_local_token_and_real_mode_deferred_until_explicit_startup() -> None:
    with pytest.raises(ValueError):
        create_app(environment={"MOCK_MODE": "1"})
    app = create_app(environment={**ENV, "MOCK_MODE": "0"})
    assert app.state.broker is None
    broker = build_mock_broker()
    invalid = ApprovalRequest.model_construct(proposal_id=uuid4(), decision="invalid")
    result = asyncio.run(broker.approval(uuid4(), invalid))
    assert result.status == "error"
    assert broker.pending_count == 0


def test_preferences_api_get_and_put() -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            response = await http.get("/api/preferences")
            assert response.status_code == 200
            prefs = response.json()
            assert prefs["exact_cache_enabled"] is False
            assert prefs["mode"] == "local_only"

            response = await http.put("/api/preferences", json={"exact_cache_enabled": True})
            assert response.status_code == 200
            prefs = response.json()
            assert prefs["exact_cache_enabled"] is True
            assert broker.preferences.exact_cache_enabled is True
            assert broker.extensions.cache_enabled is True

            response = await http.get("/api/preferences")
            assert response.json()["exact_cache_enabled"] is True

    asyncio.run(scenario())


def test_cache_delete_api() -> None:
    async def scenario() -> None:
        async with client() as (broker, http):
            response = await http.delete("/api/cache")
            assert response.status_code == 200
            assert response.json()["deleted"] == 0

    asyncio.run(scenario())
