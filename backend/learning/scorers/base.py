"""Common scorer protocol, normalization helpers and category dispatch.

Correctness (``quality``, in [0, 1]) is kept separate from format validity
(``format_valid``, bool). Scorers may surface additional detail counts
under ``details`` for diagnostics; the mapping is JSON-friendly.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from backend.contracts import TaskCategory
from backend.learning.benchmarks.types import BenchmarkTask
from backend.versions import JSONValue

_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s.-]")


@dataclass(frozen=True)
class ScoreResult:
    quality: float
    format_valid: bool
    details: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= self.quality <= 1.0):
            raise ValueError("quality must be in [0, 1]")


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation (keeping word chars, dot and hyphen), collapse spaces."""
    lowered = text.lower()
    cleaned = _PUNCT.sub(" ", lowered)
    return _WHITESPACE.sub(" ", cleaned).strip()


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def score(task: BenchmarkTask, candidate: str) -> ScoreResult:
    # Imported lazily so submodule ordering stays flat.
    from backend.learning.scorers import code as code_scorer
    from backend.learning.scorers import structured, text

    dispatch = {
        TaskCategory.QA: text.score_qa,
        TaskCategory.REASON: text.score_reason,
        TaskCategory.SUMMARISE: text.score_summarise,
        TaskCategory.CLASSIFY: text.score_classify,
        TaskCategory.EXTRACT: structured.score_extract,
        TaskCategory.FORMAT: structured.score_format,
        TaskCategory.CODE: code_scorer.score_code,
    }
    scorer = dispatch[task.category]
    return scorer(task, candidate)


def score_batch(
    pairs: Iterable[tuple[BenchmarkTask, str]],
) -> tuple[ScoreResult, ...]:
    return tuple(score(task, candidate) for task, candidate in pairs)
