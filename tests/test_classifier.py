"""Deterministic Step 06 task rules over invented, successfully sanitized inputs."""

import ast
import logging
import warnings
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.broker.classifier import (
    ClassificationError,
    ClassificationReason,
    classify_task,
)
from backend.broker.validators import JsonObjectSchema, ResponseConstraints, ResponseFormat
from backend.contracts import TaskCategory
from backend.privacy.analyzer import DetectionSpan
from backend.privacy.sanitized_payload import SanitizedPayload
from backend.privacy.sanitizer import Sanitizer


class FixtureAnalyzer:
    version = "synthetic-classifier-fixture-v1"

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        return ()


def sanitized(text: str) -> SanitizedPayload:
    return Sanitizer(FixtureAnalyzer()).sanitize(uuid4(), text).payload


@pytest.mark.parametrize("hint", list(TaskCategory))
def test_user_hint_wins_over_all_heuristics_and_shape(hint: TaskCategory) -> None:
    result = classify_task(
        sanitized("Extract and summarize code, choose one label, solve step by step in JSON"),
        hint,
        ResponseConstraints(response_format=ResponseFormat.JSON, required_fields=("invented",)),
    )
    assert result.task == hint
    assert result.reason == ClassificationReason.USER_HINT


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Return JSON for a Python function", TaskCategory.FORMAT),
        ("Summarize this paragraph in JSON", TaskCategory.FORMAT),
        ("Return a CSV table containing the listed facts", TaskCategory.FORMAT),
        ("Output three bullets about a fixed concept", TaskCategory.FORMAT),
        ("Use this JSON schema", TaskCategory.FORMAT),
        ("Extract the invented fields then summarize them", TaskCategory.EXTRACT),
        ("Pull out dates from this invented sentence", TaskCategory.EXTRACT),
        ("Write a Python function then summarize the approach", TaskCategory.CODE),
        ("Write Python code to extract email addresses", TaskCategory.CODE),
        ("Debug this script step by step", TaskCategory.CODE),
        ("Explain this function", TaskCategory.CODE),
        ("```python\nprint('invented')\n```", TaskCategory.CODE),
        ("Summarise this invented paragraph and choose one label", TaskCategory.SUMMARISE),
        ("Summarize this invented paragraph", TaskCategory.SUMMARISE),
        ("Condense this invented paragraph", TaskCategory.SUMMARISE),
        ("Classify sentiment using positive, negative or neutral", TaskCategory.CLASSIFY),
        ("Choose exactly one of the allowed labels", TaskCategory.CLASSIFY),
        ("Label this invented example, then explain why", TaskCategory.CLASSIFY),
        ("Solve the invented logic puzzle step by step", TaskCategory.REASON),
        ("Why does this fixed algorithm terminate?", TaskCategory.REASON),
        ("Calculate the result of this invented arithmetic", TaskCategory.REASON),
        ("What is a fixed concept?", TaskCategory.QA),
        ("Invented arbitrary input", TaskCategory.QA),
    ],
)
def test_category_precedence_and_qa_fallback(text: str, expected: TaskCategory) -> None:
    assert classify_task(sanitized(text)).task == expected


@pytest.mark.parametrize(
    ("constraints", "expected"),
    [
        (ResponseConstraints(required_fields=("field",)), TaskCategory.EXTRACT),
        (ResponseConstraints(json_schema=JsonObjectSchema()), TaskCategory.FORMAT),
        (ResponseConstraints(response_format=ResponseFormat.JSON), TaskCategory.FORMAT),
        (ResponseConstraints(min_bullets=2), TaskCategory.FORMAT),
        (ResponseConstraints(max_chars=500), TaskCategory.FORMAT),
        (ResponseConstraints(allowed_labels=("a", "b")), TaskCategory.CLASSIFY),
    ],
)
def test_explicit_shape_and_labels_are_available_without_prompt_parsing(
    constraints: ResponseConstraints, expected: TaskCategory
) -> None:
    assert (
        classify_task(sanitized("An invented fixed request"), constraints=constraints).task
        == expected
    )


def test_explicit_required_fields_still_precede_code_and_extraction_cues() -> None:
    result = classify_task(
        sanitized("Write Python code to extract invented names"),
        constraints=ResponseConstraints(required_fields=("name",)),
    )
    assert result.task == TaskCategory.EXTRACT
    assert result.reason == ClassificationReason.REQUIRED_FIELDS


def test_untrusted_or_tampered_payloads_fail_safely() -> None:
    trusted = sanitized("invented-private-classifier-marker")
    untrusted = SanitizedPayload.model_validate_json(trusted.model_dump_json())
    malformed: list[Any] = [
        None,
        "invented-private-classifier-marker",
        untrusted,
        trusted.model_copy(update={"text": "invented-private-tamper-marker"}),
        trusted.model_copy(update={"provenance": {}}),
        SanitizedPayload.model_construct(),
    ]
    for value in malformed:
        with pytest.raises(ClassificationError) as caught:
            classify_task(value)
        assert "invented-private" not in str(caught.value)


def test_bad_hint_or_unchecked_constraints_fail_safely() -> None:
    payload = sanitized("invented-private-classifier-marker")
    with pytest.raises(ClassificationError):
        classify_task(payload, task_hint="code")  # type: ignore[arg-type]
    with pytest.raises(ClassificationError):
        classify_task(
            payload, constraints=ResponseConstraints().model_copy(update={"max_chars": -1})
        )
    with pytest.raises(ClassificationError):
        classify_task(payload, constraints={"response_format": "json"})  # type: ignore[arg-type]
    malformed = ResponseConstraints().model_copy(update={"max_chars": "invented-private-marker"})
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        with pytest.raises(ClassificationError):
            classify_task(payload, constraints=malformed)
    assert not recorded


def test_result_is_immutable_content_free_and_nothing_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "invented-private-classifier-marker"
    with caplog.at_level(logging.DEBUG):
        result = classify_task(sanitized(marker))
    assert marker not in repr(result)
    assert marker not in result.model_dump_json()
    assert not caplog.text
    with pytest.raises(ValidationError):
        result.task = TaskCategory.CODE


def test_classifier_and_validators_have_no_provider_or_dispatch_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("classifier.py", "validators.py"):
        tree = ast.parse((root / "backend" / "broker" / name).read_text(encoding="utf-8"))
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        assert not any("providers" in module or "orchestrator" in module for module in imports)
