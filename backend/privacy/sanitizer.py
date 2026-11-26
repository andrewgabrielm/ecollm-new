"""Fail-closed sanitization; payloads are constructed only after successful masking."""

from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from backend.api.schemas import COMPOSED_CHARACTER_LIMIT, PrivacyTypeCount
from backend.privacy.analyzer import Analyzer, DetectionSpan, PrivacyError, resolve_overlaps
from backend.privacy.placeholder_map import RESERVED_PLACEHOLDER, PlaceholderMap
from backend.privacy.sanitized_payload import (
    SanitizationProvenance,
    SanitizedPayload,
    _issue_sanitized_payload,
)

SANITIZER_VERSION = "sanitizer-v2"


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    payload: SanitizedPayload = field(repr=False)
    placeholders: PlaceholderMap = field(repr=False)
    type_counts: tuple[PrivacyTypeCount, ...]


class Sanitizer:
    def __init__(self, analyzer: Analyzer) -> None:
        self._analyzer = analyzer

    def sanitize(self, request_id: UUID, text: str) -> SanitizationResult:
        placeholders = PlaceholderMap(request_id)
        try:
            # Bounds the composed transcript, not the user's prompt: the caller
            # masks the dated header, prior turns and the new message as one
            # text, so an 8000-char cap here rejected every multi-turn request
            # and any single prompt at the advertised limit.
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text) > COMPOSED_CHARACTER_LIMIT
            ):
                raise PrivacyError("Invalid sanitizer input")
            original_detections = self._analyzer.analyze(text)
            detected = resolve_overlaps(text, original_detections)
            # Preserve every detected type for hard-block decisions even when a
            # higher-confidence overlapping label owns the replacement union.
            distinct_detections = {
                (span.entity_type, span.start, span.end) for span in original_detections
            }
            counts = Counter(entity_type for entity_type, _, _ in distinct_detections)
            literal_spans = tuple(
                DetectionSpan("LITERAL", match.start(), match.end(), 0.0)
                for match in RESERVED_PLACEHOLDER.finditer(text)
            )
            replacements = resolve_overlaps(text, (*detected, *literal_spans))
            parts: list[str] = []
            cursor = 0
            for span in replacements:
                parts.append(text[cursor : span.start])
                parts.append(placeholders.add(span.entity_type, text[span.start : span.end]))
                cursor = span.end
            parts.append(text[cursor:])
            sanitized = "".join(parts)
            provenance = SanitizationProvenance(
                sanitizer_version=SANITIZER_VERSION, analyzer_version=self._analyzer.version
            )
            type_counts = tuple(
                PrivacyTypeCount(entity_type=kind, count=count)
                for kind, count in sorted(counts.items())
            )
            payload = _issue_sanitized_payload(request_id, sanitized, provenance, type_counts)
            return SanitizationResult(
                payload,
                placeholders,
                type_counts,
            )
        except Exception:
            placeholders.clear()
            raise PrivacyError("Privacy sanitization failed") from None
