"""Policy and one-use approval matrix; no endpoint or provider is invoked."""

import dataclasses
import json
import pickle
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import pytest

from backend.api.schemas import CloudFallbackReason, GenerationOptions
from backend.broker.policy_gate import (
    ApprovalNeeded,
    ApprovalReceipt,
    PolicyCheck,
    PolicyGate,
    PolicyViolation,
)
from backend.config.loader import PolicyConfig
from backend.contracts import ErrorCode, ExecutionMode, TaskCategory
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.sanitized_payload import SanitizedPayload, sanitized_digest
from backend.privacy.sanitizer import Sanitizer
from backend.providers.base import ProviderDescriptor

pytestmark = pytest.mark.privacy
REASON = CloudFallbackReason.LOCAL_ERROR


class Clock:
    value = 100.0

    def __call__(self) -> float:
        return self.value


class FixtureAnalyzer:
    version = "synthetic-policy-fixture-v1"

    def __init__(self, hard_block: bool = False) -> None:
        self.hard_block = hard_block

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        return (DetectionSpan("CREDIT_CARD", 0, len(text)),) if self.hard_block else ()


def check(
    mode: ExecutionMode = ExecutionMode.ASK_BEFORE_CLOUD,
    *,
    hard_block: bool = False,
    task: TaskCategory | None = None,
) -> PolicyCheck:
    request_id = uuid4()
    payload = (
        Sanitizer(FixtureAnalyzer(hard_block))
        .sanitize(request_id, "invented-private-policy-fixture")
        .payload
    )
    return PolicyCheck(request_id, payload, mode, GenerationOptions(), task, 60.0)


def target(route: str = "cloud", key: bool = True, **changes: Any) -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "model_id": "synthetic",
            "model_version": "fixture-v1",
            "route": route,
            "endpoint": "https://synthetic.invalid/v1/chat/completions"
            if route == "cloud"
            else "http://127.0.0.1:11434",
            "key_available": key,
            **changes,
        }
    )


@pytest.mark.parametrize("mode", list(ExecutionMode))
@pytest.mark.parametrize("route", ["local", "cloud"])
@pytest.mark.parametrize("key", [False, True])
@pytest.mark.parametrize("hard_block", [False, True])
@pytest.mark.parametrize("reason", [None, *list(CloudFallbackReason)])
def test_complete_default_policy_matrix(
    mode: ExecutionMode,
    route: str,
    key: bool,
    hard_block: bool,
    reason: CloudFallbackReason | None,
) -> None:
    gate = PolicyGate()
    request = check(mode, hard_block=hard_block)
    descriptor = target(route, key)
    # The reason must pair exactly with the mode. Cloud-only is authorized by the
    # mode itself because no local attempt was made, so the fallback reasons are
    # refused there, and its reason is refused everywhere else.
    permitted = (
        {CloudFallbackReason.CLOUD_ONLY}
        if mode == ExecutionMode.CLOUD_ONLY
        else {CloudFallbackReason.LOCAL_ERROR, CloudFallbackReason.VALIDATION_FAILURE}
    )
    eligible = route == "local" or (
        mode != ExecutionMode.LOCAL_ONLY and key and not hard_block and reason in permitted
    )
    if eligible and route == "cloud" and mode == ExecutionMode.ASK_BEFORE_CLOUD:
        with pytest.raises(ApprovalNeeded):
            gate.authorize(request, descriptor, reason)
    elif eligible:
        gate.authorize(request, descriptor, reason)
    else:
        with pytest.raises(PolicyViolation) as caught:
            gate.authorize(request, descriptor, reason)
        assert not isinstance(caught.value, ApprovalNeeded)
    assert gate.pending_count == 0


@pytest.mark.parametrize(
    "reason", [CloudFallbackReason.NO_LOCAL_MODEL, CloudFallbackReason.CLOUD_PREFERRED]
)
def test_rejected_reason_does_not_propose_cloud(reason: CloudFallbackReason) -> None:
    gate = PolicyGate()
    with pytest.raises(PolicyViolation):
        gate.propose(check(), target(), reason)
    assert gate.pending_count == 0


def test_explicit_no_model_and_preferred_category_policy() -> None:
    policy = PolicyConfig(
        allowed_fallback_reasons=(
            CloudFallbackReason.NO_LOCAL_MODEL,
            CloudFallbackReason.CLOUD_PREFERRED,
        ),
        allow_no_local_model_cloud=True,
        cloud_preferred_categories=(TaskCategory.CODE,),
    )
    gate = PolicyGate(policy)
    request = check(ExecutionMode.AUTO_CLOUD)
    gate.authorize(request, target(), CloudFallbackReason.NO_LOCAL_MODEL)
    with pytest.raises(PolicyViolation):
        gate.authorize(request, target(), CloudFallbackReason.CLOUD_PREFERRED)
    gate.authorize(
        dataclasses.replace(request, task=TaskCategory.CODE),
        target(),
        CloudFallbackReason.CLOUD_PREFERRED,
    )


