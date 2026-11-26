"""Playwright's private stdio fixture transport to the real in-process mock API.

No listener or real provider is created. stdout is the test's private transport,
never a log/artifact. Only invented browser-test content may be sent here.
"""

import asyncio
import json
import secrets
import sys

import httpx

from backend.api.main import create_app


async def run() -> None:
    token = secrets.token_urlsafe(32)
    app = create_app(environment={"MOCK_MODE": "1", "LOCAL_WRITE_TOKEN": token})
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
            headers={"X-Local-Token": token},
        ) as client,
    ):
        while line := await asyncio.to_thread(sys.stdin.readline):
            try:
                command = json.loads(line)
                path = command["path"]
                if (
                    not isinstance(path, str)
                    or "?" in path
                    or not path.startswith("/api/")
                    or command["method"] not in {"GET", "POST"}
                ):
                    raise ValueError("Invalid mock transport command")
                response = await client.request(command["method"], path, json=command.get("body"))
                result = {"status": response.status_code, "body": response.json()}
            except Exception:
                result = {"status": 500, "body": {"status": "fixture_transport_failed"}}
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(run())
