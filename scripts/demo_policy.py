"""Offline Step 05 demonstration using invented data and deterministic mocks only."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from backend.api.schemas import CloudFallbackReason
from backend.broker.context import RequestContext
from backend.broker.orchestrator import GuardedDispatcher
from backend.broker.policy_gate import CloudProposal
from backend.contracts import ErrorCode, ExecutionMode
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.recognisers import synthetic_spans
from backend.privacy.sanitizer import Sanitizer
from backend.providers.base import ProviderFailure, ProviderSuccess
from backend.providers.mock import MockModel, MockProvider


class DemoAnalyzer:
    version = "synthetic-policy-demo-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        spans = list(synthetic_spans(text))
        if "CARD-0000" in text:
            start = text.index("CARD-0000")
            spans.append(DetectionSpan("CREDIT_CARD", start, start + len("CARD-0000")))
        return tuple(spans)


async def demonstrate() -> dict[str, object]:
    cloud = MockProvider(
        {"mock-cloud": MockModel(response="Synthetic approved response")},
        route="cloud",
        key_available=True,
    )
    dispatcher = GuardedDispatcher({"mock-cloud": cloud})
    sanitizer = Sanitizer(DemoAnalyzer())
    result = sanitizer.sanitize(uuid4(), "Invented question for STU-1234-5678")

    local_only = dispatcher.new_request(result.payload)
    blocked = await dispatcher.dispatch(
        local_only, "mock-cloud", reason=CloudFallbackReason.LOCAL_ERROR
    )
    assert isinstance(blocked, ProviderFailure) and blocked.error_code == ErrorCode.POLICY_BLOCKED
    assert not cloud.test_call_counts
    dispatcher.cancel(local_only)

    context = RequestContext(
        result.payload.request_id,
        "",
        sanitized_payload=result.payload,
        privacy_placeholders=result.placeholders,
    )
    asked = dispatcher.new_request(
        result.payload, mode=ExecutionMode.ASK_BEFORE_CLOUD, context=context
    )
    proposal = await dispatcher.dispatch(
        asked, "mock-cloud", reason=CloudFallbackReason.LOCAL_ERROR
    )
    assert isinstance(proposal, CloudProposal)
    assert not cloud.test_call_counts
    receipt = dispatcher.approve(asked, proposal)
    approved = await dispatcher.dispatch(
        asked, "mock-cloud", reason=CloudFallbackReason.LOCAL_ERROR, approval=receipt
    )
    assert isinstance(approved, ProviderSuccess)
    replay = await dispatcher.dispatch(
        asked, "mock-cloud", reason=CloudFallbackReason.LOCAL_ERROR, approval=receipt
    )
    assert isinstance(replay, ProviderFailure) and replay.error_code == ErrorCode.APPROVAL_CONFLICT
    assert cloud.test_call_counts["mock-cloud"] == 1
    dispatcher.cancel(asked)

    sensitive = sanitizer.sanitize(uuid4(), "Invented card marker CARD-0000")
    hard_block = dispatcher.new_request(sensitive.payload, mode=ExecutionMode.ASK_BEFORE_CLOUD)
    denied = await dispatcher.dispatch(
        hard_block, "mock-cloud", reason=CloudFallbackReason.LOCAL_ERROR
    )
    assert isinstance(denied, ProviderFailure) and denied.error_code == ErrorCode.POLICY_BLOCKED
    assert dispatcher.gate.pending_count == 0
    assert cloud.test_call_counts["mock-cloud"] == 1
    dispatcher.cancel(hard_block)
    sensitive.placeholders.clear()
    return {
        "demonstration": "OFFLINE MOCK ONLY",
        "local_only_cloud_calls": 0,
        "proposal_cloud_calls": 0,
        "approved_cloud_calls": cloud.test_call_counts["mock-cloud"],
        "replay": replay.error_code.value,
        "hard_block": denied.error_code.value,
        "pending_approvals": dispatcher.gate.pending_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    summary = asyncio.run(demonstrate())
    root = Path(__file__).resolve().parents[1]
    directory = root / "evaluation" / "results" / f"policy-demo-{uuid4().hex[:12]}"
    directory.mkdir(parents=True, exist_ok=False)
    artifact = directory / "summary.json"
    artifact.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("OFFLINE MOCK ONLY — no network or real inference")
    for field, value in summary.items():
        if field != "demonstration":
            print(f"{field}: {value}")
    print(f"Artifact: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