def test_proposal_is_exact_idempotent_and_does_not_refresh_expiry() -> None:
    clock = Clock()
    gate = PolicyGate(clock=clock)
    request = check()
    descriptor = target()
    proposal = gate.propose(request, descriptor, REASON)
    assert request.payload is not None
    assert proposal.sanitized_preview == request.payload.text
    assert proposal.digest == request.payload.digest
    assert proposal.target_fingerprint == descriptor.fingerprint
    assert proposal.policy_version == gate.policy_version
    assert proposal.expires_at == 220
    clock.value += 30
    assert gate.propose(request, descriptor, REASON) == proposal
    assert gate.pending_count == 1
    receipt = gate.approve(proposal)
    gate.authorize(request, descriptor, REASON, receipt)
    assert gate.pending_count == 0
    with pytest.raises(PolicyViolation) as caught:
        gate.authorize(request, descriptor, REASON, receipt)
    assert caught.value.code == ErrorCode.APPROVAL_CONFLICT


@pytest.mark.parametrize(
    "change",
    [
        {"proposal_id": uuid4()},
        {"request_id": uuid4()},
        {"digest": "b" * 64},
        {"target_fingerprint": "b" * 64},
        {"model_id": "different"},
        {"model_version": "different"},
        {"endpoint": "https://different.invalid/v1"},
        {"mode": ExecutionMode.AUTO_CLOUD},
        {"policy_version": "b" * 64},
        {"options_digest": "b" * 64},
        {"expires_at": 999.0},
        {"sanitized_preview": "invented-tamper-marker"},
        {"reason": CloudFallbackReason.VALIDATION_FAILURE},
    ],
)
def test_any_changed_proposal_echo_is_rejected(change: dict[str, Any]) -> None:
    gate = PolicyGate(clock=Clock())
    request = check()
    proposal = gate.propose(request, target(), REASON)
    with pytest.raises(PolicyViolation) as caught:
        gate.approve(proposal.model_copy(update=change))
    assert caught.value.code == ErrorCode.APPROVAL_CONFLICT
    # A failed tamper never approves or destroys the original proposal.
    receipt = gate.approve(proposal)
    gate.authorize(request, target(), REASON, receipt)


@pytest.mark.parametrize("kind", ["request", "payload", "mode", "options", "target", "reason"])
def test_approval_cannot_authorize_a_changed_dispatch(kind: str) -> None:
    gate = PolicyGate()
    request = check()
    descriptor = target()
    proposal = gate.propose(request, descriptor, REASON)
    receipt = gate.approve(proposal)
    reason = REASON
    if kind == "request":
        request = check()
    elif kind == "payload":
        assert request.payload is not None
        other = Sanitizer(FixtureAnalyzer()).sanitize(
            request.request_id, "different synthetic text"
        )
        request = dataclasses.replace(request, payload=other.payload)
    elif kind == "mode":
        request = dataclasses.replace(request, mode=ExecutionMode.AUTO_CLOUD)
    elif kind == "options":
        request = dataclasses.replace(request, options=GenerationOptions(max_output_tokens=1))
    elif kind == "target":
        descriptor = target(endpoint="https://different.invalid/v1/chat/completions")
    else:
        reason = CloudFallbackReason.VALIDATION_FAILURE
    with pytest.raises(PolicyViolation):
        gate.authorize(request, descriptor, reason, receipt)


def test_concurrent_receipt_consumption_is_single_use() -> None:
    gate = PolicyGate()
    request = check()
    descriptor = target()
    receipt = gate.approve(gate.propose(request, descriptor, REASON))

    def consume(index: int) -> bool:
        try:
            gate.authorize(request, descriptor, REASON, receipt)
            return True
        except PolicyViolation:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(consume, range(16))) == 1
    assert gate.pending_count == 0


@pytest.mark.parametrize("approved", [False, True])
def test_expiry_cleans_records_and_denies_approval_or_dispatch(approved: bool) -> None:
    clock = Clock()
    gate = PolicyGate(clock=clock)
    request = check()
    proposal = gate.propose(request, target(), REASON)
    receipt = gate.approve(proposal) if approved else None
    clock.value = proposal.expires_at
    assert gate.pending_count == 0
    with pytest.raises(PolicyViolation):
        if receipt is None:
            gate.approve(proposal)
        else:
            gate.authorize(request, target(), REASON, receipt)


def test_changed_policy_and_explicit_cancellation_invalidate_receipts() -> None:
    gate = PolicyGate()
    request = check()
    proposal = gate.propose(request, target(), REASON)
    receipt = gate.approve(proposal)
    gate.set_policy(PolicyConfig(hard_block_entity_types=()))
    assert gate.pending_count == 0
    with pytest.raises(PolicyViolation):
        gate.authorize(request, target(), REASON, receipt)
    proposal = gate.propose(request, target(), REASON)
    gate.cancel(request.request_id)
    with pytest.raises(PolicyViolation):
        gate.approve(proposal)


def test_store_bounds_purge_before_refusing_new_requests() -> None:
    clock = Clock()
    gate = PolicyGate(max_pending=1, clock=clock)
    gate.propose(check(), target(), REASON)
    with pytest.raises(PolicyViolation):
        gate.propose(check(), target(), REASON)
    assert gate.pending_count == 1
    clock.value += 121
    gate.propose(check(), target(), REASON)
    assert gate.pending_count == 1


