"""Step 11 scorer known-case tests across every task category."""

from __future__ import annotations

from typing import Any

import pytest

from backend.contracts import TaskCategory
from backend.learning.benchmarks.types import BenchmarkTask, make_task_id
from backend.learning.scorers import ScoreResult, score
from backend.learning.scorers.text import _rouge_l_f1


def _task(
    *,
    family_id: str,
    category: TaskCategory,
    prompt: str,
    reference: dict[str, Any],
    variant_index: int = 0,
) -> BenchmarkTask:
    return BenchmarkTask(
        task_id=make_task_id(family_id, variant_index),
        family_id=family_id,
        category=category,
        variant_index=variant_index,
        prompt=prompt,
        reference=reference,
    )


class TestQaScorer:
    def test_exact_match(self) -> None:
        task = _task(
            family_id="qa-capital-france",
            category=TaskCategory.QA,
            prompt="Capital of France?",
            reference={"answers": ["Paris"]},
        )
        result = score(task, "Paris")
        assert result.quality == 1.0
        assert result.format_valid is True

    def test_case_insensitive_and_whitespace(self) -> None:
        task = _task(
            family_id="qa-capital-france",
            category=TaskCategory.QA,
            prompt="Capital of France?",
            reference={"answers": ["Paris"]},
        )
        assert score(task, "  paris ").quality == 1.0

    def test_answer_embedded_in_sentence(self) -> None:
        task = _task(
            family_id="qa-capital-france",
            category=TaskCategory.QA,
            prompt="Capital of France?",
            reference={"answers": ["Paris"]},
        )
        assert score(task, "The capital is Paris.").quality == 1.0

    def test_wrong_answer(self) -> None:
        task = _task(
            family_id="qa-capital-france",
            category=TaskCategory.QA,
            prompt="Capital of France?",
            reference={"answers": ["Paris"]},
        )
        assert score(task, "Berlin").quality == 0.0

    def test_empty_response(self) -> None:
        task = _task(
            family_id="qa-capital-france",
            category=TaskCategory.QA,
            prompt="Capital of France?",
            reference={"answers": ["Paris"]},
        )
        result = score(task, "   ")
        assert result.quality == 0.0
        assert result.format_valid is False


class TestClassifyScorer:
    def test_exact_label(self) -> None:
        task = _task(
            family_id="classify-sentiment-pos-1",
            category=TaskCategory.CLASSIFY,
            prompt="pick a label",
            reference={
                "label": "positive",
                "labels": ["positive", "negative", "neutral"],
            },
        )
        assert score(task, "positive").quality == 1.0

    def test_label_in_prefix(self) -> None:
        task = _task(
            family_id="classify-sentiment-pos-1",
            category=TaskCategory.CLASSIFY,
            prompt="pick a label",
            reference={
                "label": "positive",
                "labels": ["positive", "negative", "neutral"],
            },
        )
        assert score(task, "Label: positive").quality == 1.0

    def test_wrong_label(self) -> None:
        task = _task(
            family_id="classify-sentiment-pos-1",
            category=TaskCategory.CLASSIFY,
            prompt="pick a label",
            reference={
                "label": "positive",
                "labels": ["positive", "negative", "neutral"],
            },
        )
        result = score(task, "negative")
        assert result.quality == 0.0
        assert result.format_valid is True

    def test_out_of_vocabulary(self) -> None:
        task = _task(
            family_id="classify-sentiment-pos-1",
            category=TaskCategory.CLASSIFY,
            prompt="pick a label",
            reference={
                "label": "positive",
                "labels": ["positive", "negative", "neutral"],
            },
        )
        result = score(task, "glorious")
        assert result.quality == 0.0
        assert result.format_valid is False


