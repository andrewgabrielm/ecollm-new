"""Step 02 boundary contracts, without scanner/provider implementation or calls."""

import dataclasses
import json
import pickle
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.api.schemas import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    ApprovalRequiredResponse,
    CacheProvenance,
    CacheStatus,
    ChatRequest,
    ChatResponse,
    CloudFallbackReason,
    GenerationOptions,
    ResponseTimings,
)
from backend.broker.context import RequestContext, RoutingPlan, RoutingReason
from backend.contracts import ErrorCode, ExecutionMode, SensitivityScope
from backend.privacy.sanitized_payload import (
    SanitizationProvenance,
    SanitizedPayload,
    sanitized_digest,
)
from backend.providers.base import (
    Provider,
    ProviderFailure,
    ProviderResult,
    ProviderSuccess,
    RuntimeControl,
    RuntimeOperationResult,
)


def payload_fixture(text: str = "Synthetic masked [PERSON_1]") -> SanitizedPayload:
    request_id = uuid4()
    provenance = SanitizationProvenance(
        sanitizer_version="synthetic-contract-v1", analyzer_version="synthetic-contract-v1"
    )
    return SanitizedPayload(
        request_id=request_id,
        text=text,
        digest=sanitized_digest(request_id, text, provenance),
        provenance=provenance,
    )


def test_chat_request_safe_defaults_and_whitespace_preservation() -> None:
    request = ChatRequest(prompt="  A synthetic question?  ")
    assert request.mode == ExecutionMode.LOCAL_ONLY
    assert request.sensitivity_scope == SensitivityScope.UNSPECIFIED
    assert request.task_hint is None
    assert request.prompt == "  A synthetic question?  "
    assert request.options.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    assert len(ChatRequest(prompt="x" * 8000).prompt) == 8000


