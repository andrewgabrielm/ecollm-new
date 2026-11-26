"""Privacy issuance, request lifetime and honest pricing integration checks."""

import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.api.schemas import GenerationOptions
from backend.broker.context import RequestContext
from backend.contracts import ErrorCode
from backend.privacy.analyzer import DetectionSpan, PrivacyError
from backend.privacy.recognisers import synthetic_spans
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer
from backend.providers.base import ProviderFailure, TokenMetrics
from backend.providers.mock import MockModel, MockProvider
from backend.providers.pricing import PricingCatalog, load_pricing


class SyntheticAnalyzer:
    version = "synthetic-test-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        return synthetic_spans(text)


@pytest.mark.privacy
def test_payload_trust_is_request_bound_and_cannot_survive_serialization() -> None:
    result = Sanitizer(SyntheticAnalyzer()).sanitize(uuid4(), "Synthetic STU-1234-5678")
    assert result.payload.is_trusted
    decoded = SanitizedPayload.model_validate_json(result.payload.model_dump_json())
    assert not decoded.is_trusted
    changes: tuple[dict[str, object], ...] = (
        {"text": "tampered"},
        {"request_id": uuid4()},
        {"digest": "a" * 64},
        {"provenance": {}},
    )
    for updates in changes:
        assert not result.payload.model_copy(update=updates).is_trusted


@pytest.mark.privacy
def test_context_closes_retained_map_and_rejects_cross_request_binding() -> None:
    result = Sanitizer(SyntheticAnalyzer()).sanitize(uuid4(), "Synthetic STU-1234-5678")
    with pytest.raises(ValueError, match="map IDs differ"):
        RequestContext(uuid4(), "synthetic", privacy_placeholders=result.placeholders)
    context = RequestContext(
        result.payload.request_id,
        "Synthetic STU-1234-5678",
        sanitized_payload=result.payload,
        privacy_placeholders=result.placeholders,
    )
    assert context.privacy_placeholders is result.placeholders
    context.clear()
    assert context.privacy_placeholders is None
    assert not result.placeholders.tokens
    with pytest.raises(PrivacyError):
        result.placeholders.restore(result.payload.request_id, result.payload.text)


@pytest.mark.privacy
def test_failed_sanitization_and_untrusted_values_cannot_invoke_mock() -> None:
    class BrokenAnalyzer(SyntheticAnalyzer):
        def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
            raise ValueError("invented-private-marker")

    provider = MockProvider({"synthetic": MockModel(response="fixture")})
    with pytest.raises(PrivacyError) as caught:
        Sanitizer(BrokenAnalyzer()).sanitize(uuid4(), "invented-private-marker")
    assert "invented-private-marker" not in str(caught.value)
    assert not provider.test_call_counts
    result = Sanitizer(SyntheticAnalyzer()).sanitize(uuid4(), "safe synthetic input")
    untrusted = SanitizedPayload.model_validate_json(result.payload.model_dump_json())
    response = asyncio.run(provider.generate(untrusted, "synthetic", GenerationOptions(), 1))
    assert isinstance(response, ProviderFailure)
    assert response.error_code == ErrorCode.POLICY_BLOCKED
    assert not provider.test_call_counts


def test_dated_pricing_unknowns_and_exact_token_costs() -> None:
    assert load_pricing().as_of == date(2026, 11, 17)
    assert (
        load_pricing().estimate("missing", "v1", TokenMetrics(input_tokens=1, output_tokens=1))
        is None
    )
    config = PricingCatalog.model_validate(
        {
            "as_of": "2026-11-17",
            "rates": [
                {
                    "model_id": "synthetic",
                    "model_version": "fixture-v1",
                    "input_usd_per_million": "0.5",
                    "output_usd_per_million": "1.5",
                    "source": {"uri": "https://example.invalid/synthetic-pricing"},
                }
            ],
        }
    )
    assert config.estimate(
        "synthetic", "fixture-v1", TokenMetrics(input_tokens=100, output_tokens=200)
    ) == Decimal("0.00035")
    assert (
        config.estimate(
            "synthetic", "fixture-v2", TokenMetrics(input_tokens=100, output_tokens=200)
        )
        is None
    )
    assert config.estimate("synthetic", "fixture-v1", TokenMetrics(input_tokens=100)) is None
    assert config.estimate("synthetic", "fixture-v1", None) is None
    with pytest.raises(ValidationError):
        PricingCatalog.model_validate({**config.model_dump(), "rates": config.rates * 2})
