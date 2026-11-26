"""Step 01 acceptance checks, requiring no endpoints or downloaded assets."""

import importlib
import logging
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from backend.config.loader import load_settings


@pytest.mark.parametrize(
    "package",
    [
        "backend.api",
        "backend.broker",
        "backend.privacy",
        "backend.providers",
        "backend.model_management",
        "backend.profiles",
        "backend.cache",
        "backend.learning",
        "backend.telemetry",
        "backend.config",
        "backend.storage",
    ],
)
def test_package_import_smoke(package: str) -> None:
    importlib.import_module(package)


def test_safe_config_defaults() -> None:
    settings = load_settings({})
    assert settings.mock_mode is True
    assert settings.cloud_api_key is None
    assert load_settings({"MOCK_MODE": "0"}).mock_mode is False


def test_config_does_not_echo_secrets(caplog: pytest.LogCaptureFixture) -> None:
    marker = "synthetic-private-value"
    with caplog.at_level(logging.DEBUG):
        settings = load_settings({"CLOUD_API_KEY": marker})
        logging.getLogger(__name__).debug("settings=%r", settings)
        with pytest.raises(ValueError) as caught:
            load_settings({"MOCK_MODE": marker})
    assert marker not in caplog.text
    assert marker not in str(caught.value)
    assert marker not in repr(settings)
    assert marker not in settings.model_dump_json()


def test_python_uses_project_environment() -> None:
    root = Path(__file__).resolve().parents[1]
    assert sys.version_info[:2] == (3, 11)
    assert Path(sys.prefix).resolve() == (root / ".venv").resolve()


def test_network_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="socket.socket"):
        socket.socket()


def test_default_command_deselects_slow_tests(tmp_path: Path) -> None:
    """Exercise pytest's actual project defaults in the same root virtualenv."""
    root = Path(__file__).resolve().parents[1]
    fixture = tmp_path / "test_marker_contract.py"
    fixture.write_text(
        "import pytest\n"
        "def test_fast():\n    assert True\n"
        "@pytest.mark.slow\n"
        "def test_slow():\n    raise AssertionError('slow ran')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(root / "pyproject.toml"), str(fixture)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 deselected" in result.stdout
