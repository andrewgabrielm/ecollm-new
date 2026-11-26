"""Private local IPC for the UI proxy; credentials never enter browser assets or files."""

import json
import os
import socketserver
from pathlib import Path
from threading import Thread
from types import TracebackType

from backend.api.security import ApiSettings

ROOT = Path(__file__).resolve().parents[2]
UI_ORIGIN = "http://127.0.0.1:5173"
BRIDGE_PATH = ROOT / "var" / "ui" / "bridge.sock"


class _Server(socketserver.UnixStreamServer):
    def handle_error(self, request: object, client_address: object) -> None:
        # No content-bearing socket/handler exceptions in runtime logs.
        pass


class UiBridge:
    """Same-user Unix socket, private directory, bounded handshake and owned cleanup."""

    def __init__(self, settings: ApiSettings, path: Path = BRIDGE_PATH) -> None:
        self._path = path
        self._settings = settings
        self._server: _Server | None = None
        self._thread: Thread | None = None
        self._identity: tuple[int, int] | None = None

    def __repr__(self) -> str:
        return "UiBridge(<redacted>)"

    def __enter__(self) -> "UiBridge":
        directory = self._path.parent
        if directory.is_symlink() or directory.parent.is_symlink():
            raise ValueError("UI bridge requires an owned local directory")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("UI bridge directory must be private to its owner (0700)")
        if self._path.exists() or self._path.is_symlink():
            raise ValueError("UI bridge already exists; stop the previous backend first")
        encoded = (
            json.dumps(
                {
                    "token": self._settings.write_token.get_secret_value(),
                    "backendPort": self._settings.port,
                    "uiOrigin": UI_ORIGIN,
                }
            ).encode("ascii")
            + b"\n"
        )

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.request.settimeout(1.0)
                if self.rfile.readline(64) == b"ECOLM_UI_V1\n":
                    self.wfile.write(encoded)

        self._server = _Server(str(self._path), Handler)
        bound = self._path.lstat()
        self._identity = (bound.st_dev, bound.st_ino)
        try:
            self._path.chmod(0o600)
            self._thread = Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1})
            self._thread.daemon = True
            self._thread.start()
        except Exception:
            self.__exit__(None, None, None)
            raise ValueError("UI bridge startup failed") from None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._server is not None:
            if self._thread is not None and self._thread.is_alive():
                self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            if self._thread.ident is not None:
                self._thread.join(timeout=2)
            self._thread = None
        try:
            current = self._path.lstat()
        except FileNotFoundError:
            pass
        else:
            if (current.st_dev, current.st_ino) == self._identity:
                self._path.unlink()
        self._identity = None
