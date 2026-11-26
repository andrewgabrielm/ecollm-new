"""Offline privacy regression fixtures; no real user data or provider calls."""

import dataclasses
import importlib.util
import itertools
import json
import logging
import pickle
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from backend.api.schemas import (
    COMPOSED_CHARACTER_LIMIT,
    COMPOSED_HEADER_SLACK,
    HISTORY_CHARACTER_BUDGET,
    PROMPT_CHARACTER_LIMIT,
)
from backend.privacy.analyzer import (
    AnalyzerUnavailableError,
    DetectionSpan,
    PresidioAnalyzer,
    PrivacyError,
    resolve_overlaps,
)
from backend.privacy.inspector import MASKED_WARNING, OutputInspector
from backend.privacy.placeholder_map import PlaceholderMap
from backend.privacy.recognisers import offline_email_recognizer, synthetic_spans
from backend.privacy.sanitizer import Sanitizer

pytestmark = pytest.mark.privacy


class FixtureAnalyzer:
    version = "invented-offline-fixture-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        email = tuple(
            DetectionSpan("EMAIL_ADDRESS", match.start(), match.end())
            for match in re.finditer(r"[a-z.]+@example\.invalid", text)
        )
        return (*synthetic_spans(text), *email)


class FailingAnalyzer:
    version = "invented-failure-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        raise RuntimeError("invented-private-failure-marker")


def test_masks_synthetic_spans_counts_and_locally_restores_duplicates() -> None:
    analyzer = FixtureAnalyzer()
    text = "Contact invented.person@example.invalid for STU-1234-5678 or STU-1234-5678."
    request_id = uuid4()
    result = Sanitizer(analyzer).sanitize(request_id, text)
    assert "invented.person@example.invalid" not in result.payload.text
    assert "STU-1234-5678" not in result.payload.text
    assert result.payload.is_trusted
    assert len(result.placeholders.tokens) == 2
    counts = {item.entity_type: item.count for item in result.type_counts}
    assert counts == {"EMAIL_ADDRESS": 1, "SYNTHETIC_STUDENT_ID": 2}
    restored = OutputInspector(analyzer).inspect(
        request_id, result.payload.text, result.placeholders
    )
    assert restored.answer == text
    assert restored.restored
    assert not restored.cache_eligible
    assert not restored.warning


def test_clean_text_preserves_whitespace_and_is_only_potentially_cache_eligible() -> None:
    analyzer = FixtureAnalyzer()
    request_id = uuid4()
    result = Sanitizer(analyzer).sanitize(request_id, "  Explain a fixed concept.  ")
    assert result.payload.text == "  Explain a fixed concept.  "
    assert result.type_counts == ()
    inspection = OutputInspector(analyzer).inspect(
        request_id, "A fixed explanation.", result.placeholders
    )
    assert inspection.answer == "A fixed explanation."
    assert inspection.cache_eligible
    assert not inspection.restored
    assert not inspection.warning


@pytest.mark.parametrize("literal", ["[PERSON_1]", "[[PII_a_PERSON_1]]", "[EMAIL_ADDRESS_9]"])
def test_user_authored_placeholders_are_reserved_and_restored_as_literals(literal: str) -> None:
    request_id = uuid4()
    analyzer = FixtureAnalyzer()
    result = Sanitizer(analyzer).sanitize(request_id, f"Explain {literal} literally.")
    assert literal not in result.payload.text
    assert result.type_counts == ()
    inspection = OutputInspector(analyzer).inspect(
        request_id, result.payload.text, result.placeholders
    )
    assert inspection.answer == f"Explain {literal} literally."
    assert inspection.restored
    assert not inspection.cache_eligible


@pytest.mark.parametrize("unknown", ["[PERSON_1]", "[[PII_a_PERSON_1]]", "[[PII_malformed"])
def test_unknown_forged_or_malformed_output_tokens_are_withheld(unknown: str) -> None:
    request_id = uuid4()
    analyzer = FixtureAnalyzer()
    result = Sanitizer(analyzer).sanitize(request_id, "A clean invented prompt")
    inspection = OutputInspector(analyzer).inspect(
        request_id, f"Answer {unknown}", result.placeholders
    )
    assert inspection.answer == MASKED_WARNING
    assert inspection.warning
    assert not inspection.cache_eligible
    assert not inspection.restored


def test_foreign_request_tokens_never_restore_using_current_map() -> None:
    analyzer = FixtureAnalyzer()
    first = Sanitizer(analyzer).sanitize(uuid4(), "STU-1234-5678")
    second = Sanitizer(analyzer).sanitize(uuid4(), "EMP-8765-4321")
    inspected = OutputInspector(analyzer).inspect(
        first.payload.request_id, second.payload.text, first.placeholders
    )
    assert inspected.answer == MASKED_WARNING
    wrong_request = OutputInspector(analyzer).inspect(
        uuid4(), first.payload.text, first.placeholders
    )
    assert wrong_request.answer == MASKED_WARNING