class TestSummariseScorer:
    def test_identical_summary(self) -> None:
        reference = "Plants convert sunlight into sugar and oxygen."
        task = _task(
            family_id="summarise-photosynthesis",
            category=TaskCategory.SUMMARISE,
            prompt="summarise",
            reference={"reference": reference, "source": "source text"},
        )
        result = score(task, reference)
        assert result.quality == pytest.approx(1.0)

    def test_partial_overlap(self) -> None:
        task = _task(
            family_id="summarise-photosynthesis",
            category=TaskCategory.SUMMARISE,
            prompt="summarise",
            reference={
                "reference": "Plants convert sunlight into sugar and oxygen.",
                "source": "source text",
            },
        )
        result = score(task, "Plants make sugar and oxygen.")
        assert 0.0 < result.quality < 1.0

    def test_unrelated_text(self) -> None:
        task = _task(
            family_id="summarise-photosynthesis",
            category=TaskCategory.SUMMARISE,
            prompt="summarise",
            reference={
                "reference": "Plants convert sunlight into sugar and oxygen.",
                "source": "source text",
            },
        )
        result = score(task, "Ice cream is cold.")
        assert result.quality == 0.0

    def test_rouge_l_exact(self) -> None:
        assert _rouge_l_f1("one two three", "one two three") == pytest.approx(1.0)

    def test_rouge_l_empty(self) -> None:
        assert _rouge_l_f1("", "anything") == 0.0


class TestExtractScorer:
    def test_perfect_json(self) -> None:
        task = _task(
            family_id="extract-invoice-small",
            category=TaskCategory.EXTRACT,
            prompt="extract",
            reference={"fields": {"id": "1042", "customer": "Acme Corp"}},
        )
        result = score(task, '{"id": "1042", "customer": "Acme Corp"}')
        assert result.quality == pytest.approx(1.0)
        assert result.format_valid is True

    def test_missing_field(self) -> None:
        task = _task(
            family_id="extract-invoice-small",
            category=TaskCategory.EXTRACT,
            prompt="extract",
            reference={"fields": {"id": "1042", "customer": "Acme Corp"}},
        )
        result = score(task, '{"id": "1042"}')
        assert 0.0 < result.quality < 1.0

    def test_wrong_field_value(self) -> None:
        task = _task(
            family_id="extract-invoice-small",
            category=TaskCategory.EXTRACT,
            prompt="extract",
            reference={"fields": {"id": "1042", "customer": "Acme Corp"}},
        )
        result = score(task, '{"id": "999", "customer": "Acme Corp"}')
        assert result.quality < 1.0

    def test_fenced_json_accepted(self) -> None:
        task = _task(
            family_id="extract-invoice-small",
            category=TaskCategory.EXTRACT,
            prompt="extract",
            reference={"fields": {"id": "1042"}},
        )
        result = score(task, '```json\n{"id": "1042"}\n```')
        assert result.quality == pytest.approx(1.0)

    def test_non_json_is_zero_and_invalid(self) -> None:
        task = _task(
            family_id="extract-invoice-small",
            category=TaskCategory.EXTRACT,
            prompt="extract",
            reference={"fields": {"id": "1042"}},
        )
        result = score(task, "the id is 1042")
        assert result.quality == 0.0
        assert result.format_valid is False


class TestFormatScorer:
    def test_matching_schema(self) -> None:
        task = _task(
            family_id="format-point",
            category=TaskCategory.FORMAT,
            prompt="json",
            reference={"schema": {"x": "integer", "y": "integer"}},
        )
        result = score(task, '{"x": 2, "y": 3}')
        assert result.quality == 1.0
        assert result.format_valid is True

    def test_wrong_type(self) -> None:
        task = _task(
            family_id="format-point",
            category=TaskCategory.FORMAT,
            prompt="json",
            reference={"schema": {"x": "integer"}},
        )
        result = score(task, '{"x": "two"}')
        assert result.quality == 0.0
        assert result.format_valid is False

    def test_missing_key(self) -> None:
        task = _task(
            family_id="format-point",
            category=TaskCategory.FORMAT,
            prompt="json",
            reference={"schema": {"x": "integer", "y": "integer"}},
        )
        result = score(task, '{"x": 1}')
        assert result.quality == 0.0

    def test_extra_key(self) -> None:
        task = _task(
            family_id="format-point",
            category=TaskCategory.FORMAT,
            prompt="json",
            reference={"schema": {"x": "integer"}},
        )
        result = score(task, '{"x": 1, "extra": 2}')
        assert result.quality == 0.0

    def test_boolean_rejects_integer(self) -> None:
        task = _task(
            family_id="format-flag",
            category=TaskCategory.FORMAT,
            prompt="json",
            reference={"schema": {"enabled": "boolean"}},
        )
        result = score(task, '{"enabled": 1}')
        assert result.quality == 0.0


