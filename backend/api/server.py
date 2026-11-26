"""Owner-run loopback mock server; never launched by lint, tests or smoke."""

import argparse
import os
import secrets
from contextlib import nullcontext

import uvicorn

from backend.api.main import create_app
from backend.api.security import ApiSettings
from backend.api.ui_bridge import UI_ORIGIN, UiBridge
from backend.config.loader import load_settings
from backend.storage.connection import LocalDatabase


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--ui", action="store_true", help="Enable the private local UI proxy bridge"
    )
    args = parser.parse_args()
    if args.ui and not os.environ.get("LOCAL_WRITE_TOKEN"):
        os.environ["LOCAL_WRITE_TOKEN"] = secrets.token_urlsafe(32)
    settings = ApiSettings.from_environment(port=args.port)
    if args.ui:
        settings = ApiSettings.model_validate(
            {
                **settings.model_dump(),
                "write_token": settings.write_token,
                "allowed_origins": (UI_ORIGIN,),
            }
        )
    # Without a database the app runs with no profile, cache, telemetry or saved
    # conversation store, so every persistence feature silently reports itself
    # as unavailable. Migrations are applied before the first request.
    database = LocalDatabase()
    database.migrate()
    app = create_app(settings=settings, db=database)
    label = (
        "Mock API"
        if load_settings().mock_mode
        else "Local API (real requested; check /api/health for actual mode)"
    )
    print(f"{label}: http://127.0.0.1:{settings.port} (one worker; no access logs)")
    with UiBridge(settings) if args.ui else nullcontext():
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=settings.port,
            workers=1,
            proxy_headers=False,
            access_log=False,
            log_level="warning",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