@pytest.mark.parametrize(
    "data",
    [
        {"prompt": ""},
        {"prompt": " \n\t"},
        {"prompt": "x" * 8001},
        {"prompt": 42},
        {"prompt": "synthetic", "mode": "unknown"},
        {"prompt": "synthetic", "task_hint": "medical"},
        {"prompt": "synthetic", "sensitivity_scope": "public"},
        {"prompt": "synthetic", "unknown": True},
        {"prompt": "synthetic", "options": {"temperature": -0.1}},
        {"prompt": "synthetic", "options": {"temperature": True}},
        {"prompt": "synthetic", "options": {"temperature": float("nan")}},
        {"prompt": "synthetic", "options": {"max_output_tokens": True}},
        {"prompt": "synthetic", "options": {"max_output_tokens": 4097}},
        {"prompt": "synthetic", "options": {"unknown": True}},
    ],
)
def test_malformed_requests_rejected(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(data)


def test_request_response_and_validation_reprs_do_not_echo_content() -> None:
    marker = "invented-private-content"
    request_id = uuid4()
    request = ChatRequest(prompt=marker)
    response = ChatResponse(
        request_id=request_id,
        answer=marker,
        route="local",
        model_id="synthetic-a",
        model_version="v1",
        reason="rules_ranked",
        timings=ResponseTimings(total_ms=1),
    )
    approval = ApprovalRequiredResponse(
        request_id=request_id,
        sanitized_preview=marker,
        best_local_answer=marker,
        expires_at=datetime.now(UTC),
        reason=CloudFallbackReason.LOCAL_ERROR,
    )
    assert marker not in repr(request)
    assert marker not in repr(response)
    assert marker not in repr(approval)
    # Content remains available to explicit transport serialization.
    assert response.model_dump()["answer"] == marker
    with pytest.raises(ValidationError) as caught:
        ChatRequest(prompt=marker * 1000)
    assert marker not in str(caught.value)


def test_request_context_redaction_and_serialization_forbidden() -> None:
    marker = "invented-private-context"
    context = RequestContext(uuid4(), marker, {"[PERSON_1]": marker})
    assert marker not in repr(context)
    assert marker not in str(context)
    assert context.raw_prompt == marker
    assert context.placeholder_map["[PERSON_1]"] == marker
    for serialize in (
        lambda: json.dumps(context),
        lambda: pickle.dumps(context),
        lambda: dataclasses.asdict(cast(Any, context)),
        context.model_dump,
        context.model_dump_json,
    ):
        with pytest.raises(TypeError) as caught:
            serialize()
        assert marker not in str(caught.value)
    context.clear()
    assert not context.raw_prompt
    assert not context.placeholder_map
    assert context.sanitized_payload is None


def test_context_rejects_payload_from_another_request() -> None:
    with pytest.raises(ValueError, match="IDs differ"):
        RequestContext(uuid4(), "synthetic", sanitized_payload=payload_fixture())


def test_payload_is_immutable_and_digest_binds_all_inputs() -> None:
    payload = payload_fixture()
    assert payload.text not in repr(payload)
    assert sanitized_digest(payload.request_id, payload.text, payload.provenance) == payload.digest
    modified: dict[str, Any] = payload.model_dump()
    modified["text"] = "different synthetic text"
    with pytest.raises(ValidationError, match="digest mismatch"):
        SanitizedPayload.model_validate(modified)
    modified = payload.model_dump()
    modified["request_id"] = uuid4()
    with pytest.raises(ValidationError, match="digest mismatch"):
        SanitizedPayload.model_validate(modified)
    modified = payload.model_dump()
    modified["provenance"]["analyzer_version"] = "another-version"
    with pytest.raises(ValidationError, match="digest mismatch"):
        SanitizedPayload.model_validate(modified)
    with pytest.raises(ValidationError, match="frozen"):
        payload.text = "different"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SanitizationProvenance.model_validate(
            {"sanitizer_version": "v1", "analyzer_version": "v1", "trusted": True}
        )


def test_routing_plan_is_immutable_and_ordered() -> None:
    plan = RoutingPlan(
        model_ids=("synthetic-b", "synthetic-a"),
        manifest_versions=("b-v1", "a-v2"),
        reason=RoutingReason.RULES_RANKED,
    )
    assert plan.model_ids == ("synthetic-b", "synthetic-a")
    assert plan.manifest_versions == ("b-v1", "a-v2")
    with pytest.raises(ValidationError, match="frozen"):
        plan.model_ids = ()
    for ids, versions in [(("a",), ()), (("a", "a"), ("v1", "v1")), ((), ())]:
        with pytest.raises(ValidationError):
            RoutingPlan(
                model_ids=ids, manifest_versions=versions, reason=RoutingReason.RULES_RANKED
            )
    assert (
        RoutingPlan(
            model_ids=(), manifest_versions=(), reason=RoutingReason.NO_ELIGIBLE_MODEL
        ).model_ids
        == ()
    )


def test_provider_results_are_discriminated_and_redacted() -> None:
    marker = "invented-private-provider-text"
    adapter: TypeAdapter[ProviderSuccess | ProviderFailure] = TypeAdapter(ProviderResult)
    success = adapter.validate_python(
        {
            "status": "success",
            "model_id": "synthetic-a",
            "model_version": "v1",
            "text": marker,
            "latency_ms": 1,
        }
    )
    assert isinstance(success, ProviderSuccess)
    assert marker not in repr(success)
    failure = adapter.validate_python(
        {
            "status": "error",
            "model_id": "synthetic-a",
            "model_version": "v1",
            "error_code": "timeout",
            "latency_ms": 1,
        }
    )
    assert isinstance(failure, ProviderFailure)
    assert failure.error_code == ErrorCode.TIMEOUT
    with pytest.raises(ValidationError):
        adapter.validate_python({**failure.model_dump(), "text": marker})
    with pytest.raises(ValidationError):
        adapter.validate_python({**success.model_dump(), "error_code": "timeout"})
    with pytest.raises(ValidationError):
        adapter.validate_python({**success.model_dump(), "latency_ms": -1})
    with pytest.raises(ValidationError):
        adapter.validate_python({**success.model_dump(), "status": "unknown"})


def test_runtime_control_is_separate_and_operation_results_are_consistent() -> None:
    assert hasattr(Provider, "generate")
    assert hasattr(Provider, "health")
    assert hasattr(Provider, "warm_up")
    assert not hasattr(Provider, "pull")
    assert not hasattr(RuntimeControl, "generate")
    assert hasattr(RuntimeControl, "pull")
    assert hasattr(RuntimeControl, "unload")
    assert hasattr(RuntimeControl, "remove")
    with pytest.raises(ValidationError):
        RuntimeOperationResult(status="error", model_id="synthetic-a")
    with pytest.raises(ValidationError):
        RuntimeOperationResult(
            status="success", model_id="synthetic-a", error_code=ErrorCode.TIMEOUT
        )


def test_non_finite_generation_options_rejected() -> None:
    with pytest.raises(ValidationError):
        GenerationOptions(temperature=float("inf"))


def test_cache_response_requires_local_matching_producing_model_provenance() -> None:
    values: dict[str, Any] = {
        "request_id": uuid4(),
        "answer": "synthetic answer",
        "route": "local",
        "model_id": "synthetic-b",
        "model_version": "b-v1",
        "reason": "rules_ranked",
        "cache_status": CacheStatus.EXACT_HIT,
        "timings": ResponseTimings(total_ms=1),
    }
    with pytest.raises(ValidationError, match="local answer provenance"):
        ChatResponse.model_validate(values)
    provenance = CacheProvenance(
        model_id="synthetic-b",
        model_version="b-v1",
        policy_version="policy-v1",
        schema_version="schema-v1",
    )
    values["cache_provenance"] = provenance
    assert ChatResponse.model_validate(values).cache_provenance == provenance
    with pytest.raises(ValidationError, match="producing model/version"):
        ChatResponse.model_validate({**values, "model_id": "synthetic-a"})
    with pytest.raises(ValidationError, match="producing model/version"):
        ChatResponse.model_validate({**values, "model_version": "b-v2"})
    with pytest.raises(ValidationError, match="local answer provenance"):
        ChatResponse.model_validate({**values, "route": "cloud"})
    with pytest.raises(ValidationError, match="only valid for a cache hit"):
        ChatResponse.model_validate({**values, "cache_status": CacheStatus.MISS})