class TestReasonScorer:
    def test_short_answer(self) -> None:
        task = _task(
            family_id="reason-apples-sharing",
            category=TaskCategory.REASON,
            prompt="How many apples?",
            reference={"answers": ["4", "four"]},
        )
        assert score(task, "4").quality == 1.0
        assert score(task, "four").quality == 1.0

    def test_wrong_scalar(self) -> None:
        task = _task(
            family_id="reason-apples-sharing",
            category=TaskCategory.REASON,
            prompt="How many apples?",
            reference={"answers": ["4"]},
        )
        assert score(task, "5").quality == 0.0


class TestCodeScorer:
    def test_passing_candidate(self) -> None:
        task = _task(
            family_id="code-add",
            category=TaskCategory.CODE,
            prompt="add",
            reference={
                "function": "add",
                "signature": "def add(a: int, b: int) -> int:",
                "tests": [
                    {"input": [0, 0], "expected": 0},
                    {"input": [1, 2], "expected": 3},
                    {"input": [-3, 5], "expected": 2},
                ],
            },
        )
        source = "def add(a, b):\n    return a + b\n"
        result = score(task, source)
        assert isinstance(result, ScoreResult)
        assert result.quality == 1.0
        assert result.format_valid is True

    def test_partially_correct_candidate(self) -> None:
        task = _task(
            family_id="code-add",
            category=TaskCategory.CODE,
            prompt="add",
            reference={
                "function": "add",
                "signature": "def add(a: int, b: int) -> int:",
                "tests": [
                    {"input": [1, 2], "expected": 3},
                    {"input": [10, 20], "expected": 30},
                ],
            },
        )
        # Off-by-one; passes nothing.
        result = score(task, "def add(a, b):\n    return a + b + 1\n")
        assert result.quality == 0.0
        # Format is still valid because the function executes and returns an int.
        assert result.format_valid is True

    def test_syntax_error_candidate(self) -> None:
        task = _task(
            family_id="code-add",
            category=TaskCategory.CODE,
            prompt="add",
            reference={
                "function": "add",
                "signature": "def add(a: int, b: int) -> int:",
                "tests": [{"input": [1, 2], "expected": 3}],
            },
        )
        result = score(task, "def add(a, b:\n    return a + b")
        assert result.quality == 0.0
        assert result.format_valid is False

    def test_missing_function(self) -> None:
        task = _task(
            family_id="code-add",
            category=TaskCategory.CODE,
            prompt="add",
            reference={
                "function": "add",
                "signature": "def add(a: int, b: int) -> int:",
                "tests": [{"input": [1, 2], "expected": 3}],
            },
        )
        result = score(task, "def subtract(a, b):\n    return a - b\n")
        assert result.quality == 0.0
        assert result.format_valid is False

    def test_fenced_candidate(self) -> None:
        task = _task(
            family_id="code-add",
            category=TaskCategory.CODE,
            prompt="add",
            reference={
                "function": "add",
                "signature": "def add(a: int, b: int) -> int:",
                "tests": [{"input": [1, 2], "expected": 3}],
            },
        )
        fenced = "```python\ndef add(a, b):\n    return a + b\n```"
        assert score(task, fenced).quality == 1.0
