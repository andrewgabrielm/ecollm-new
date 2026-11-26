"""/api/cloud routes — list, set and clear user-supplied cloud API keys."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from backend.api.main import create_app
from backend.broker.orchestrator import ChatOrchestrator, build_mock_broker
from backend.config.loader import CloudDestinationConfig
from backend.providers.cloud_openai_compat import CloudDestination
from backend.storage.connection import LocalDatabase

pytestmark = pytest.mark.privacy

TOKEN = "synthetic-local-write-" + "x" * 40
ENV = {"MOCK_MODE": "1", "LOCAL_WRITE_TOKEN": TOKEN}


def _destination(model_id: str = "grok-cloud") -> CloudDestination:
    return CloudDestination.model_validate(
        {
            "endpoint": "https://api.x.ai/v1/chat/completions",
            "model_id": model_id,
            "model_version": "grok-4-fast-2026-11",
            "runtime_model": "grok-4-fast-reasoning",
            "display_name": "xAI Grok",
        }
    )


@asynccontextmanager
async def _client(
    tmp_path: Path, destinations: tuple[CloudDestination, ...] = ()
) -> AsyncIterator[tuple[ChatOrchestrator, httpx.AsyncClient]]:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    broker = build_mock_broker(cloud_destinations=destinations)
    app = create_app(broker=broker, environment=ENV, db=database)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
            base_url="http://127.0.0.1:8000",
            headers={"X-Local-Token": TOKEN},
        ) as http,
    ):
        yield broker, http


def test_no_destinations_by_default(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (_, http):
            response = await http.get("/api/cloud")
            assert response.status_code == 200
            assert response.json() == {"destinations": []}

    asyncio.run(scenario())


def test_declared_destination_listed_with_no_key(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination(),)) as (_, http):
            body = (await http.get("/api/cloud")).json()
            assert len(body["destinations"]) == 1
            assert body["destinations"][0]["key_available"] is False
            assert body["destinations"][0]["key_source"] == "none"
            assert body["destinations"][0]["display_name"] == "xAI Grok"

    asyncio.run(scenario())


def test_set_and_clear_key_flips_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination(),)) as (_, http):
            put = await http.put(
                "/api/cloud/grok-cloud/key",
                json={"api_key": "xai-synthetic-test-key"},
            )
            assert put.status_code == 200
            body = put.json()
            assert body["destinations"][0]["key_available"] is True
            assert body["destinations"][0]["key_source"] == "runtime"
            # Server does not echo the key back under any field.
            raw = put.text
            assert "xai-synthetic-test-key" not in raw

            clear = await http.delete("/api/cloud/grok-cloud/key")
            assert clear.status_code == 200
            body = clear.json()
            assert body["destinations"][0]["key_available"] is False
            assert body["destinations"][0]["key_source"] == "none"

    asyncio.run(scenario())


def test_set_key_requires_write_token(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination(),)) as (_, http):
            response = await http.put(
                "/api/cloud/grok-cloud/key",
                json={"api_key": "xai-synthetic-test-key"},
                headers={"X-Local-Token": ""},
            )
            assert response.status_code == 403

    asyncio.run(scenario())


def test_malformed_key_falls_back_to_current_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination(),)) as (broker, http):
            response = await http.put(
                "/api/cloud/grok-cloud/key",
                json={"api_key": "contains space"},
            )
            assert response.status_code == 200  # route stays non-leaky
            provider = broker.cloud_providers()["grok-cloud"]
            assert provider.describe("grok-cloud").key_available is False

    asyncio.run(scenario())


def test_empty_api_key_rejected_by_schema(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination(),)) as (_, http):
            response = await http.put(
                "/api/cloud/grok-cloud/key",
                json={"api_key": ""},
            )
            assert response.status_code == 422

    asyncio.run(scenario())


def test_unknown_destination_returns_current_list(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination(),)) as (_, http):
            response = await http.put(
                "/api/cloud/unknown-cloud/key",
                json={"api_key": "xai-key"},
            )
            assert response.status_code == 200
            ids = [row["model_id"] for row in response.json()["destinations"]]
            assert ids == ["grok-cloud"]

    asyncio.run(scenario())


def test_key_never_in_telemetry_or_logs(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination(),)) as (broker, http):
            await http.put(
                "/api/cloud/grok-cloud/key",
                json={"api_key": "xai-ultra-secret-value"},
            )
            # The repr/model_dump of the provider must not expose the secret.
            provider = broker.cloud_providers()["grok-cloud"]
            assert "xai-ultra-secret-value" not in repr(provider.destination)
            # SecretStr → str yields '**********', not the value.
            assert "xai-ultra-secret-value" not in provider.destination.model_dump_json()

    asyncio.run(scenario())


def test_active_destination_follows_wherever_a_key_was_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Declaring several providers must not strand the fallback on the first one:
    # the user keys whichever provider they have an account with.
    async def scenario() -> None:
        first, second, third = (
            _destination("alpha-cloud"),
            _destination("beta-cloud"),
            _destination("gamma-cloud"),
        )
        async with _client(tmp_path, destinations=(first, second, third)) as (broker, http):
            # The mock broker always ships a keyed mock-cloud ahead of these.
            monkeypatch.setattr(broker.dispatcher._providers["mock-cloud"], "_key_available", False)
            await http.put("/api/cloud/gamma-cloud/key", json={"api_key": "synthetic-gamma-key"})
            assert broker._cloud_model_id == "gamma-cloud"
            # An earlier-declared provider outranks a later one once it is keyed.
            await http.put("/api/cloud/beta-cloud/key", json={"api_key": "synthetic-beta-key"})
            assert broker._cloud_model_id == "beta-cloud"
            # Clearing it falls back to the remaining keyed provider.
            await http.delete("/api/cloud/beta-cloud/key")
            assert broker._cloud_model_id == "gamma-cloud"

    asyncio.run(scenario())


def test_unkeyed_destinations_still_report_a_target_rather_than_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With no key anywhere the request must fail as an unusable destination,
    # not as though no cloud were configured at all.
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination("alpha-cloud"),)) as (broker, _):
            monkeypatch.setattr(broker.dispatcher._providers["mock-cloud"], "_key_available", False)
            assert broker._cloud_model_id == "mock-cloud"

    asyncio.run(scenario())


def test_model_name_is_supplied_at_runtime_not_preconfigured(tmp_path: Path) -> None:
    # A pinned model name goes stale and 404s, so the destination ships without
    # one and stays unusable until the owner names the model.
    async def scenario() -> None:
        destination = CloudDestination.model_validate(
            {
                "endpoint": "https://api.x.ai/v1/chat/completions",
                "model_id": "grok-cloud",
                "model_version": "xai-v1",
                "display_name": "xAI Grok",
            }
        )
        async with _client(tmp_path, destinations=(destination,)) as (broker, http):
            listed = await http.get("/api/cloud")
            row = next(
                item for item in listed.json()["destinations"] if item["model_id"] == "grok-cloud"
            )
            assert row["runtime_model"] is None
            response = await http.put(
                "/api/cloud/grok-cloud/model", json={"runtime_model": "grok-4.7"}
            )
            assert response.status_code == 200
            saved = next(
                item for item in response.json()["destinations"] if item["model_id"] == "grok-cloud"
            )
            assert saved["runtime_model"] == "grok-4.7"
            assert broker.cloud_providers()["grok-cloud"].runtime_model == "grok-4.7"

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["", "has space", "x" * 129, "bad|char"])
def test_malformed_model_names_are_rejected(tmp_path: Path, name: str) -> None:
    async def scenario() -> None:
        destination = CloudDestination.model_validate(
            {
                "endpoint": "https://api.x.ai/v1/chat/completions",
                "model_id": "grok-cloud",
                "model_version": "xai-v1",
                "display_name": "xAI Grok",
            }
        )
        async with _client(tmp_path, destinations=(destination,)) as (broker, http):
            await http.put("/api/cloud/grok-cloud/model", json={"runtime_model": name})
            assert broker.cloud_providers()["grok-cloud"].runtime_model is None

    asyncio.run(scenario())


def test_models_listing_is_empty_without_a_key(tmp_path: Path) -> None:
    # Listing spends the credential, so there is nothing to ask without one.
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination(),)) as (_, http):
            response = await http.get("/api/cloud/grok-cloud/models")
            body = response.json()
            assert body["models"] == [] and body["reachable"] is False

    asyncio.run(scenario())


def test_owner_can_add_and_remove_a_destination(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (broker, http):
            added = await http.post(
                "/api/cloud",
                json={
                    "model_id": "my-host",
                    "display_name": "My Host",
                    "endpoint": "https://api.example.com/v1/chat/completions",
                    "health_endpoint": "https://api.example.com/v1/models",
                },
            )
            assert "my-host" in [row["model_id"] for row in added.json()["destinations"]]
            assert "my-host" in broker.cloud_providers()
            removed = await http.delete("/api/cloud/my-host")
            assert "my-host" not in [row["model_id"] for row in removed.json()["destinations"]]
            assert "my-host" not in broker.cloud_providers()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.example.com/v1/chat/completions",  # not https
        "https://127.0.0.1/v1/chat/completions",  # loopback literal
        "https://169.254.169.254/v1/chat/completions",  # link-local metadata
        "https://10.0.0.5/v1/chat/completions",  # private range
        "https://api.example.com",  # no completion path
        "ftp://api.example.com/v1/chat/completions",
    ],
)
def test_untrusted_endpoints_are_refused(tmp_path: Path, endpoint: str) -> None:
    # The UI can name a destination, so it gets the same validation the packaged
    # entries get; a rejected endpoint must not become a place text can go.
    async def scenario() -> None:
        async with _client(tmp_path) as (broker, http):
            await http.post(
                "/api/cloud",
                json={
                    "model_id": "untrusted",
                    "display_name": "Untrusted",
                    "endpoint": endpoint,
                    "health_endpoint": None,
                },
            )
            assert "untrusted" not in broker.cloud_providers()

    asyncio.run(scenario())


def test_adding_a_destination_requires_the_local_token(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (broker, http):
            response = await http.post(
                "/api/cloud",
                headers={"X-Local-Token": ""},
                json={
                    "model_id": "sneaky",
                    "display_name": "Sneaky",
                    "endpoint": "https://api.example.com/v1/chat/completions",
                    "health_endpoint": None,
                },
            )
            assert response.status_code == 403
            assert "sneaky" not in broker.cloud_providers()

    asyncio.run(scenario())


def test_a_packaged_destination_cannot_be_removed(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path, destinations=(_destination(),)) as (broker, http):
            await http.delete("/api/cloud/grok-cloud")
            assert "grok-cloud" in broker.cloud_providers()

    asyncio.run(scenario())


def test_presets_are_offered_and_exclude_already_registered_providers(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (_, http):
            first = await http.get("/api/cloud/presets")
            offered = {row["model_id"] for row in first.json()["presets"]}
            assert {"groq", "openai", "gemini", "claude", "deepseek", "xai"} <= offered
            chosen = next(r for r in first.json()["presets"] if r["model_id"] == "groq")
            await http.post(
                "/api/cloud",
                json={
                    "model_id": chosen["model_id"],
                    "display_name": chosen["display_name"],
                    "endpoint": chosen["endpoint"],
                    "health_endpoint": chosen["health_endpoint"],
                },
            )
            # A registered provider must not be offered again; POST would refuse it.
            again = await http.get("/api/cloud/presets")
            assert "groq" not in {row["model_id"] for row in again.json()["presets"]}

    asyncio.run(scenario())


def test_every_preset_endpoint_passes_destination_validation(tmp_path: Path) -> None:
    # A preset must not be able to introduce a destination the owner-added path
    # would reject: same https and DNS-hostname rules apply to both.
    from backend.config.cloud_presets import CLOUD_PRESETS
    from backend.storage.cloud_destinations import CloudDestinationStore

    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    store = CloudDestinationStore(database)
    for preset in CLOUD_PRESETS:
        store.add(
            CloudDestinationConfig.model_validate(
                {
                    "model_id": preset.model_id,
                    "display_name": preset.display_name,
                    "endpoint": preset.endpoint,
                    "health_endpoint": preset.health_endpoint,
                    "model_version": "owner-v1",
                }
            )
        )
    assert len(store.list()) == len(CLOUD_PRESETS)
