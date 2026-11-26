"""Opt-in saved chat history: gating, bounds and deletion that actually purges."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from backend.api.main import create_app
from backend.storage.connection import LocalDatabase
from backend.storage.conversations import (
    CONVERSATION_LIMIT,
    ConversationStore,
    SavedMessage,
    derive_title,
)

pytestmark = pytest.mark.privacy

TOKEN = "synthetic-local-write-" + "x" * 40
ENV = {"MOCK_MODE": "1", "LOCAL_WRITE_TOKEN": TOKEN}
SECRET = "invented-private-marker"


def _store(tmp_path: Path) -> ConversationStore:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    return ConversationStore(database)


def _pair(text: str = "hello") -> tuple[SavedMessage, ...]:
    return (
        SavedMessage(role="user", content=text),
        SavedMessage(role="assistant", content="an answer", model_id="mock-general"),
    )


@asynccontextmanager
async def _client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    app = create_app(environment=ENV, db=database)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
            base_url="http://127.0.0.1:8000",
            headers={"X-Local-Token": TOKEN},
        ) as http,
    ):
        yield http


async def _enable(http: httpx.AsyncClient, value: bool) -> None:
    response = await http.put("/api/preferences", json={"workload_persistence_enabled": value})
    assert response.status_code == 200


def test_round_trip_preserves_order_and_roles(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = uuid4()
    store.save(identity, _pair("first question"))
    assert [item.role for item in store.messages(identity)] == ["user", "assistant"]
    assert store.messages(identity)[0].content == "first question"
    assert store.list()[0].title == "first question"


def test_saving_again_replaces_rather_than_appends(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = uuid4()
    store.save(identity, _pair())
    store.save(identity, (*_pair(), SavedMessage(role="user", content="third")))
    assert len(store.messages(identity)) == 3
    assert store.count() == 1


def test_delete_removes_the_message_text_not_only_the_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = uuid4()
    store.save(identity, _pair(SECRET))
    assert store.delete(identity) == 1
    assert store.messages(identity) == ()
    assert store.count() == 0


def test_delete_all_purges_every_conversation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for _ in range(3):
        store.save(uuid4(), _pair())
    assert store.delete_all() == 3
    assert store.count() == 0


def test_oldest_conversations_are_pruned_at_the_bound(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(CONVERSATION_LIMIT + 5):
        store.save(uuid4(), _pair(f"conversation {index}"))
    assert store.count() == CONVERSATION_LIMIT


@pytest.mark.parametrize(
    ("text", "expected"),
    [("hello", "hello"), ("  ", "New conversation"), ("a\nb", "a"), ("x" * 200, "x" * 79 + "…")],
)
def test_title_is_derived_and_bounded(text: str, expected: str) -> None:
    assert derive_title(text) == expected


def test_empty_or_oversized_conversations_are_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.save(uuid4(), ())
    with pytest.raises(ValueError):
        store.save(uuid4(), tuple(SavedMessage(role="user", content="x") for _ in range(501)))


def test_nothing_is_written_while_the_preference_is_off(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as http:
            listed = await http.get("/api/conversations")
            assert listed.json() == {"enabled": False, "conversations": []}
            saved = await http.put(
                "/api/conversations",
                json={"id": str(uuid4()), "messages": [{"role": "user", "content": SECRET}]},
            )
            assert saved.status_code == 200
            assert saved.json()["enabled"] is False
            assert saved.json()["conversations"] == []

    asyncio.run(scenario())


def test_saving_works_once_the_owner_opts_in(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as http:
            await _enable(http, True)
            identity = str(uuid4())
            saved = await http.put(
                "/api/conversations",
                json={
                    "id": identity,
                    "messages": [
                        {"role": "user", "content": "a question"},
                        {"role": "assistant", "content": "an answer"},
                    ],
                },
            )
            assert saved.json()["enabled"] is True
            assert [row["id"] for row in saved.json()["conversations"]] == [identity]
            detail = await http.get(f"/api/conversations/{identity}")
            assert [m["content"] for m in detail.json()["messages"]] == ["a question", "an answer"]

    asyncio.run(scenario())


def test_opting_out_purges_conversations_already_stored(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as http:
            await _enable(http, True)
            identity = str(uuid4())
            await http.put(
                "/api/conversations",
                json={"id": identity, "messages": [{"role": "user", "content": SECRET}]},
            )
            await _enable(http, False)
            await _enable(http, True)
            # Re-enabling must not resurrect anything that consent covered before.
            listed = await http.get("/api/conversations")
            assert listed.json()["conversations"] == []
            detail = await http.get(f"/api/conversations/{identity}")
            assert detail.json()["messages"] == []

    asyncio.run(scenario())


def test_deletion_is_honoured_even_after_opting_out(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as http:
            await _enable(http, True)
            identity = str(uuid4())
            await http.put(
                "/api/conversations",
                json={"id": identity, "messages": [{"role": "user", "content": "x"}]},
            )
            await _enable(http, False)
            response = await http.delete(f"/api/conversations/{identity}")
            assert response.status_code == 200

    asyncio.run(scenario())


def test_writes_require_the_local_token(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as http:
            await _enable(http, True)
            for method, url in (
                ("PUT", "/api/conversations"),
                ("DELETE", f"/api/conversations/{uuid4()}"),
                ("DELETE", "/api/conversations"),
            ):
                response = await http.request(
                    method,
                    url,
                    headers={"X-Local-Token": ""},
                    json={"id": str(uuid4()), "messages": [{"role": "user", "content": "x"}]}
                    if method == "PUT"
                    else None,
                )
                assert response.status_code == 403, url

    asyncio.run(scenario())


def test_saved_text_never_reaches_telemetry(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as http:
            await _enable(http, True)
            await http.put(
                "/api/conversations",
                json={"id": str(uuid4()), "messages": [{"role": "user", "content": SECRET}]},
            )
            telemetry = await http.get("/api/telemetry")
            assert SECRET not in telemetry.text

    asyncio.run(scenario())


def test_unknown_conversation_reads_empty_rather_than_erroring(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.messages(UUID(int=0)) == ()
    assert store.delete(UUID(int=0)) == 0