def test_concurrent_requests_have_distinct_namespaces_and_independent_first_tokens() -> None:
    def sanitize(index: int) -> tuple[UUID, str, PlaceholderMap]:
        request_id = uuid4()
        result = Sanitizer(FixtureAnalyzer()).sanitize(request_id, f"STU-1234-{index:04d}")
        return request_id, result.payload.text, result.placeholders

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(sanitize, range(16)))
    assert len({text for _, text, _ in results}) == 16
    for index, (request_id, text, placeholders) in enumerate(results):
        assert text.endswith("_1]]")
        assert placeholders.restore(request_id, text) == (f"STU-1234-{index:04d}", True)
        foreign = results[(index + 1) % len(results)][1]
        assert OutputInspector(FixtureAnalyzer()).inspect(request_id, foreign, placeholders).warning


def test_overlap_resolution_is_deterministic_and_masks_the_complete_union() -> None:
    spans = (
        DetectionSpan("PERSON", 0, 6, 0.8),
        DetectionSpan("ORGANIZATION", 4, 10, 0.8),
        DetectionSpan("EMAIL_ADDRESS", 12, 15, 1),
    )
    expected = (
        DetectionSpan("ORGANIZATION", 0, 10, 0.8),
        DetectionSpan("EMAIL_ADDRESS", 12, 15, 1),
    )
    for order in itertools.permutations(spans):
        assert resolve_overlaps("0123456789__abc", order) == expected


def test_overlapping_duplicate_spans_do_not_duplicate_counts() -> None:
    class DuplicateAnalyzer:
        version = "duplicate-fixture-v1"

        def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
            return (DetectionSpan("PERSON", 0, len(text)),) * 2

    result = Sanitizer(DuplicateAnalyzer()).sanitize(uuid4(), "invented-person-marker")
    assert [(item.entity_type, item.count) for item in result.type_counts] == [("PERSON", 1)]
    assert len(result.placeholders.tokens) == 1


def test_overlap_resolution_preserves_every_sensitive_type_for_policy() -> None:
    class OverlapAnalyzer:
        version = "overlap-policy-fixture-v1"

        def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
            return (
                DetectionSpan("PERSON", 0, len(text), 1.0),
                DetectionSpan("CREDIT_CARD", 2, len(text) - 2, 0.5),
            )

    result = Sanitizer(OverlapAnalyzer()).sanitize(uuid4(), "invented-sensitive-marker")
    assert {item.entity_type: item.count for item in result.type_counts} == {
        "PERSON": 1,
        "CREDIT_CARD": 1,
    }
    assert len(result.placeholders.tokens) == 1
    assert "invented-sensitive-marker" not in result.payload.text


@pytest.mark.parametrize(
    "text", ["", " \n", "x" * (COMPOSED_CHARACTER_LIMIT + 1)], ids=["empty", "blank", "oversized"]
)
def test_invalid_sanitizer_input_cannot_produce_payload(text: str) -> None:
    # The cap bounds the whole composed transcript, not the user's prompt.
    with pytest.raises(PrivacyError, match="Privacy sanitization failed"):
        Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), text)


def test_a_full_prompt_and_history_together_stay_within_the_sanitizer_cap() -> None:
    # Regression: composing header + history + prompt once exceeded an 8000-char
    # cap, so every multi-turn request failed closed as a privacy error.
    largest = "x" * PROMPT_CHARACTER_LIMIT + "y" * HISTORY_CHARACTER_BUDGET
    assert len(largest) + COMPOSED_HEADER_SLACK <= COMPOSED_CHARACTER_LIMIT
    assert Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), largest).payload.is_trusted


def test_scanner_fault_is_safe_and_cannot_produce_trusted_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(PrivacyError) as caught:
        Sanitizer(FailingAnalyzer()).sanitize(uuid4(), "invented-private-input-marker")
    assert "invented-private" not in str(caught.value)
    assert "invented-private" not in caplog.text
    assert caught.value.__suppress_context__


def test_sanitizer_fault_clears_map_and_hides_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    touched: list[PlaceholderMap] = []

    def fail_add(self: PlaceholderMap, entity_type: str, value: str) -> str:
        touched.append(self)
        raise RuntimeError("invented-private-sanitizer-marker")

    monkeypatch.setattr(PlaceholderMap, "add", fail_add)
    with pytest.raises(PrivacyError) as caught:
        Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), "STU-1234-5678")
    assert touched[0].entry_count == 0
    assert "invented-private" not in str(caught.value)
    assert "invented-private" not in caplog.text
    assert caught.value.__suppress_context__


def test_output_scanner_fault_and_new_sensitive_output_are_masked() -> None:
    request_id = uuid4()
    sanitized = Sanitizer(FixtureAnalyzer()).sanitize(
        request_id, "Explain for person@example.invalid"
    )
    fault = OutputInspector(FailingAnalyzer()).inspect(
        request_id, "A clean answer", sanitized.placeholders
    )
    new_pii = OutputInspector(FixtureAnalyzer()).inspect(
        request_id, "Contact new.person@example.invalid", sanitized.placeholders
    )
    for inspection in (fault, new_pii):
        assert inspection.answer == MASKED_WARNING
        assert inspection.warning
        assert not inspection.cache_eligible
        assert not inspection.restored