@pytest.mark.parametrize("remaining", [0.0, -1.0, float("nan"), float("inf"), 121.0, True])
def test_invalid_or_expired_compute_budget_is_rejected(remaining: float) -> None:
    gate = PolicyGate()
    request = dataclasses.replace(check(), remaining_seconds=remaining)
    with pytest.raises(PolicyViolation):
        gate.authorize(request, target(), REASON)
    assert gate.pending_count == 0


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "deserialized",
        "request_mismatch",
        "text_tamper",
        "counts_tamper",
        "counts_and_digest_tamper",
        "status_tamper",
    ],
)
def test_failed_or_forged_privacy_state_never_proposes(kind: str) -> None:
    gate = PolicyGate()
    request = check(hard_block=True)
    assert request.payload is not None
    original_payload = request.payload
    if kind == "missing":
        request = dataclasses.replace(request, payload=None)
    elif kind == "deserialized":
        request = dataclasses.replace(
            request,
            payload=SanitizedPayload.model_validate_json(original_payload.model_dump_json()),
        )
    elif kind == "request_mismatch":
        request = dataclasses.replace(request, request_id=uuid4())
    elif kind == "text_tamper":
        request = dataclasses.replace(
            request, payload=original_payload.model_copy(update={"text": "invented"})
        )
    elif kind == "counts_tamper":
        request = dataclasses.replace(
            request, payload=original_payload.model_copy(update={"privacy_counts": ()})
        )
    elif kind == "counts_and_digest_tamper":
        digest = sanitized_digest(
            original_payload.request_id, original_payload.text, original_payload.provenance, ()
        )
        forged = original_payload.model_copy(update={"privacy_counts": (), "digest": digest})
        assert not forged.is_trusted
        request = dataclasses.replace(request, payload=forged)
    else:
        provenance = original_payload.provenance.model_copy(update={"status": "failed"})
        request = dataclasses.replace(
            request, payload=original_payload.model_copy(update={"provenance": provenance})
        )
    with pytest.raises(PolicyViolation):
        gate.propose(request, target(), REASON)
    assert gate.pending_count == 0


@pytest.mark.parametrize("mutation", ["missing", "bytes", "wrong", "wrong_id"])
def test_malformed_or_forged_receipts_fail_safely(mutation: str) -> None:
    gate = PolicyGate()
    request = check()
    receipt = gate.approve(gate.propose(request, target(), REASON))
    malformed: Any = receipt
    if mutation == "missing":
        malformed = ApprovalReceipt.__new__(ApprovalReceipt)
    elif mutation == "bytes":
        malformed._token = b"bad"
    elif mutation == "wrong":
        malformed._token = "not-the-approved-receipt"
    else:
        malformed._proposal_id = []
    with pytest.raises(PolicyViolation) as caught:
        gate.authorize(request, target(), REASON, malformed)
    assert caught.value.code == ErrorCode.APPROVAL_CONFLICT


def test_safe_representations_and_internal_receipt_serialization() -> None:
    gate = PolicyGate()
    request = check()
    proposal = gate.propose(request, target(), REASON)
    receipt = gate.approve(proposal)
    marker = "invented-private-policy-fixture"
    for value in (gate, request, proposal, receipt):
        assert marker not in repr(value)
    assert marker == proposal.sanitized_preview  # Explicit display value only.
    for serialize in (
        lambda: pickle.dumps(receipt),
        lambda: json.dumps(receipt),
        receipt.model_dump,
        receipt.model_dump_json,
    ):
        with pytest.raises(TypeError) as caught:
            serialize()
        assert marker not in str(caught.value)


def test_different_pending_binding_and_repeated_approval_are_conflicts() -> None:
    gate = PolicyGate()
    request = check()
    proposal = gate.propose(request, target(), REASON)
    with pytest.raises(PolicyViolation):
        gate.propose(request, target(model_id="different"), REASON)
    gate.approve(proposal)
    with pytest.raises(PolicyViolation):
        gate.approve(proposal)


@pytest.mark.parametrize(
    "values",
    [{"approval_seconds": 0}, {"approval_seconds": 121}, {"max_pending": 0}, {"max_pending": True}],
)
def test_approval_store_configuration_rejects_unbounded_values(values: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        PolicyGate(**values)


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf"), True])
def test_invalid_clock_is_safe_failure(value: float) -> None:
    gate = PolicyGate(clock=lambda: value)
    with pytest.raises(PolicyViolation) as caught:
        gate.now()
    assert caught.value.code == ErrorCode.INTERNAL


def test_clock_failure_or_regression_does_not_leak_or_extend_approvals() -> None:
    clock = Clock()
    gate = PolicyGate(clock=clock)
    assert gate.now() == 100.0
    clock.value = 99.0
    with pytest.raises(PolicyViolation):
        gate.now()

    def broken() -> float:
        raise RuntimeError("invented-private-clock-marker")

    with pytest.raises(PolicyViolation) as caught:
        PolicyGate(clock=broken).now()
    assert "invented-private" not in str(caught.value)
