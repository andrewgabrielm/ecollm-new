"""Response format checks deliberately do not establish answer correctness."""

import json
import logging
import warnings
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.broker.validators import (
    JsonField,
    JsonObjectSchema,
    JsonType,
    ResponseConstraints,
    ValidationReason,
    validate_response,
)


def json_constraints(**changes: Any) -> ResponseConstraints:
    return ResponseConstraints.model_validate({"response_format": "json", **changes})


def test_open_ended_and_factually_wrong_answers_pass_only_structure() -> None:
    assert validate_response("A fixed explanation.").valid
    result = validate_response('{"answer":"two plus two is five"}', json_constraints())
    assert result.valid
    assert result.reason == ValidationReason.VALID_STRUCTURE
    assert "correct" not in type(result).model_fields


@pytest.mark.parametrize("text", ["", " \n\t", None, 123, b"bytes"])
def test_blank_or_wrong_type_is_invalid_text(text: Any) -> None:
    result = validate_response(text)
    assert not result.valid
    assert result.reason == ValidationReason.INVALID_TEXT


@pytest.mark.parametrize(
    "text",
    [
        '{"a":1,"a":2}',
        '{"a":{"nested":1,"nested":2}}',
        '{"answer":NaN}',
        '{"answer":Infinity}',
        '{"answer":-Infinity}',
        '{"answer":1e999}',
        '{"a":1,}',
        "```json\n{}\n```",
        "{} trailing invented text",
        "{'a':1}",
    ],
)
def test_json_is_strict_and_cannot_hide_ambiguous_fields(text: str) -> None:
    result = validate_response(text, json_constraints())
    assert not result.valid
    assert result.reason == ValidationReason.INVALID_JSON


@pytest.mark.parametrize("text", ["{}", "[]", '"invented"', "null", "false", "2", "2.5"])
def test_json_format_accepts_valid_json_values_without_object_requirement(text: str) -> None:
    assert validate_response(text, json_constraints()).valid


def test_required_fields_are_flat_presence_checks_including_null() -> None:
    spec = ResponseConstraints(required_fields=("answer",))
    assert spec.requires_json
    assert validate_response('{"answer":null}', spec).valid
    assert validate_response('{"answer":"invented", "extra":1}', spec).valid
    for text in ("{}", "[]", '{"nested":{"answer":1}}'):
        result = validate_response(text, spec)
        assert not result.valid
        assert result.reason == ValidationReason.REQUIRED_FIELDS
    dotted = ResponseConstraints(required_fields=("nested.answer",))
    assert not validate_response('{"nested":{"answer":1}}', dotted).valid
    assert validate_response('{"nested.answer":1}', dotted).valid


@pytest.mark.parametrize(
    ("kind", "valid_value", "invalid_value"),
    [
        (JsonType.STRING, "invented", 1),
        (JsonType.INTEGER, 1, True),
        (JsonType.NUMBER, 1.5, False),
        (JsonType.BOOLEAN, True, 1),
        (JsonType.NULL, None, "null"),
        (JsonType.OBJECT, {"nested": 1}, []),
        (JsonType.ARRAY, [1], {}),
    ],
)
def test_flat_schema_enforces_declared_types_without_coercion(
    kind: JsonType, valid_value: object, invalid_value: object
) -> None:
    spec = ResponseConstraints(
        json_schema=JsonObjectSchema(fields=(JsonField(name="answer", kind=kind),))
    )
    assert validate_response(json.dumps({"answer": valid_value}), spec).valid
    result = validate_response(json.dumps({"answer": invalid_value}), spec)
    assert not result.valid
    assert result.reason == ValidationReason.SCHEMA
    assert not validate_response("{}", spec).valid
    assert not validate_response(json.dumps({"answer": valid_value, "extra": 1}), spec).valid


def test_optional_fields_and_explicit_extra_policy() -> None:
    schema = JsonObjectSchema(
        fields=(JsonField(name="optional", kind=JsonType.STRING, required=False),),
        allow_extra=True,
    )
    spec = ResponseConstraints(json_schema=schema)
    assert validate_response("{}", spec).valid
    assert validate_response('{"extra":1}', spec).valid
    assert not validate_response('{"optional":null}', spec).valid


