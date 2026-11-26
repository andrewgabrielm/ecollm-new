"""Invented identifiers used in privacy fixtures; no real jurisdictional ID claim."""

import importlib
import re
from typing import Any

from backend.privacy.analyzer import DetectionSpan

SYNTHETIC_PATTERNS = (
    ("SYNTHETIC_STUDENT_ID", r"\bSTU-[0-9]{4}-[0-9]{4}\b"),
    ("SYNTHETIC_EMPLOYEE_ID", r"\bEMP-[0-9]{4}-[0-9]{4}\b"),
)


def synthetic_spans(text: str) -> tuple[DetectionSpan, ...]:
    return tuple(
        DetectionSpan(entity_type, match.start(), match.end(), 1.0)
        for entity_type, pattern in SYNTHETIC_PATTERNS
        for match in re.finditer(pattern, text)
    )


def presidio_recognizers() -> tuple[Any, ...]:
    """Construct custom recognizers only during explicit analyzer preload."""
    presidio = importlib.import_module("presidio_analyzer")
    return tuple(
        presidio.PatternRecognizer(
            supported_entity=entity_type,
            patterns=[presidio.Pattern(name=entity_type, regex=pattern, score=1.0)],
            supported_language="en",
        )
        for entity_type, pattern in SYNTHETIC_PATTERNS
    )


def offline_email_recognizer() -> Any:
    """Keep Presidio email patterns, validating with bundled suffix data only.

    Unknown suffixes remain sensitive-looking matches rather than becoming a
    false privacy exclusion (including the invented ``example.invalid`` fixture).
    No disk cache or module-level download-capable extractor is used.
    """
    predefined = importlib.import_module("presidio_analyzer.predefined_recognizers")
    tldextract = importlib.import_module("tldextract")
    extractor = tldextract.TLDExtract(
        cache_dir=None, suffix_list_urls=(), fallback_to_snapshot=True
    )
    recognizer = predefined.EmailRecognizer()

    def validate_email(pattern_text: str) -> bool:
        result = extractor(pattern_text)
        return bool(result.domain)

    recognizer.validate_result = validate_email
    return recognizer
