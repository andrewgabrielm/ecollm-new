"""Bounded response-shape checks, never factual correctness or code execution.

The optional schema is deliberately a flat JSON-object subset: named fields,
required flags, scalar/container types and extra-field policy. It is not a full
JSON Schema implementation and does not evaluate expressions or execute code.
"""

import json
import math
import re
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from backend.contracts import ContractModel

MAX_RESPONSE_CHARS = 64_000
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 4096
_Name = Annotated[StrictStr, Field(min_length=1, max_length=128)]
_Label = Annotated[StrictStr, Field(min_length=1, max_length=256)]
_Length = Annotated[StrictInt, Field(ge=0, le=MAX_RESPONSE_CHARS)]
_Bullets = Annotated[StrictInt, Field(ge=0, le=1000)]


class ResponseFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class JsonType(StrEnum):
    OBJECT = "object"
    ARRAY = "array"
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    NULL = "null"


class JsonField(ContractModel):
    name: _Name = Field(repr=False)
    kind: JsonType
    required: StrictBool = True


class JsonObjectSchema(ContractModel):
    fields: tuple[JsonField, ...] = Field(default=(), max_length=64, repr=False)
    allow_extra: StrictBool = False

    @model_validator(mode="after")
    def unique_fields(self) -> Self:
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("Schema field names must be unique")
        return self


class ResponseConstraints(ContractModel):
    response_format: ResponseFormat = ResponseFormat.TEXT
    required_fields: tuple[_Name, ...] = Field(default=(), max_length=64, repr=False)
    json_schema: JsonObjectSchema | None = Field(default=None, repr=False)
    allowed_labels: tuple[_Label, ...] = Field(default=(), max_length=64, repr=False)
    min_chars: _Length = 1
    max_chars: _Length = MAX_RESPONSE_CHARS
    min_bullets: _Bullets | None = None
    max_bullets: _Bullets | None = None

    @model_validator(mode="after")
    def coherent_constraints(self) -> Self:
        if self.min_chars > self.max_chars:
            raise ValueError("Minimum response length exceeds maximum")
        if (
            self.min_bullets is not None
            and self.max_bullets is not None
            and self.min_bullets > self.max_bullets
        ):
            raise ValueError("Minimum bullet count exceeds maximum")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("Required field names must be unique")
        if len(set(self.allowed_labels)) != len(self.allowed_labels):
            raise ValueError("Allowed labels must be unique")
        if any(not label.strip() for label in self.allowed_labels):
            raise ValueError("Labels must contain non-whitespace characters")
        if self.json_schema is not None and not self.json_schema.allow_extra:
            schema_names = {field.name for field in self.json_schema.fields}
            if not set(self.required_fields).issubset(schema_names):
                raise ValueError("Required fields must be declared by the closed schema")
        return self

    @property
    def requires_json(self) -> bool:
        return (
            self.response_format == ResponseFormat.JSON
            or bool(self.required_fields)
            or self.json_schema is not None
        )


class ValidationReason(StrEnum):
    VALID_STRUCTURE = "valid_structure"
    INVALID_CONSTRAINTS = "invalid_constraints"
    INVALID_TEXT = "invalid_text"
    LENGTH = "length"
    BULLET_COUNT = "bullet_count"
    INVALID_JSON = "invalid_json"
    JSON_COMPLEXITY = "json_complexity"
    REQUIRED_FIELDS = "required_fields"
    SCHEMA = "schema"
    LABEL = "label"


class ValidationResult(ContractModel):
    valid: StrictBool
    reason: ValidationReason

    @model_validator(mode="after")
    def coherent_result(self) -> Self:
        if self.valid != (self.reason == ValidationReason.VALID_STRUCTURE):
            raise ValueError("Validation status and reason disagree")
        return self


class _InvalidJson(ValueError):
    pass


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise _InvalidJson("Duplicate JSON object keys")
        output[key] = value
    return output


def _reject_constant(value: str) -> None:
    raise _InvalidJson("Nonfinite JSON constants are invalid")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise _InvalidJson("Nonfinite JSON numbers are invalid")
    return number


