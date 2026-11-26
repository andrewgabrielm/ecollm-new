"""Deterministic Step 07 HTTP smoke via in-process ASGI and synthetic mocks only."""

import argparse
import asyncio
import json
import secrets
from pathlib import Path
from uuid import uuid4

import httpx

from backend.api.main import create_app
from scripts.export_openapi import export_schema


async def run_smoke() -> dict[str, object]:
    token = secrets.token_urlsafe(32)
    app = create_app(environment={"MOCK_MODE": "1", "LOCAL_WRITE_TOKEN": token})
    summary: dict[str, object] = {"mode": "MOCK_ONLY_IN_PROCESS_ASGI", "external_calls": 0}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
            headers={"X-Local-Token": token},
        ) as client,
    ):
        health = await client.get("/api/health")
        assert health.status_code == 200 and health.json()["mode"] == "mock"
        first = await client.post("/api/chat", json={"prompt": "A fixed synthetic question"})
        assert first.status_code == 200 and first.json()["model_id"] == "mock-general"
        summary["local_a"] = first.status_code
        second = await client.post("/api/chat", json={"prompt": "Write a Python function"})
        assert second.status_code == 200 and second.json()["model_id"] == "mock-specialist"
        summary["local_b"] = second.status_code
        cascade = await client.post(
            "/api/chat",
            json={
                "prompt": "Return JSON with an answer",
                "constraints": {"required_fields": ["answer"]},
            },
        )
        assert cascade.status_code == 200 and cascade.json()["reason"] == "local_cascade"
        summary["structured_cascade"] = cascade.status_code
        pii = await client.post("/api/chat", json={"prompt": "Hello STU-1234-5678"})
        assert pii.status_code == 200 and pii.json()["restored"]
        assert "STU-1234-5678" in pii.json()["answer"]
        summary["pii_restored"] = True
        body = {
            "prompt": "Return JSON about STU-1234-5678",
            "mode": "ask_before_cloud",
            "constraints": {"required_fields": ["result"]},
        }
        asked = await client.post("/api/chat", json=body)
        assert asked.status_code == 202
        proposal = asked.json()
        assert "STU-1234-5678" not in proposal["sanitized_preview"]
        approval = {
            "proposal_id": proposal["proposal_id"],
            "decision": "approve",
            "sanitized_preview": proposal["sanitized_preview"],
            "digest": proposal["digest"],
        }
        path = f"/api/chat/{proposal['request_id']}/approval"
        approved = await client.post(path, json=approval)
        assert approved.status_code == 200 and approved.json()["route"] == "cloud"
        assert approved.json()["mock"] and approved.json()["restored"]
        summary["approval_required"] = asked.status_code
        summary["approved_mock_cloud"] = approved.status_code
        replay = await client.post(path, json=approval)
        assert replay.status_code == 409
        summary["replay_blocked"] = replay.status_code
        again = (await client.post("/api/chat", json=body)).json()
        declined = await client.post(
            f"/api/chat/{again['request_id']}/approval",
            json={"proposal_id": again["proposal_id"], "decision": "decline"},
        )
        assert declined.status_code == 200 and declined.json()["status"] == "cancelled"
        summary["declined"] = declined.status_code
        blocked = await client.post(
            "/api/chat", json={"prompt": "fixture"}, headers={"X-Local-Token": ""}
        )
        assert blocked.status_code == 403
        summary["write_guard"] = blocked.status_code
        hostile = await client.get("/api/health", headers={"Host": "evil.invalid:8000"})
        assert hostile.status_code == 400
        summary["host_guard"] = hostile.status_code
        metrics = (await client.get("/api/metrics")).json()
        assert metrics["pending_approvals"] == 0 and not metrics["cache_enabled"]
        assert not metrics["persistence_enabled"] and not metrics["learned_router_enabled"]
        assert metrics["estimated_cost_usd"] is None
        summary["pending_approvals"] = metrics["pending_approvals"]
        summary["extensions_disabled"] = True
    summary["openapi"] = str(export_schema())
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    summary = asyncio.run(run_smoke())
    directory = (
        Path(__file__).resolve().parents[1] / "evaluation" / "results" / f"smoke-{uuid4().hex[:12]}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    artifact = directory / "summary.json"
    artifact.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("MOCK ONLY — in-process HTTP/ASGI; no server, external calls or real inference")
    for key, value in summary.items():
        print(f"{key}: {value}")
    print(f"Artifact: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
