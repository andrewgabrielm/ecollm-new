"""Inspect masked output before request-local restoration or cache eligibility."""

from dataclasses import dataclass, field
from uuid import UUID

from backend.privacy.analyzer import Analyzer, resolve_overlaps
from backend.privacy.placeholder_map import PlaceholderMap

MASKED_WARNING = "[Response withheld: privacy inspection could not confirm safe output.]"


@dataclass(frozen=True, slots=True)
class InspectionResult:
    answer: str = field(repr=False)
    restored: bool = False
    cache_eligible: bool = False
    warning: bool = False


class OutputInspector:
    def __init__(self, analyzer: Analyzer) -> None:
        self._analyzer = analyzer

    def inspect(
        self, request_id: UUID, text: str, placeholders: PlaceholderMap
    ) -> InspectionResult:
        try:
            if not isinstance(text, str) or not text.strip() or len(text) > 64000:
                return InspectionResult(MASKED_WARNING, warning=True)
            masked = placeholders.text_for_scan(request_id, text)
            # Only scan for leaked PII when placeholders were substituted in the
            # input.  When entry_count == 0 the model never saw any PII, so
            # generic entities (dates, locations, names) in its response are not
            # leaks and must not be blocked.
            if placeholders.entry_count > 0 and resolve_overlaps(
                masked, self._analyzer.analyze(masked)
            ):
                return InspectionResult(MASKED_WARNING, warning=True)
            answer, restored = placeholders.restore(request_id, text)
            return InspectionResult(
                answer, restored=restored, cache_eligible=placeholders.entry_count == 0
            )
        except Exception:
            return InspectionResult(MASKED_WARNING, warning=True)
