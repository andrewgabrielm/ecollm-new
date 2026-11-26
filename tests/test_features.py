"""Versioned pre-generation feature checks using invented data and no runtime."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.broker.feature_extractor import (
    FEATURE_SCHEMA_HASH,
    FEATURE_SCHEMA_VERSION,
    FeatureError,
    FeatureRow,
    extract_features,
)
from backend.contracts import TaskCategory
from backend.model_management.manifest import ModelManifest, RosterEntry
from backend.model_management.registry import HardwareContext
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.recognisers import synthetic_spans
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer


class FixtureAnalyzer:
    version = "synthetic-feature-test-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        return synthetic_spans(text)


def payload(text: str = "Return JSON for the fixed example?") -> SanitizedPayload:
    return Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), text).payload


def entry(*, provenance: str = "synthetic", scores: bool = True) -> RosterEntry:
    """Invented prior rows only, regardless of provenance used for boundary tests."""
    manifest = ModelManifest.model_validate(
        {
            "id": "synthetic-candidate",
            "source": {"uri": "https://example.invalid/fixture"},
            "runtime_tag": "synthetic:fixture",
            "family": "synthetic-fixture",
            "quantization": "fixture-q4",
            "capability_benchmark": (
                [
                    {
                        "task": "format",
                        "provenance": provenance,
                        "quality": 0.7,
                        "latency_ms": 400,
                        "benchmark_version": "a" * 64,
                    }
                ]
                if scores
                else []
            ),
        }
    )
    return RosterEntry(manifest=manifest, installed=True, healthy=True)


def test_same_extractor_produces_identical_training_and_inference_schema() -> None:
    source = payload("Return JSON:\n- item 1\n- item 2?")
    candidate = entry()
    inference = extract_features(source, TaskCategory.FORMAT, candidate, mock_mode=True)
    training = extract_features(source, TaskCategory.FORMAT, candidate, mock_mode=True)
    assert inference.model_dump(mode="json") == training.model_dump(mode="json")
    assert FeatureRow.model_validate_json(inference.model_dump_json()) == inference
    assert inference.schema_version == FEATURE_SCHEMA_VERSION
    assert inference.schema_hash == FEATURE_SCHEMA_HASH
    assert inference.sanitized_char_count == len(source.text)
    assert inference.bullet_line_count == 2
    assert inference.digit_count == 2
    assert inference.question_count == 1
    assert inference.line_count == 3
    assert inference.has_json_cue
    assert inference.candidate_manifest_version == candidate.manifest.version
    assert inference.candidate_weight_digest is None
    assert inference.capability_quality == 0.7
    assert inference.capability_latency_ms == 400


def test_features_do_not_export_content_or_future_outcomes() -> None:
    marker = "invented-private-content-marker"
    row = extract_features(payload(marker), TaskCategory.QA, entry(), mock_mode=True)
    serialized = row.model_dump_json()
    assert marker not in serialized
    assert marker not in repr(row)
    assert row.capability_quality is None  # The fixture observation covers FORMAT only.
    forbidden = {
        "prompt",
        "text",
        "answer",
        "output",
        "request_id",
        "digest",
        "actual_latency_ms",
        "quality_label",
    }
    assert not forbidden.intersection(FeatureRow.model_fields)
    with pytest.raises(ValidationError):
        FeatureRow.model_validate({**row.model_dump(), "answer": marker})


def test_placeholder_namespaces_do_not_change_structural_features() -> None:
    first = payload("Explain STU-1234-5678 in a fixed example")
    second = payload("Explain STU-1234-5678 in a fixed example")
    assert first.text != second.text
    left = extract_features(first, TaskCategory.QA, entry())
    right = extract_features(second, TaskCategory.QA, entry())
    assert left == right
    assert left.digit_count == 0
    assert "STU-1234-5678" not in left.model_dump_json()


def test_unknown_measurements_and_hardware_remain_unknown() -> None:
    row = extract_features(payload(), TaskCategory.FORMAT, entry(scores=False))
    assert row.capability_quality is None
    assert row.capability_latency_ms is None
    assert row.capability_benchmark_version is None
    assert row.capability_provenance.value == "unknown"
    assert row.hardware_platform is None
    assert row.hardware_ram_bytes is None
    assert row.hardware_vram_bytes is None
    assert row.hardware_cpu_threads is None
    machine = HardwareContext()
    assert (
        extract_features(payload(), TaskCategory.FORMAT, entry(), machine).hardware_version
        == row.hardware_version
    )


def test_synthetic_prior_is_never_used_for_real_mode() -> None:
    row = extract_features(payload(), TaskCategory.FORMAT, entry(), mock_mode=False)
    assert row.capability_quality is None
    assert row.capability_latency_ms is None
    assert row.capability_provenance.value == "unknown"
    assert row.capability_benchmark_version is None


def test_versioned_measured_prior_is_available_without_inventing_values() -> None:
    # A stub of future benchmark metadata tests the contract; no real observations exist.
    row = extract_features(payload(), TaskCategory.FORMAT, entry(provenance="measured"))
    assert row.capability_quality == 0.7
    assert row.capability_latency_ms == 400
    assert row.capability_provenance.value == "measured"


def test_feature_row_is_frozen_and_rejects_unknown_schema() -> None:
    row = extract_features(payload(), TaskCategory.FORMAT, entry())
    with pytest.raises(ValidationError):
        row.question_count = 99
    with pytest.raises(ValidationError):
        FeatureRow.model_validate({**row.model_dump(), "schema_version": "future"})
    with pytest.raises(ValidationError):
        FeatureRow.model_validate({**row.model_dump(), "schema_hash": "b" * 64})


@pytest.mark.parametrize("kind", ["deserialize", "text", "provenance", "counts"])
def test_untrusted_or_tampered_payload_fails_safely(kind: str) -> None:
    value = payload("invented-private-marker")
    if kind == "deserialize":
        value = SanitizedPayload.model_validate_json(value.model_dump_json())
    else:
        changes: dict[str, object] = {
            "text": "invented-private-other",
            "provenance": {},
            "counts": (),
        }
        key = "privacy_counts" if kind == "counts" else kind
        value = value.model_copy(update={key: changes[kind]})
        if kind == "counts":
            value = value.model_copy(update={"digest": "c" * 64})
    with pytest.raises(FeatureError) as caught:
        extract_features(value, TaskCategory.FORMAT, entry())
    assert "invented-private" not in str(caught.value)


def test_malformed_copied_candidate_cannot_export_fake_capability(
    recwarn: pytest.WarningsRecorder,
) -> None:
    candidate = entry()
    invalid = candidate.model_copy(update={"manifest": {"private": "invented-private-marker"}})
    with pytest.raises(FeatureError) as caught:
        extract_features(payload(), TaskCategory.FORMAT, invalid)
    assert "invented-private" not in str(caught.value)
    assert not recwarn
