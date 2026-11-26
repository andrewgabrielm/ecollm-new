"""Step 04 adapter checks: synthetic payloads, intercepted HTTP, no real endpoints."""

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx

from backend.api.schemas import GenerationOptions
from backend.contracts import ErrorCode
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer
from backend.providers.base import ProviderFailure, ProviderResult, ProviderSuccess
from backend.providers.cloud_openai_compat import CloudDestination, CloudProvider
from backend.providers.mock import MockModel, MockProvider
from backend.providers.ollama_slm import OllamaModel, OllamaProvider, OllamaRuntimeControl
from backend.providers.transport import create_client

CLOUD_URL = "https://synthetic.invalid/v1/chat/completions"
OLLAMA_URL = "http://127.0.0.1:11434"
DIGEST = "a" * 64
KEY = "invented-credential-" + "fixture"


class CleanAnalyzer:
    version = "synthetic-provider-fixture-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        return ()


def payload() -> SanitizedPayload:
    return Sanitizer(CleanAnalyzer()).sanitize(uuid4(), "invented-private-prompt-marker").payload


def destination(**updates: Any) -> CloudDestination:
    return CloudDestination.model_validate(
        {
            "endpoint": CLOUD_URL,
            "model_id": "synthetic-cloud",
            "model_version": "fixture-v1",
            "runtime_model": "fixture-v1",
            "api_key_variable": "CLOUD_API_KEY",
            **updates,
        }
    )


def completion(**updates: Any) -> dict[str, Any]:
    return {
        "model": "fixture-v1",
        "choices": [{"message": {"content": "invented answer"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        **updates,
    }


def listed(digest: str = DIGEST) -> dict[str, Any]:
    return {"models": [{"name": "synthetic:fixture", "digest": digest}]}


def generated(**updates: Any) -> dict[str, Any]:
    return {
        "model": "synthetic:fixture",
        "response": "invented local answer",
        "done": True,
        "total_duration": 9_000_000,
        "load_duration": 1_000_000,
        "prompt_eval_duration": 2_000_000,
        "eval_duration": 6_000_000,
        "prompt_eval_count": 4,
        "eval_count": 3,
        **updates,
    }


def ollama() -> OllamaProvider:
    return OllamaProvider(
        {"synthetic-local": OllamaModel(runtime_tag="synthetic:fixture", pinned_digest=DIGEST)}
    )


async def cloud_generate(
    value: SanitizedPayload | None = None, *, key: str = KEY, timeout: float = 2
) -> ProviderResult:
    provider = CloudProvider(destination(), environment={"CLOUD_API_KEY": key})
    try:
        return await provider.generate(
            payload() if value is None else value,
            "synthetic-cloud",
            GenerationOptions(seed=3),
            timeout,
        )
    finally:
        await provider.aclose()


async def local_generate(value: SanitizedPayload | None = None) -> ProviderResult:
    provider = ollama()
    try:
        return await provider.generate(
            payload() if value is None else value, "synthetic-local", GenerationOptions(), 2
        )
    finally:
        await provider.aclose()


def test_cloud_success_nonstreaming_exact_destination_and_tokens() -> None:
    with respx.mock as router:
        route = router.post(CLOUD_URL).respond(200, json=completion())
        result = asyncio.run(cloud_generate())
    assert isinstance(result, ProviderSuccess)
    assert result.text == "invented answer"
    assert result.model_version == "fixture-v1"
    assert result.token_metrics is not None
    assert result.token_metrics.input_tokens == 12
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "invented-private-prompt-marker"}]
    assert body["seed"] == 3
    assert request.headers["Authorization"] == "Bearer " + KEY
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ErrorCode.POLICY_BLOCKED),
        (403, ErrorCode.POLICY_BLOCKED),
        (429, ErrorCode.UNAVAILABLE),
        (500, ErrorCode.UNAVAILABLE),
        (400, ErrorCode.INVALID_REQUEST),
        (422, ErrorCode.INVALID_REQUEST),
    ],
)
def test_cloud_errors_are_typed_and_never_retried(status: int, expected: ErrorCode) -> None:
    with respx.mock as router:
        route = router.post(CLOUD_URL).respond(status, text="invented-upstream-private-marker")
        result = asyncio.run(cloud_generate())
    assert isinstance(result, ProviderFailure)
    assert result.error_code == expected
    assert route.call_count == 1
    assert "invented-upstream-private-marker" not in repr(result)


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
def test_cloud_transport_failures_redact_details_and_do_not_retry(
    exception: type[httpx.RequestError], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG), respx.mock as router:
        route = router.post(CLOUD_URL).mock(side_effect=exception("invented-private-error"))
        result = asyncio.run(cloud_generate())
    assert isinstance(result, ProviderFailure)
    assert result.error_code == (
        ErrorCode.TIMEOUT if exception is httpx.ReadTimeout else ErrorCode.UNAVAILABLE
    )
    assert route.call_count == 1
    assert KEY not in caplog.text
    assert "invented-private-prompt-marker" not in caplog.text
    assert "invented-private-error" not in repr(result) + caplog.text


