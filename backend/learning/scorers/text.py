"""Text scorers for QA, reason, summarise and classify categories.

QA and reason accept any reference surface form after shared normalization.
Summarise uses ROUGE-L F1 implemented in pure Python. Classify requires an
exact label match from the task's closed label set.
"""

from collections.abc import Sequence

from backend.learning.benchmarks.types import BenchmarkTask
from backend.learning.scorers.base import ScoreResult, normalize_text, tokenize
from backend.versions import JSONValue


def _as_str_list(value: JSONValue) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("expected list in reference payload")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError("expected list of strings in reference payload")
        out.append(item)
    return tuple(out)


def _as_str(value: JSONValue) -> str:
    if not isinstance(value, str):
        raise TypeError("expected string in reference payload")
    return value


def _match_answer(candidate: str, answers: Sequence[str]) -> bool:
    normalized = normalize_text(candidate)
    if not normalized:
        return False
    candidate_tokens = tokenize(candidate)
    for answer in answers:
        target = normalize_text(answer)
        if not target:
            continue
        if normalized == target:
            return True
        # Short answers often appear embedded in a sentence; require word boundary match
        # over alphanumeric tokens so trailing punctuation does not block the match.
        target_tokens = tokenize(answer)
        if len(target_tokens) == 1 and target_tokens[0] in candidate_tokens:
            return True
    return False


def score_qa(task: BenchmarkTask, candidate: str) -> ScoreResult:
    answers = _as_str_list(task.reference["answers"])
    matched = _match_answer(candidate, answers)
    return ScoreResult(
        quality=1.0 if matched else 0.0,
        format_valid=bool(candidate.strip()),
        details={"matched_answers": [a for a in answers if _match_answer(candidate, (a,))]},
    )


def score_reason(task: BenchmarkTask, candidate: str) -> ScoreResult:
    # Reasoning tasks are scored as QA-style exact-answer match. The orchestrator
    # passes only the final answer surface; reasoning traces are not graded here.
    return score_qa(task, candidate)


def score_classify(task: BenchmarkTask, candidate: str) -> ScoreResult:
    label = _as_str(task.reference["label"])
    labels = _as_str_list(task.reference["labels"])
    normalized_candidate = normalize_text(candidate)
    normalized_labels = {normalize_text(lbl): lbl for lbl in labels}
    predicted = normalized_labels.get(normalized_candidate)
    if predicted is None:
        # Tolerate "Label: foo" style prefixes by looking for any label as a whole word.
        tokens = set(tokenize(candidate))
        for norm, original in normalized_labels.items():
            if norm in tokens:
                predicted = original
                break
    format_valid = predicted is not None
    quality = 1.0 if predicted == label else 0.0
    return ScoreResult(
        quality=quality,
        format_valid=format_valid,
        details={"predicted": predicted or ""},
    )


def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
    n, m = len(left), len(right)
    if n == 0 or m == 0:
        return 0
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if left[i - 1] == right[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[n][m]


def _rouge_l_f1(candidate: str, reference: str) -> float:
    cand_tokens = tokenize(candidate)
    ref_tokens = tokenize(reference)
    if not cand_tokens or not ref_tokens:
        return 0.0
    lcs = _lcs_length(cand_tokens, ref_tokens)
    if lcs == 0:
        return 0.0
    precision = lcs / len(cand_tokens)
    recall = lcs / len(ref_tokens)
    return (2 * precision * recall) / (precision + recall)


def score_summarise(task: BenchmarkTask, candidate: str) -> ScoreResult:
    reference = _as_str(task.reference["reference"])
    rouge = _rouge_l_f1(candidate, reference)
    # Normalize to [0, 1] already; clamp defensively against float arithmetic drift.
    quality = max(0.0, min(1.0, rouge))
    return ScoreResult(
        quality=quality,
        format_valid=bool(candidate.strip()),
        details={"rouge_l_f1": round(quality, 6)},
    )
