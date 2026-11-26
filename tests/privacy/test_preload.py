"""Installed library integration with a blank test pipeline, never a trained-model claim."""

import importlib
import importlib.metadata
import importlib.util
from typing import Any
from uuid import uuid4

import pytest

from backend.privacy.analyzer import PresidioAnalyzer
from backend.privacy.sanitizer import Sanitizer

pytestmark = pytest.mark.privacy


def test_preload_uses_injected_local_pipeline_without_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spacy = importlib.import_module("spacy")
    nlp_module = importlib.import_module("presidio_analyzer.nlp_engine")
    requests = importlib.import_module("requests")
    pipeline = spacy.blank("en")
    original_find = importlib.util.find_spec
    original_version = importlib.metadata.version
    calls: list[str] = []

    def find(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "en_core_web_sm":
            return object()  # Simulates presence only; the blank pipeline is a test fixture.
        return original_find(name, *args, **kwargs)

    def load(name: str) -> Any:
        assert name == "en_core_web_sm"
        return pipeline

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append("forbidden")
        raise AssertionError("Asset loader or outbound HTTP must not run")

    def version(name: str) -> str:
        return "blank-test-pipeline" if name == "en_core_web_sm" else original_version(name)

    monkeypatch.setattr(importlib.util, "find_spec", find)
    monkeypatch.setattr(importlib.metadata, "version", version)
    monkeypatch.setattr(spacy, "load", load)
    monkeypatch.setattr(nlp_module.SpacyNlpEngine, "load", forbidden)
    monkeypatch.setattr(requests.Session, "send", forbidden)
    analyzer = PresidioAnalyzer.preload()
    result = Sanitizer(analyzer).sanitize(
        uuid4(), "Synthetic email invented.person@example.invalid and ID STU-1234-5678"
    )
    assert result.payload.is_trusted
    assert "invented.person@example.invalid" not in result.payload.text
    assert "STU-1234-5678" not in result.payload.text
    assert "blank-test-pipeline" in analyzer.version
    assert not calls


# These need the real trained pipeline, since the whole point is NER precision.
REAL_NLP = pytest.mark.slow

ORDINARY_PROMPTS = (
    "Generate an essay on world war 2",
    "Write a Python function that reverses a list",
    "Explain how photosynthesis works",
    "Who was Winston Churchill?",
    "What is the capital of France?",
)
REAL_IDENTIFIERS = (
    ("Contact invented.person@example.invalid", "EMAIL_ADDRESS"),
    ("My card is 4111 1111 1111 1111", "CREDIT_CARD"),
    ("My social security number is 536-22-4701", "US_SSN"),
    ("My id is STU-1234-5678", "SYNTHETIC_STUDENT_ID"),
)


@REAL_NLP
@pytest.mark.parametrize("prompt", ORDINARY_PROMPTS)
def test_ordinary_instructions_are_not_masked(prompt: str) -> None:
    # A false positive does more than add noise: it hides words the model needs,
    # and any placeholder at all arms the output inspector, which then withholds
    # the answer for naming public figures. "Generate" was read as a PERSON.
    result = Sanitizer(PresidioAnalyzer.preload()).sanitize(uuid4(), prompt)
    assert result.placeholders.entry_count == 0
    assert result.payload.text == prompt


@REAL_NLP
@pytest.mark.parametrize(("prompt", "expected"), REAL_IDENTIFIERS)
def test_real_identifiers_are_still_masked(prompt: str, expected: str) -> None:
    result = Sanitizer(PresidioAnalyzer.preload()).sanitize(uuid4(), prompt)
    assert expected in {count.entity_type for count in result.payload.privacy_counts}


@REAL_NLP
@pytest.mark.parametrize("entity", ["CREDIT_CARD", "US_SSN"])
def test_hard_blocked_types_remain_detectable(entity: str) -> None:
    # Cloud hard-blocks key on these exact labels, so losing either label would
    # silently drop the block rather than fail loudly.
    from backend.privacy.analyzer import DETECTED_ENTITIES

    assert entity in DETECTED_ENTITIES
