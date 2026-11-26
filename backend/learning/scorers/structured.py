"""Structured scorers for extract (field F1) and format (strict JSON schema).

Both scorers parse the candidate as JSON. Format validity is reported
regardless of correctness so routers can learn the two axes independently.
"""

import json
import re
from typing import Any

from backend.learning.benchmarks.types import BenchmarkTask
from backend.learning.scorers.base import ScoreResult, normalize_text
from backend.versions import JSONValue

# Models often wrap JSON in a Markdown code fence. Strip a single leading/trailing fence
# before attempting to parse; otherwise leave the text alone.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _parse_json(candidate: str) -> JSONValue | None:
    text = candidate.strip()
    match = _FENCE.match(text)
    if match:
        text = match.group(1)
    try:
        parsed: JSONValue = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed


def _as_mapping(value: JSONValue) -> dict[str, JSONValue] | None:
    if not isinstance(value, dict):
        return None
    return value


def score_extract(task: BenchmarkTask, candidate: str) -> ScoreResult:
    reference_value = task.reference["fields"]
    if not isinstance(reference_value, dict):
        raise TypeError("extract reference must be a mapping")
    reference_fields: dict[str, str] = {}
    for key, raw in reference_value.items():
        if not isinstance(raw, str):
            raise TypeError("extract reference values must be strings")
        reference_fields[key] = raw
    parsed = _parse_json(candidate)
    parsed_map = _as_mapping(parsed) if parsed is not None else None
    if parsed_map is None:
        return ScoreResult(
            quality=0.0,
            format_valid=False,
            details={"parsed": False},
        )
    tp = 0
    fp = 0
    fn = 0
    for key, expected in reference_fields.items():
        got = parsed_map.get(key)
        if got is None:
            fn += 1
            continue
        got_text = got if isinstance(got, str) else json.dumps(got, sort_keys=True)
        if normalize_text(got_text) == normalize_text(expected):
            tp += 1
        else:
            fp += 1
            fn += 1
    # Extra keys the reference doesn't ask for count as false positives.
    for extra_key in parsed_map.keys() - reference_fields.keys():
        if parsed_map[extra_key] not in (None, "", []):
            fp += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)
    return ScoreResult(
        quality=max(0.0, min(1.0, f1)),
        format_valid=True,
        details={
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
        },
    )


_TYPE_CHECKS: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _matches_schema(value: JSONValue, schema: dict[str, str]) -> tuple[bool, list[str]]:
    if not isinstance(value, dict):
        return False, ["not_object"]
    issues: list[str] = []
    for key, expected_type in schema.items():
        if key not in value:
            issues.append(f"missing:{key}")
            continue
        got = value[key]
        checker = _TYPE_CHECKS.get(expected_type)
        if checker is None:
            issues.append(f"unknown_type:{expected_type}")
            continue
        # Booleans are ints in Python; disallow bool masquerading as integer/number.
        if expected_type in {"integer", "number"} and isinstance(got, bool):
            issues.append(f"type:{key}")
            continue
        if not isinstance(got, checker):
            issues.append(f"type:{key}")
    extra = set(value.keys()) - set(schema.keys())
    for extra_key in sorted(extra):
        issues.append(f"extra:{extra_key}")
    return (len(issues) == 0), issues


def score_format(task: BenchmarkTask, candidate: str) -> ScoreResult:
    schema_value = task.reference["schema"]
    if not isinstance(schema_value, dict):
        raise TypeError("format reference schema must be a mapping")
    schema: dict[str, str] = {}
    for key, raw in schema_value.items():
        if not isinstance(raw, str):
            raise TypeError("format reference schema types must be strings")
        schema[key] = raw
    parsed = _parse_json(candidate)
    if parsed is None:
        return ScoreResult(
            quality=0.0,
            format_valid=False,
            details={"parsed": False},
        )
    ok, issues = _matches_schema(parsed, schema)
    issue_payload: list[JSONValue] = list(issues)
    return ScoreResult(
        quality=1.0 if ok else 0.0,
        format_valid=ok,
        details={"issues": issue_payload},
    )