def test_cloud_redirect_is_blocked_without_forwarding_authorization() -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.post(CLOUD_URL).respond(307, headers={"Location": "https://other.invalid/"})
        unexpected = router.post("https://other.invalid/").respond(200, json=completion())
        result = asyncio.run(cloud_generate())
    assert isinstance(result, ProviderFailure)
    assert result.error_code == ErrorCode.POLICY_BLOCKED
    assert route.call_count == 1
    assert not unexpected.called


@pytest.mark.parametrize(
    "body",
    [
        "invalid-json",
        {"model": "fixture-v1", "choices": []},
        completion(model="different-version"),
        completion(choices=[{"message": {"content": "partial"}, "finish_reason": "length"}]),
        completion(choices=[{"message": {"content": ""}, "finish_reason": "stop"}]),
    ],
)
def test_cloud_malformed_or_incomplete_results_are_not_success(body: object) -> None:
    with respx.mock as router:
        router.post(CLOUD_URL).respond(200, content=json.dumps(body).encode())
        result = asyncio.run(cloud_generate())
    assert isinstance(result, ProviderFailure)
    assert result.error_code == ErrorCode.VALIDATION_FAILED


@pytest.mark.parametrize("kind", ["missing-key", "deserialized", "malformed-copy", "constructed"])
def test_cloud_untrusted_or_unconfigured_inputs_make_zero_http_calls(kind: str) -> None:
    trusted = payload()
    value = trusted
    if kind == "deserialized":
        value = SanitizedPayload.model_validate_json(trusted.model_dump_json())
    elif kind == "malformed-copy":
        value = trusted.model_copy(update={"provenance": {}})
    elif kind == "constructed":
        value = SanitizedPayload.model_construct()
    with respx.mock(assert_all_called=False) as router:
        result = asyncio.run(cloud_generate(value, key="" if kind == "missing-key" else KEY))
        assert not router.calls
    assert isinstance(result, ProviderFailure)
    assert result.error_code == ErrorCode.POLICY_BLOCKED


def test_cloud_health_uses_only_configured_same_origin_metadata() -> None:
    async def check() -> None:
        provider = CloudProvider(
            destination(health_endpoint="https://synthetic.invalid/v1/models"),
            environment={"CLOUD_API_KEY": KEY},
        )
        try:
            assert (await provider.health("synthetic-cloud")).available
            assert KEY not in repr(provider)
        finally:
            await provider.aclose()

    with respx.mock as router:
        router.get("https://synthetic.invalid/v1/models").respond(200, json={"data": []})
        asyncio.run(check())


def test_ollama_success_verifies_identity_and_converts_actual_runtime_metrics() -> None:
    with respx.mock as router:
        tags = router.get(OLLAMA_URL + "/api/tags").respond(200, json=listed("sha256:" + DIGEST))
        generate = router.post(OLLAMA_URL + "/api/generate").respond(200, json=generated())
        result = asyncio.run(local_generate())
    assert tags.call_count == generate.call_count == 1
    assert isinstance(result, ProviderSuccess)
    assert result.model_version == DIGEST
    assert result.runtime_metrics is not None
    assert result.runtime_metrics.total_ms == 9
    assert result.runtime_metrics.load_ms == 1
    assert result.runtime_metrics.prompt_ms == 2
    assert result.runtime_metrics.generation_ms == 6
    assert result.token_metrics is not None
    assert result.token_metrics.output_tokens == 3
    assert json.loads(generate.calls.last.request.content)["stream"] is False


@pytest.mark.parametrize("metadata", [listed("b" * 64), {"models": []}])
def test_ollama_missing_or_changed_identity_never_generates(metadata: dict[str, Any]) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(OLLAMA_URL + "/api/tags").respond(200, json=metadata)
        generate = router.post(OLLAMA_URL + "/api/generate").respond(200, json=generated())
        result = asyncio.run(local_generate())
    assert isinstance(result, ProviderFailure)
    assert result.error_code == ErrorCode.UNAVAILABLE
    assert not generate.called


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, ErrorCode.POLICY_BLOCKED), (429, ErrorCode.UNAVAILABLE), (500, ErrorCode.UNAVAILABLE)],
)
def test_ollama_upstream_errors_do_not_retry(status: int, expected: ErrorCode) -> None:
    with respx.mock as router:
        router.get(OLLAMA_URL + "/api/tags").respond(200, json=listed())
        route = router.post(OLLAMA_URL + "/api/generate").respond(status, text="invented-private")
        result = asyncio.run(local_generate())
    assert isinstance(result, ProviderFailure)
    assert result.error_code == expected
    assert route.call_count == 1
    assert "invented-private" not in repr(result)


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
def test_ollama_connection_failures_are_typed_without_hidden_retry(
    exception: type[httpx.RequestError],
) -> None:
    with respx.mock as router:
        route = router.get(OLLAMA_URL + "/api/tags").mock(side_effect=exception("invented-private"))
        result = asyncio.run(local_generate())
    assert isinstance(result, ProviderFailure)
    assert result.error_code == (
        ErrorCode.TIMEOUT if exception is httpx.ReadTimeout else ErrorCode.UNAVAILABLE
    )
    assert route.call_count == 1