def _bullet_count(text: str) -> int:
    """Count simple unordered/numbered lines outside backtick/tilde code fences."""
    fence: str | None = None
    count = 0
    for line in text.splitlines():
        stripped = line.lstrip(" \t")
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker is not None:
            delimiter = marker.group()
            if fence is None:
                fence = delimiter
            elif delimiter[0] == fence[0] and len(delimiter) >= len(fence):
                fence = None
            continue
        if fence is None and re.match(r"^[ \t]{0,8}(?:[-*+][ \t]+|[0-9]{1,4}[.)][ \t]+)\S", line):
            count += 1
    return count


def _bounded_json(value: object) -> bool:
    queue = [(value, 0)]
    visited = 0
    while queue:
        item, depth = queue.pop()
        visited += 1
        if visited > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return False
        if isinstance(item, dict):
            queue.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            queue.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            return False
    return True


def _matches_type(value: object, kind: JsonType) -> bool:
    if kind == JsonType.NULL:
        return value is None
    if kind == JsonType.BOOLEAN:
        return isinstance(value, bool)
    if kind == JsonType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == JsonType.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == JsonType.STRING:
        return isinstance(value, str)
    if kind == JsonType.ARRAY:
        return isinstance(value, list)
    return isinstance(value, dict)


def _schema_valid(value: object, schema: JsonObjectSchema) -> bool:
    if not isinstance(value, dict):
        return False
    names = {field.name for field in schema.fields}
    if not schema.allow_extra and set(value) - names:
        return False
    return all(
        (
            not field.required
            if field.name not in value
            else _matches_type(value[field.name], field.kind)
        )
        for field in schema.fields
    )


def validate_response(
    text: str, constraints: ResponseConstraints | None = None
) -> ValidationResult:
    """Return only content-free shape results; a pass never establishes answer truth."""
    try:
        if constraints is not None and not isinstance(constraints, ResponseConstraints):
            raise ValueError("Invalid constraints type")
        spec = ResponseConstraints.model_validate(
            ResponseConstraints().model_dump()
            if constraints is None
            else constraints.model_dump(warnings=False)
        )
    except (AttributeError, TypeError, ValueError):
        return ValidationResult(valid=False, reason=ValidationReason.INVALID_CONSTRAINTS)
    if not isinstance(text, str) or not text.strip():
        return ValidationResult(valid=False, reason=ValidationReason.INVALID_TEXT)
    if len(text) > MAX_RESPONSE_CHARS or not spec.min_chars <= len(text) <= spec.max_chars:
        return ValidationResult(valid=False, reason=ValidationReason.LENGTH)
    bullets = _bullet_count(text)
    if (spec.min_bullets is not None and bullets < spec.min_bullets) or (
        spec.max_bullets is not None and bullets > spec.max_bullets
    ):
        return ValidationResult(valid=False, reason=ValidationReason.BULLET_COUNT)
    value: object = text.strip()
    if spec.requires_json:
        try:
            value = json.loads(
                text,
                object_pairs_hook=_object,
                parse_constant=_reject_constant,
                parse_float=_finite_float,
            )
        except (ValueError, TypeError, RecursionError):
            return ValidationResult(valid=False, reason=ValidationReason.INVALID_JSON)
        if not _bounded_json(value):
            return ValidationResult(valid=False, reason=ValidationReason.JSON_COMPLEXITY)
        if spec.required_fields and (
            not isinstance(value, dict) or not set(spec.required_fields).issubset(value)
        ):
            return ValidationResult(valid=False, reason=ValidationReason.REQUIRED_FIELDS)
        if spec.json_schema is not None and not _schema_valid(value, spec.json_schema):
            return ValidationResult(valid=False, reason=ValidationReason.SCHEMA)
    if spec.allowed_labels and (not isinstance(value, str) or value not in spec.allowed_labels):
        return ValidationResult(valid=False, reason=ValidationReason.LABEL)
    return ValidationResult(valid=True, reason=ValidationReason.VALID_STRUCTURE)
