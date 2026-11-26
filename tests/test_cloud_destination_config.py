"""Cloud destination config + build_real_broker wiring tests.

All checks are offline: no real HTTP, no real Ollama daemon, no real key.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.config.loader import (
    AppConfig,
    CloudDestinationConfig,
    ConfigError,
    load_settings,
    load_yaml,
)
from backend.providers.cloud_openai_compat import CloudDestination, CloudProvider


class TestCloudDestinationConfig:
    def test_valid_config_round_trips_to_cloud_destination(self) -> None:
        config = CloudDestinationConfig(
            endpoint="https://api.x.ai/v1/chat/completions",
            model_id="grok-cloud",
            model_version="grok-4-fast-2026-11",
            display_name="xAI Grok",
            runtime_model="grok-4-fast-reasoning",
            api_key_variable="XAI_API_KEY",
        )
        destination = CloudDestination.model_validate(config.model_dump())
        assert destination.endpoint == "https://api.x.ai/v1/chat/completions"
        assert destination.api_key_variable == "XAI_API_KEY"
        assert destination.display_name == "xAI Grok"

    def test_runtime_only_destination_omits_api_key_variable(self) -> None:
        config = CloudDestinationConfig(
            endpoint="https://api.x.ai/v1/chat/completions",
            model_id="grok-cloud",
            model_version="v1",
            runtime_model="grok-4-fast-reasoning",
        )
        destination = CloudDestination.model_validate(config.model_dump())
        assert destination.api_key_variable is None

    def test_rejects_non_https_endpoint(self) -> None:
        with pytest.raises(ValidationError):
            CloudDestination.model_validate(
                CloudDestinationConfig(
                    endpoint="http://api.x.ai/v1/chat/completions",
                    model_id="grok-cloud",
                    model_version="v1",
                    runtime_model="grok-4-fast-reasoning",
                ).model_dump()
            )

    def test_rejects_bare_host(self) -> None:
        with pytest.raises(ValidationError):
            CloudDestination.model_validate(
                CloudDestinationConfig(
                    endpoint="https://api.x.ai",
                    model_id="grok-cloud",
                    model_version="v1",
                    runtime_model="grok",
                ).model_dump()
            )

    def test_rejects_lowercase_api_key_variable(self) -> None:
        with pytest.raises(ValidationError):
            CloudDestinationConfig(
                endpoint="https://api.x.ai/v1/chat/completions",
                model_id="grok-cloud",
                model_version="v1",
                runtime_model="grok",
                api_key_variable="xai_api_key",
            )

    def test_rejects_invalid_model_id(self) -> None:
        with pytest.raises(ValidationError):
            CloudDestinationConfig(
                endpoint="https://api.x.ai/v1/chat/completions",
                model_id="Grok Cloud!",
                model_version="v1",
                runtime_model="grok",
            )


class TestSettingsIntegration:
    def test_shipped_destinations_cannot_egress_without_an_owner_supplied_key(self) -> None:
        # Declaring a destination must not by itself enable egress. Every shipped
        # entry omits api_key_variable, so no key exists until the owner pastes
        # one into the running process.
        settings = load_settings(environment={"MOCK_MODE": "1"})
        assert all(item.api_key_variable is None for item in settings.cloud_destinations)

    def test_yaml_with_multiple_destinations_round_trip(self, tmp_path: Path) -> None:
        config_text = (
            "schema_version: settings-v1\n"
            "mock_mode: true\n"
            "cloud_destinations:\n"
            "  - endpoint: https://api.x.ai/v1/chat/completions\n"
            "    model_id: grok-cloud\n"
            "    model_version: grok-4-fast-2026-11\n"
            "    display_name: xAI Grok\n"
            "    runtime_model: grok-4-fast-reasoning\n"
            "    api_key_variable: XAI_API_KEY\n"
            "  - endpoint: https://api.openai.com/v1/chat/completions\n"
            "    model_id: openai-cloud\n"
            "    model_version: gpt-4o-mini-2026-10\n"
            "    display_name: OpenAI GPT-4o mini\n"
            "    runtime_model: gpt-4o-mini\n"
        )
        target = tmp_path / "settings.yaml"
        target.write_text(config_text)
        config = load_yaml(target, AppConfig)
        assert len(config.cloud_destinations) == 2
        assert config.cloud_destinations[0].api_key_variable == "XAI_API_KEY"
        assert config.cloud_destinations[1].api_key_variable is None

    def test_yaml_with_invalid_cloud_destination_is_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "settings.yaml"
        bad.write_text(
            "schema_version: settings-v1\n"
            "mock_mode: true\n"
            "cloud_destinations:\n"
            "  - endpoint: http://api.x.ai/v1/chat/completions\n"
            "    model_id: grok-cloud\n"
            "    model_version: v1\n"
            "    runtime_model: grok\n"
        )
        config = load_yaml(bad, AppConfig)
        assert len(config.cloud_destinations) == 1
        with pytest.raises(ValidationError):
            CloudDestination.model_validate(config.cloud_destinations[0].model_dump())

    def test_unknown_field_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "settings.yaml"
        bad.write_text(
            "schema_version: settings-v1\n"
            "mock_mode: true\n"
            "cloud_destinations:\n"
            "  - endpoint: https://api.x.ai/v1/chat/completions\n"
            "    model_id: grok-cloud\n"
            "    model_version: v1\n"
            "    runtime_model: grok\n"
            "    unknown_field: oops\n"
        )
        with pytest.raises(ConfigError):
            load_yaml(bad, AppConfig)


class TestCloudProviderRuntimeKey:
    @staticmethod
    def _provider() -> CloudProvider:
        destination = CloudDestination.model_validate(
            {
                "endpoint": "https://api.x.ai/v1/chat/completions",
                "model_id": "grok-cloud",
                "model_version": "v1",
                "runtime_model": "grok",
                "display_name": "xAI Grok",
            }
        )
        return CloudProvider(destination, environment={})

    def test_starts_with_no_key(self) -> None:
        provider = self._provider()
        assert provider.key_source == "none"
        assert provider.describe("grok-cloud").key_available is False

    def test_runtime_key_marks_source_runtime(self) -> None:
        provider = self._provider()
        provider.set_api_key("xai-test-key")
        assert provider.key_source == "runtime"
        assert provider.describe("grok-cloud").key_available is True

    def test_env_key_marks_source_env(self) -> None:
        from backend.providers.cloud_openai_compat import CloudProvider

        destination = CloudDestination.model_validate(
            {
                "endpoint": "https://api.x.ai/v1/chat/completions",
                "model_id": "grok-cloud",
                "model_version": "v1",
                "runtime_model": "grok",
                "api_key_variable": "XAI_API_KEY",
            }
        )
        provider = CloudProvider(destination, environment={"XAI_API_KEY": "xai-env-key"})
        assert provider.key_source == "env"
        assert provider.describe("grok-cloud").key_available is True

    def test_clear_api_key(self) -> None:
        provider = self._provider()
        provider.set_api_key("xai-test-key")
        provider.clear_api_key()
        assert provider.key_source == "none"
        assert provider.describe("grok-cloud").key_available is False

    @pytest.mark.parametrize("bad", ["", " ", "key with space", "ascii\xff"])
    def test_rejects_malformed_keys(self, bad: str) -> None:
        provider = self._provider()
        with pytest.raises(ValueError):
            provider.set_api_key(bad)
