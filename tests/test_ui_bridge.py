"""UI proxy IPC is same-user, private, content-free on errors and lifecycle-bound."""

import json
import socket
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

import pytest
from pydantic import SecretStr

from backend.api.security import ApiSettings
from backend.api.ui_bridge import ROOT, UI_ORIGIN, UiBridge

pytestmark = pytest.mark.privacy
TOKEN = "synthetic-ui-only-" + "x" * 40


@pytest.fixture
def bridge_path() -> Iterator[Path]:
    (ROOT / "var").mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="ui-test-", dir=ROOT / "var") as directory:
        yield Path(directory) / "bridge.sock"


def test_private_ipc_handshake_and_shutdown(bridge_path: Path) -> None:
    settings = ApiSettings(write_token=SecretStr(TOKEN))
    with UiBridge(settings, bridge_path) as bridge:
        assert TOKEN not in repr(bridge)
        assert bridge_path.stat().st_mode & 0o777 == 0o600
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(bridge_path))
            client.sendall(b"ECOLM_UI_V1\n")
            value = json.loads(client.recv(2048))
        assert value == {"token": TOKEN, "backendPort": 8000, "uiOrigin": UI_ORIGIN}
    assert not bridge_path.exists()


def test_invalid_handshake_returns_no_secret(bridge_path: Path) -> None:
    with (
        UiBridge(ApiSettings(write_token=SecretStr(TOKEN)), bridge_path),
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client,
    ):
        client.connect(str(bridge_path))
        client.sendall(b"invalid\n")
        assert client.recv(2048) == b""


def test_bridge_refuses_existing_path_without_overwriting(bridge_path: Path) -> None:
    bridge_path.write_text("synthetic-owned-file")
    with (
        pytest.raises(ValueError, match="already exists"),
        UiBridge(ApiSettings(write_token=SecretStr(TOKEN)), bridge_path),
    ):
        pytest.fail("Must not start")
    assert bridge_path.read_text() == "synthetic-owned-file"


def test_bridge_refuses_world_readable_directory(bridge_path: Path) -> None:
    bridge_path.parent.chmod(0o755)
    with (
        pytest.raises(ValueError, match="private"),
        UiBridge(ApiSettings(write_token=SecretStr(TOKEN)), bridge_path),
    ):
        pytest.fail("Must not start")


def test_bridge_refuses_symlinks(bridge_path: Path) -> None:
    bridge_path.symlink_to(bridge_path.parent / "absent")
    with (
        pytest.raises(ValueError),
        UiBridge(ApiSettings(write_token=SecretStr(TOKEN)), bridge_path),
    ):
        pytest.fail("Must not start")


def test_bridge_cleanup_never_deletes_a_replacement_file(bridge_path: Path) -> None:
    with UiBridge(ApiSettings(write_token=SecretStr(TOKEN)), bridge_path):
        bridge_path.unlink()
        bridge_path.write_text("unrelated synthetic file")
    assert bridge_path.read_text() == "unrelated synthetic file"


def test_bridge_startup_failure_releases_its_socket(
    bridge_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(thread: object) -> None:
        raise RuntimeError("Synthetic thread startup failure")

    monkeypatch.setattr(Thread, "start", fail)
    with (
        pytest.raises(ValueError, match="startup failed"),
        UiBridge(ApiSettings(write_token=SecretStr(TOKEN)), bridge_path),
    ):
        pytest.fail("Must not start")
    assert not bridge_path.exists()
