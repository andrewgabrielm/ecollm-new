"""Step 15 telemetry API integration — mock chat flow, no real inference."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from backend.api.main import create_app
from backend.broker.orchestrator import ChatOrchestrator
from backend.profiles.preferences import PreferenceProfile
from backend.storage.connection import LocalDatabase

pytestmark = pytest.mark.privacy

TOKEN = "synthetic-local-write-" + "x" * 40
ENV = {"MOCK_MODE": "1", "LOCAL_WRITE_TOKEN": TOKEN}


@asynccontextmanager
async def _client(
    tmp_path: Path,
) -> AsyncIterator[tuple[ChatOrchestrator, httpx.AsyncClient]]:
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
        broker = app.state.broker
        assert isinstance(broker, ChatOrchestrator)
        yield broker, http


async def _send_chat(http: httpx.AsyncClient, prompt: str) -> None:
    response = await http.post(
        "/api/chat",
        json={"prompt": prompt, "mode": "local_only"},
    )
    assert response.status_code == 200, response.text


async def _enable_analytics(http: httpx.AsyncClient) -> None:
    response = await http.put(
        "/api/preferences",
        json={"persistent_analytics_enabled": True},
    )
    assert response.status_code == 200, response.text


@pytest.mark.anyio
async def test_telemetry_disabled_by_default(tmp_path: Path) -> None:
    async with _client(tmp_path) as (_, http):
        await _send_chat(http, "A simple factual question.")
        response = await http.get("/api/telemetry")
        body = response.json()
        assert body["enabled"] is False
        assert body["sample_count"] == 0


@pytest.mark.anyio
async def test_telemetry_records_after_consent(tmp_path: Path) -> None:
    async with _client(tmp_path) as (broker, http):
        broker.update_preferences(PreferenceProfile(persistent_analytics_enabled=True))
        await _send_chat(http, "A simple factual question.")
        await _send_chat(http, "Another synthetic prompt about geography.")
        response = await http.get("/api/telemetry")
        body = response.json()
        assert body["enabled"] is True
        assert body["sample_count"] == 2
        assert sum(body["route_counts"].values()) == 2
        assert body["cache"]["total_records"] == 2


@pytest.mark.anyio
async def test_telemetry_delete_requires_local_token(tmp_path: Path) -> None:
    async with _client(tmp_path) as (broker, http):
        broker.update_preferences(PreferenceProfile(persistent_analytics_enabled=True))
        await _send_chat(http, "A simple factual question.")
        unauthorized = await http.delete("/api/telemetry", headers={"X-Local-Token": ""})
        assert unauthorized.status_code == 403
        cleared = await http.delete("/api/telemetry")
        assert cleared.status_code == 200
        summary = (await http.get("/api/telemetry")).json()
        assert summary["sample_count"] == 0


@pytest.mark.anyio
async def test_prompt_text_never_in_summary(tmp_path: Path) -> None:
    secret_prompt = "SECRET MARKER do not surface this text"
    async with _client(tmp_path) as (broker, http):
        broker.update_preferences(PreferenceProfile(persistent_analytics_enabled=True))
        await _send_chat(http, secret_prompt)
        response = await http.get("/api/telemetry")
        assert "SECRET MARKER" not in response.text
