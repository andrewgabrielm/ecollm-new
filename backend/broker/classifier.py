"""Deterministic task rules over trusted sanitized text; no provider or quality claims."""

import re
from enum import StrEnum

from backend.broker.validators import MAX_RESPONSE_CHARS, ResponseConstraints
from backend.contracts import ContractModel, TaskCategory
from backend.privacy.sanitized_payload import SanitizedPayload


class ClassificationError(ValueError):
    """Safe invalid classification input; source content is never attached."""


class ClassificationReason(StrEnum):
    USER_HINT = "user_hint"
    REQUIRED_FIELDS = "required_fields"
    SHAPE_CONSTRAINT = "shape_constraint"
    EXTRACTION_CUE = "extraction_cue"
    CODE_CUE = "code_cue"
    SUMMARY_CUE = "summary_cue"
    LABEL_CUE = "label_cue"
    REASONING_CUE = "reasoning_cue"
    QA_FALLBACK = "qa_fallback"


class TaskClassification(ContractModel):
    task: TaskCategory
    reason: ClassificationReason


_FLAGS = re.IGNORECASE | re.DOTALL
_FORMAT = re.compile(
    r"\b(?:json\s+schema|(?:as|in)\s+(?:valid\s+)?json)\b"
    r"|\b(?:return|respond|output|format|produce)\b.{0,80}"
    r"\b(?:json|csv|xml|yaml|table|bullets?|bulleted\s+list)\b",
    _FLAGS,
)
_EXTRACT = re.compile(r"\b(?:extract|extraction|pull\s+out)\b", _FLAGS)
_CODE = re.compile(
    r"```|\b(?:write|implement|debug|fix|refactor|generate|review|explain)\b.{0,100}"
    r"\b(?:code|function|script|program|python|javascript|typescript|sql|rust|java)\b",
    _FLAGS,
)
_SUMMARY = re.compile(r"\b(?:summari[sz]e|summary|condense|abridge)\b", _FLAGS)
_LABEL = re.compile(
    r"\b(?:classify|classification|categori[sz]e|label)\b"
    r"|\b(?:choose|select|return)\s+(?:exactly\s+)?one\s+(?:of|label)\b",
    _FLAGS,
)
_REASON = re.compile(
    r"\b(?:reason|reasoning|deduce|derive|prove|solve|calculate|why|compare)\b"
    r"|\bstep[ -]by[ -]step\b",
    _FLAGS,
)


def classify_task(
    payload: SanitizedPayload,
    task_hint: TaskCategory | None = None,
    constraints: ResponseConstraints | None = None,
) -> TaskClassification:
    """Hint → explicit shape → code → extraction → summary → labels → reasoning → QA.

    These are transparent category heuristics. No text or tokens are returned,
    and the classification never grants permission to use a provider.
    """
    try:
        if not isinstance(payload, SanitizedPayload) or not payload.is_trusted:
            raise ClassificationError("Trusted sanitized input is required")
        SanitizedPayload.model_validate(payload.model_dump())
        if task_hint is not None and not isinstance(task_hint, TaskCategory):
            raise ClassificationError("Invalid task hint")
        if constraints is not None and not isinstance(constraints, ResponseConstraints):
            raise ClassificationError("Invalid response constraints")
        spec = ResponseConstraints.model_validate(
            ResponseConstraints().model_dump()
            if constraints is None
            else constraints.model_dump(warnings=False)
        )
    except (AttributeError, TypeError, ValueError):
        raise ClassificationError("Invalid sanitized classification input") from None
    if task_hint is not None:
        return TaskClassification(task=task_hint, reason=ClassificationReason.USER_HINT)
    if spec.required_fields:
        return TaskClassification(
            task=TaskCategory.EXTRACT, reason=ClassificationReason.REQUIRED_FIELDS
        )
    if (
        spec.requires_json
        or spec.min_bullets is not None
        or spec.max_bullets is not None
        or spec.min_chars > 1
        or spec.max_chars < MAX_RESPONSE_CHARS
        or _FORMAT.search(payload.text)
    ):
        return TaskClassification(
            task=TaskCategory.FORMAT, reason=ClassificationReason.SHAPE_CONSTRAINT
        )
    if _CODE.search(payload.text):
        return TaskClassification(task=TaskCategory.CODE, reason=ClassificationReason.CODE_CUE)
    if _EXTRACT.search(payload.text):
        return TaskClassification(
            task=TaskCategory.EXTRACT, reason=ClassificationReason.EXTRACTION_CUE
        )
    if _SUMMARY.search(payload.text):
        return TaskClassification(
            task=TaskCategory.SUMMARISE, reason=ClassificationReason.SUMMARY_CUE
        )
    if spec.allowed_labels or _LABEL.search(payload.text):
        return TaskClassification(task=TaskCategory.CLASSIFY, reason=ClassificationReason.LABEL_CUE)
    if _REASON.search(payload.text):
        return TaskClassification(
            task=TaskCategory.REASON, reason=ClassificationReason.REASONING_CUE
        )
    return TaskClassification(task=TaskCategory.QA, reason=ClassificationReason.QA_FALLBACK)
