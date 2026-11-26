"""Conversation composition: history is bounded, dated and masked with the prompt."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.api.schemas import (
    COMPOSED_CHARACTER_LIMIT,
    HISTORY_CHARACTER_BUDGET,
    HISTORY_TURN_LIMIT,
    ChatRequest,
    ConversationTurn,
)
from backend.broker.chat import compose_prompt
from backend.contracts import TaskCategory

FIXED = datetime(2026, 11, 25, 14, 30, tzinfo=UTC)
PRIVATE = "STU-1234-5678"


def turn(role: Literal["user", "assistant"], content: str) -> ConversationTurn:
    return ConversationTurn(role=role, content=content)


def test_single_turn_carries_the_clock_without_dialogue_framing() -> None:
    text = compose_prompt(ChatRequest(prompt="What is the capital of France?"), now=FIXED)
    assert "Wednesday, 25 November 2026, 14:30" in text
    assert text.endswith("What is the capital of France?")
    assert "Conversation so far:" not in text


def test_history_is_ordered_and_attributed_to_each_speaker() -> None:
    request = ChatRequest(
        prompt="What did I just ask?",
        history=(turn("user", "Who wrote Hamlet?"), turn("assistant", "Shakespeare did.")),
    )
    text = compose_prompt(request, now=FIXED)
    assert "Conversation so far:" in text
    assert text.index("User: Who wrote Hamlet?") < text.index("Assistant: Shakespeare did.")
    assert text.endswith("User: What did I just ask?")


def test_history_reaches_the_sanitizer_so_earlier_turns_are_masked_too() -> None:
    # A prior turn holding an identifier must not slip past masking just because
    # the current prompt is clean.
    request = ChatRequest(prompt="Summarise that.", history=(turn("user", f"My id is {PRIVATE}"),))
    assert PRIVATE in compose_prompt(request, now=FIXED)


def test_turn_count_above_the_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(
            prompt="hello",
            history=tuple(turn("user", "hi") for _ in range(HISTORY_TURN_LIMIT + 1)),
        )


def test_total_history_characters_above_the_budget_are_rejected() -> None:
    oversized = "x" * 8000
    count = HISTORY_CHARACTER_BUDGET // 8000 + 1
    with pytest.raises(ValidationError):
        ChatRequest(prompt="hello", history=tuple(turn("user", oversized) for _ in range(count)))


def test_history_at_the_budget_is_accepted() -> None:
    each = "x" * (HISTORY_CHARACTER_BUDGET // 3)
    request = ChatRequest(prompt="hello", history=tuple(turn("user", each) for _ in range(3)))
    assert sum(len(item.content) for item in request.history) == HISTORY_CHARACTER_BUDGET


@pytest.mark.parametrize("role", ["system", "tool", "", "User"])
def test_unknown_roles_are_rejected(role: str) -> None:
    # Validated as a mapping because that is how an untrusted body arrives.
    with pytest.raises(ValidationError):
        ConversationTurn.model_validate({"role": role, "content": "hi"})


def test_blank_turn_content_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConversationTurn(role="user", content="")


def test_history_defaults_to_empty_so_existing_clients_are_unchanged() -> None:
    assert ChatRequest(prompt="hello").history == ()


def test_turn_content_is_redacted_from_repr() -> None:
    request = ChatRequest(prompt=f"hello {PRIVATE}", history=(turn("user", PRIVATE),))
    assert PRIVATE not in repr(request)


def test_budget_chain_fits_inside_the_local_context_window() -> None:
    # These four numbers were set independently and contradicted each other:
    # history ran to 24000 chars into a 2048-token window, so Ollama silently
    # dropped the start of every long conversation. Characters convert at a
    # pessimistic 3 per token.
    from backend.api.schemas import DEFAULT_MAX_OUTPUT_TOKENS
    from backend.broker.local_runtime import REAL_CONTEXT_TOKENS

    prompt_tokens = COMPOSED_CHARACTER_LIMIT / 3
    assert prompt_tokens + DEFAULT_MAX_OUTPUT_TOKENS <= REAL_CONTEXT_TOKENS


def test_composed_limit_covers_a_full_prompt_with_full_history() -> None:
    history = tuple(turn("user", "x" * (HISTORY_CHARACTER_BUDGET // 4)) for _ in range(4))
    request = ChatRequest(prompt="y" * 8000, history=history)
    assert len(compose_prompt(request, now=FIXED)) <= COMPOSED_CHARACTER_LIMIT


CODE_HISTORY = (
    ConversationTurn(role="user", content="Write a python function to sort a list"),
    ConversationTurn(role="assistant", content="def sort_list(x): return sorted(x)"),
)
QUESTION = "What is the capital of France?"


def test_composed_transcript_alone_would_misclassify_a_later_question() -> None:
    # Pins the cause: the transcript carries the earlier code cue, so anything
    # classifying the composed text retags every later turn as code.
    from backend.broker.classifier import classify_task
    from backend.privacy.sanitizer import Sanitizer
    from tests.privacy.test_engine import FixtureAnalyzer

    san = Sanitizer(FixtureAnalyzer())
    composed = compose_prompt(ChatRequest(prompt=QUESTION, history=CODE_HISTORY), now=FIXED)
    assert classify_task(san.sanitize(uuid4(), composed).payload).task == TaskCategory.CODE
    assert classify_task(san.sanitize(uuid4(), QUESTION).payload).task == TaskCategory.QA


def test_routing_classifies_the_new_message_not_the_transcript() -> None:
    # End to end: a general question after a code turn must stay QA, while the
    # model still receives the earlier turns.
    import asyncio

    from backend.broker.orchestrator import build_mock_broker

    async def scenario() -> None:
        broker = build_mock_broker()
        try:
            result = await broker.chat(ChatRequest(prompt=QUESTION, history=CODE_HISTORY))
            assert result.status == "success"
            assert result.task == TaskCategory.QA
        finally:
            await broker.aclose()

    asyncio.run(scenario())


def test_history_makes_a_request_cache_ineligible() -> None:
    # The cache key is built from the prompt alone, so "summarise that" would
    # otherwise hit an entry stored for the same words in another conversation.
    from backend.cache.eligibility import cache_eligible_request
    from backend.contracts import SensitivityScope
    from backend.profiles.preferences import PreferenceProfile

    prefs = PreferenceProfile(exact_cache_enabled=True)
    scope = SensitivityScope.NON_SENSITIVE
    assert cache_eligible_request(prefs, scope, 0, has_history=False)
    assert not cache_eligible_request(prefs, scope, 0, has_history=True)


CLOUD_ONLY_PROMPT = "A synthetic cloud-only question"


def _cloud_only(**overrides: object) -> ChatRequest:
    from backend.contracts import ExecutionMode

    return ChatRequest.model_validate(
        {"prompt": CLOUD_ONLY_PROMPT, "mode": ExecutionMode.CLOUD_ONLY, **overrides}
    )


def test_cloud_only_skips_the_local_cascade_entirely() -> None:
    import asyncio

    from backend.broker.orchestrator import build_mock_broker

    async def scenario() -> None:
        broker = build_mock_broker()
        try:
            result = await broker.chat(_cloud_only())
            assert result.status == "success"
            assert result.route == "cloud"
            assert result.reason == "cloud_only"
            # No local model was asked, so none may record a call.
            assert broker.test_call_counts["mock-general"] == 0
            assert broker.test_call_counts["mock-specialist"] == 0
        finally:
            await broker.aclose()

    asyncio.run(scenario())


def test_cloud_only_still_refuses_a_hard_blocked_identifier() -> None:
    # Choosing cloud as the primary route is not consent to send a card number.
    import asyncio

    from backend.broker.orchestrator import build_mock_broker

    async def scenario() -> None:
        broker = build_mock_broker()
        try:
            result = await broker.chat(_cloud_only(prompt="My card is CARD-0000 please check"))
            assert result.status == "error"
            assert broker.test_call_counts["mock-cloud"] == 0
        finally:
            await broker.aclose()

    asyncio.run(scenario())


def test_cloud_only_reports_unavailable_when_no_destination_is_configured() -> None:
    # It must not silently answer locally, and must not blame a local failure
    # that never happened.
    import asyncio

    from backend.broker.orchestrator import build_mock_broker

    async def scenario() -> None:
        broker = build_mock_broker()
        broker.dispatcher.unregister("mock-cloud")
        try:
            result = await broker.chat(_cloud_only())
            assert result.status == "error"
            assert result.error_code.value == "unavailable"
            assert broker.test_call_counts["mock-general"] == 0
        finally:
            await broker.aclose()

    asyncio.run(scenario())