def test_map_and_results_do_not_expose_values_in_reprs_or_serialization(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    marker = "invented.person@example.invalid"
    result = Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), marker)
    assert marker not in repr(result)
    assert marker not in repr(result.placeholders)
    assert marker not in result.payload.model_dump_json()
    for serialize in (
        lambda: json.dumps(result.placeholders),
        lambda: pickle.dumps(result.placeholders),
        lambda: dataclasses.asdict(result),
        result.placeholders.model_dump,
        result.placeholders.model_dump_json,
    ):
        with pytest.raises(TypeError) as caught:
            serialize()
        assert marker not in str(caught.value)
    assert not caplog.text
    assert not list(tmp_path.iterdir())


def test_cleared_map_never_restores() -> None:
    request_id = uuid4()
    result = Sanitizer(FixtureAnalyzer()).sanitize(request_id, "STU-1234-5678")
    result.placeholders.clear()
    assert result.placeholders.entry_count == 0
    with pytest.raises(PrivacyError):
        result.placeholders.restore(request_id, result.payload.text)
    assert (
        OutputInspector(FixtureAnalyzer())
        .inspect(request_id, result.payload.text, result.placeholders)
        .warning
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ("bad-type", 0, 1, 1.0),
        ("PERSON", -1, 1, 1.0),
        ("PERSON", 1, 1, 1.0),
        ("PERSON", 0, 1, float("nan")),
        ("PERSON", 0, 1, True),
    ],
)
def test_invalid_analyzer_spans_are_rejected(arguments: tuple[str, int, int, float]) -> None:
    with pytest.raises(PrivacyError):
        DetectionSpan(*arguments)


def test_out_of_bounds_analyzer_result_fails_closed() -> None:
    class InvalidAnalyzer:
        version = "invalid-fixture-v1"

        def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
            return (DetectionSpan("PERSON", 0, len(text) + 1),)

    with pytest.raises(PrivacyError):
        Sanitizer(InvalidAnalyzer()).sanitize(uuid4(), "invented marker")


def test_preload_missing_asset_never_attempts_import_or_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(AnalyzerUnavailableError, match="explicit setup required"):
        PresidioAnalyzer.preload()


def test_presidio_adapter_suppresses_raw_third_party_failures() -> None:
    class Engine:
        def analyze(self, **kwargs: object) -> list[object]:
            raise RuntimeError("invented-private-third-party-marker")

    adapter = PresidioAnalyzer(cast(Any, Engine()), "fake")
    with pytest.raises(PrivacyError) as caught:
        adapter.analyze("invented-private-input-marker")
    assert "invented-private" not in str(caught.value)


def test_presidio_debug_context_logs_cannot_expose_request_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Engine:
        def analyze(self, **kwargs: object) -> list[object]:
            logging.getLogger("presidio-analyzer").debug("Context list: invented-private-marker")
            return []

    with caplog.at_level(logging.DEBUG, logger="presidio-analyzer"):
        assert PresidioAnalyzer(cast(Any, Engine()), "fake").analyze("synthetic") == ()
    assert "invented-private" not in caplog.text


def test_real_email_recognizer_uses_only_bundled_suffix_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise the installed Presidio recognizer without a spaCy model. Any
    # accidental suffix HTTP fetch fails the assertion even if a dependency
    # catches its own network exception and falls back to a cached snapshot.
    tldextract = importlib.import_module("tldextract")
    requests = importlib.import_module("requests")
    outbound: list[str] = []

    def forbidden_send(*args: object, **kwargs: object) -> Any:
        outbound.append("attempt")
        raise RuntimeError("No suffix-list HTTP request is permitted")

    monkeypatch.setattr(tldextract, "extract", forbidden_send)
    monkeypatch.setattr(requests.Session, "send", forbidden_send)
    recognizer = offline_email_recognizer()
    matches = recognizer.analyze(
        text="Email invented.person@example.invalid or person@example.com",
        entities=["EMAIL_ADDRESS"],
        nlp_artifacts=None,
    )
    assert len(matches) == 2
    assert outbound == []


@pytest.mark.slow
def test_real_preloaded_presidio_smoke_when_model_is_installed() -> None:
    if importlib.util.find_spec("en_core_web_sm") is None:
        pytest.skip(
            "NOT_RUN: pinned local en_core_web_sm asset is not installed; no download attempted"
        )
    analyzer = PresidioAnalyzer.preload()
    text = "Synthetic contact invented.person@example.invalid with ID STU-1234-5678."
    result = Sanitizer(analyzer).sanitize(uuid4(), text)
    assert "invented.person@example.invalid" not in result.payload.text
    assert "STU-1234-5678" not in result.payload.text
    assert result.payload.is_trusted