@pytest.mark.parametrize(
    "body", [generated(done=False), generated(model="wrong:fixture"), generated(response=""), {}]
)
def test_ollama_malformed_generations_are_rejected(body: dict[str, Any]) -> None:
    with respx.mock as router:
        router.get(OLLAMA_URL + "/api/tags").respond(200, json=listed())
        router.post(OLLAMA_URL + "/api/generate").respond(200, json=body)
        result = asyncio.run(local_generate())
    assert isinstance(result, ProviderFailure)
    assert result.error_code == ErrorCode.VALIDATION_FAILED


def test_ollama_read_only_control_health_and_unknown_residency() -> None:
    async def check() -> None:
        provider = ollama()
        try:
            assert (await provider.health("synthetic-local")).available
            control = OllamaRuntimeControl(provider)
            models = await control.list_models()
            assert len(models) == 1
            assert models[0].resident is None
            for result in (
                await control.pull("synthetic-local", DIGEST),
                await control.unload("synthetic-local"),
                await control.remove("synthetic-local"),
            ):
                assert result.error_code == ErrorCode.POLICY_BLOCKED
        finally:
            await provider.aclose()

    with respx.mock as router:
        tags = router.get(OLLAMA_URL + "/api/tags").respond(200, json=listed())
        asyncio.run(check())
        assert len(router.calls) == tags.call_count == 2


@pytest.mark.parametrize("kind", ["deserialized", "malformed-copy", "constructed"])
def test_ollama_untrusted_payloads_do_not_even_read_metadata(kind: str) -> None:
    trusted = payload()
    if kind == "deserialized":
        value = SanitizedPayload.model_validate_json(trusted.model_dump_json())
    elif kind == "malformed-copy":
        value = trusted.model_copy(update={"provenance": {}})
    else:
        value = SanitizedPayload.model_construct()
    with respx.mock(assert_all_called=False) as router:
        result = asyncio.run(local_generate(value))
        assert not router.calls
    assert isinstance(result, ProviderFailure)
    assert result.error_code == ErrorCode.POLICY_BLOCKED


def test_mock_availability_failure_simulated_latency_and_content_free_counters() -> None:
    async def check() -> None:
        provider = MockProvider(
            {
                "ready": MockModel(response="invented answer", latency_ms=3),
                "absent": MockModel(response="fixture", available=False),
                "failed": MockModel(response="fixture", failure=ErrorCode.UNAVAILABLE),
                "slow": MockModel(response="fixture", latency_ms=3000),
            }
        )
        assert (await provider.health("ready")).available
        assert not (await provider.health("absent")).available
        for _ in range(2):
            result = await provider.generate(payload(), "ready", GenerationOptions(), 1)
            assert isinstance(result, ProviderSuccess)
            assert result.text == "invented answer"
            assert result.latency_ms == 3
        absent = await provider.generate(payload(), "absent", GenerationOptions(), 1)
        failed = await provider.generate(payload(), "failed", GenerationOptions(), 1)
        slow = await provider.generate(payload(), "slow", GenerationOptions(), 1)
        assert isinstance(absent, ProviderFailure) and absent.error_code == ErrorCode.UNAVAILABLE
        assert isinstance(failed, ProviderFailure) and failed.error_code == ErrorCode.UNAVAILABLE
        assert isinstance(slow, ProviderFailure) and slow.error_code == ErrorCode.TIMEOUT
        assert slow.latency_ms == 1000
        assert dict(provider.test_call_counts) == {"ready": 2, "failed": 1, "slow": 1}
        assert "invented-private-prompt-marker" not in repr(provider.test_call_counts)
        untrusted = payload().model_copy(update={"provenance": {}})
        blocked = await provider.generate(untrusted, "ready", GenerationOptions(), 1)
        assert (
            isinstance(blocked, ProviderFailure) and blocked.error_code == ErrorCode.POLICY_BLOCKED
        )
        assert provider.test_call_counts["ready"] == 2

    asyncio.run(check())


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://external.invalid:11434",
        "http://127.0.0.1:11434/extra",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434?key=private",
        "http://127.0.0.1:0",
    ],
)
def test_local_destination_restrictions(endpoint: str) -> None:
    with pytest.raises(ValueError):
        OllamaProvider(
            {"local": OllamaModel(runtime_tag="synthetic:fixture", pinned_digest=DIGEST)},
            endpoint=endpoint,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint": "http://synthetic.invalid/v1/chat/completions"},
        {"endpoint": "https://synthetic.invalid/"},
        {"endpoint": "https://user:pass@synthetic.invalid/v1/chat/completions"},
        {"endpoint": "https://synthetic.invalid/v1/chat/completions?key=private"},
        {"endpoint": "https://bad host.invalid/v1/chat/completions"},
        {"health_endpoint": "https://other.invalid/health"},
    ],
)
def test_cloud_destination_restrictions(changes: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        destination(**changes)


def test_transport_ignores_ssl_environment_and_preserves_disabled_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/invented-private-certificate")
    client = create_client()
    assert client.follow_redirects is False
    asyncio.run(client.aclose())