def test_labels_are_case_sensitive_whole_answer_matches() -> None:
    spec = ResponseConstraints(allowed_labels=("positive", "negative"))
    assert validate_response(" positive \n", spec).valid
    for answer in ("Positive", "positive because…", "unknown"):
        result = validate_response(answer, spec)
        assert not result.valid
        assert result.reason == ValidationReason.LABEL
    json_spec = json_constraints(allowed_labels=("positive", "negative"))
    assert validate_response('"positive"', json_spec).valid
    assert not validate_response('{"label":"positive"}', json_spec).valid


def test_length_counts_unicode_characters_and_preserves_whitespace() -> None:
    spec = ResponseConstraints(min_chars=2, max_chars=2)
    assert validate_response("é🦉", spec).valid
    assert validate_response(" x", spec).valid
    for text in ("x", "xyz", "x" * 64001):
        result = validate_response(text, spec)
        assert not result.valid
        assert result.reason == ValidationReason.LENGTH


def test_bullet_limits_include_numbered_lists_and_ignore_fenced_code() -> None:
    spec = ResponseConstraints(min_bullets=2, max_bullets=2)
    assert validate_response("- first\n* second", spec).valid
    assert validate_response("1. first\n2) second", spec).valid
    assert validate_response("```python\n- code\n```\n- first\n- second", spec).valid
    assert validate_response("~~~\n- code\n~~~\n- first\n- second", spec).valid
    for text in ("- one", "- one\n- two\n- three", "- \n- \ntext"):
        result = validate_response(text, spec)
        assert not result.valid
        assert result.reason == ValidationReason.BULLET_COUNT


@pytest.mark.parametrize("text", ["[" * 33 + "0" + "]" * 33, json.dumps([0] * 4097)])
def test_json_complexity_is_bounded(text: str) -> None:
    result = validate_response(text, json_constraints())
    assert not result.valid
    assert result.reason == ValidationReason.JSON_COMPLEXITY


@pytest.mark.parametrize(
    "values",
    [
        {"min_chars": True},
        {"max_chars": -1},
        {"min_chars": 3, "max_chars": 2},
        {"min_bullets": 2, "max_bullets": 1},
        {"allowed_labels": ["duplicate", "duplicate"]},
        {"allowed_labels": ["  "]},
        {"required_fields": ["duplicate", "duplicate"]},
        {"response_format": "xml"},
        {"execute_code": True},
    ],
)
def test_constraints_reject_malformed_or_unknown_values(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResponseConstraints.model_validate(values)


def test_schema_rejects_duplicate_or_inconsistent_fields() -> None:
    field = JsonField(name="invented", kind=JsonType.STRING)
    with pytest.raises(ValidationError):
        JsonObjectSchema(fields=(field, field))
    with pytest.raises(ValidationError):
        ResponseConstraints(
            required_fields=("missing",), json_schema=JsonObjectSchema(fields=(field,))
        )


def test_unchecked_constraint_copies_fail_closed_and_results_do_not_echo_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "invented-private-validation-marker"
    malformed = ResponseConstraints().model_copy(update={"max_chars": -1})
    with caplog.at_level(logging.DEBUG):
        result = validate_response(marker, malformed)
        invalid_json = validate_response(
            '{"invented-private-validation-marker":', json_constraints()
        )
    assert result.reason == ValidationReason.INVALID_CONSTRAINTS
    assert marker not in repr(result)
    assert marker not in invalid_json.model_dump_json()
    assert not caplog.text
    constraints = ResponseConstraints(allowed_labels=(marker,), required_fields=(marker,))
    assert marker not in repr(constraints)
    assert not validate_response(marker, {}).valid  # type: ignore[arg-type]


def test_invalid_copied_field_types_never_emit_serialization_warnings() -> None:
    marker = "invented-private-validation-marker"
    malformed = ResponseConstraints().model_copy(update={"max_chars": marker})
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        result = validate_response(marker, malformed)
    assert not result.valid
    assert not recorded


def test_code_is_never_executed(tmp_path: Path) -> None:
    sentinel = tmp_path / "never-created.txt"
    code = f"__import__('pathlib').Path({str(sentinel)!r}).write_text('invented')"
    assert validate_response(code).valid
    assert not sentinel.exists()
