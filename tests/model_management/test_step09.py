"""Synthetic verified-shape manifests and mocked HTTP only: no real model/source claim."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.api.main import create_app
from backend.api.schemas import ChatRequest, ChatResponse, ErrorResponse
from backend.broker.local_runtime import REAL_CONTEXT_TOKENS, RealLocalBroker
from backend.broker.orchestrator import MockAnalyzer, build_local_control, build_real_broker
from backend.broker.real_smoke import execute_smoke, smoke_plan
from backend.config.loader import load_catalog
from backend.model_management.hardware import GIB, HardwareReport
from backend.model_management.initial_install import (
    InitialInstallError,
    RuntimeLease,
    plan_install,
    trusted_manifest,
)
from backend.model_management.manifest import ModelCatalog, ModelManifest
from backend.model_management.registry import ModelRegistry, RuntimeInventory
from backend.privacy.analyzer import AnalyzerUnavailableError, PresidioAnalyzer
from scripts.step09 import fixture_hardware

pytestmark = pytest.mark.privacy


class Scenario:
    """Invented registry/daemon. HTTPX intercepts every byte; no sockets or downloads."""

    def __init__(self, count: int = 2) -> None:
        self.blobs: dict[str, bytes] = {}
        models = []
        for index in range(count):
            name = f"fixture-model-{index}"
            raw = json.dumps(
                {
                    "schemaVersion": 2,
                    "config": {"digest": "sha256:" + str(index) * 64, "size": 512},
                    "layers": [{"digest": "sha256:" + "a" * 64, "size": 512}],
                    "synthetic_fixture": index,
                }
            ).encode()
            digest = hashlib.sha256(raw).hexdigest()
            self.blobs[name] = raw
            models.append(
                ModelManifest.model_validate(
                    {
                        "id": name,
                        "runtime_tag": name + ":fixture-q4",
                        "pinned_digest": digest,
                        "source": {
                            "uri": f"https://registry.ollama.ai/v2/library/{name}/manifests/fixture-q4",
                            "revision": digest,
                        },
                        "family": "synthetic",
                        "license_review": {
                            "status": "approved",
                            "identifier": "INVENTED-TEST-LICENSE",
                            "reference": {"uri": "https://example.invalid/synthetic-license"},
                        },
                        "quantization": "Q4_K_M",
                        "size_bytes": 1024,
                        "memory_bytes": GIB,
                        "platforms": ["linux-x86_64"],
                        "verification": "verified",
                    }
                )
            )
        self.catalog = ModelCatalog(models=tuple(models))
        self.installed = {item.runtime_tag: item.pinned_digest for item in models}
        self.resident: set[str] = set()
        self.calls: list[httpx.Request] = []
        self.offline = False
        self.ps_unavailable = False
        self.changed_remote = False
        self.bad_post_digest = False
        self.generation_timeout = False
        self.pull_failure = False
        self.output: str | None = None
        self.transport = httpx.MockTransport(self.respond)

    def respond(self, request: httpx.Request) -> httpx.Response:
        import re

        self.calls.append(request)
        if self.offline:
            raise httpx.ConnectError("Synthetic unavailable", request=request)
        path = request.url.path
        if request.url.host == "registry.ollama.ai":
            raw = self.blobs[path.split("/")[3]]
            return httpx.Response(
                200, content=b"synthetic changed manifest" if self.changed_remote else raw
            )
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.99.0-fixture"})
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": name, "digest": digest} for name, digest in self.installed.items()
                    ]
                },
            )
        if path == "/api/ps":
            return (
                httpx.Response(503)
                if self.ps_unavailable
                else httpx.Response(
                    200,
                    json={
                        "models": [
                            {"name": name, "digest": self.installed[name], "size": GIB}
                            for name in self.resident
                        ]
                    },
                )
            )
        if path == "/api/pull":
            if self.pull_failure:
                return httpx.Response(503, json={"error": "SYNTHETIC-PRIVATE-DIAGNOSTIC"})
            values = json.loads(request.content)
            assert values["stream"] is False and values["insecure"] is False
            item = next(
                model for model in self.catalog.models if model.runtime_tag == values["model"]
            )
            self.installed[item.runtime_tag] = (
                "f" * 64 if self.bad_post_digest else item.pinned_digest
            )
            return httpx.Response(200, json={"status": "success"})
        if path == "/api/generate":
            if self.generation_timeout:
                raise httpx.ReadTimeout("Synthetic timeout", request=request)
            values = json.loads(request.content)
            assert values["stream"] is False and values["keep_alive"] in {0, 60}
            assert values["options"]["num_ctx"] == REAL_CONTEXT_TOKENS
            prompt = values["prompt"]
            if values["keep_alive"]:
                self.resident.add(values["model"])
            else:
                self.resident.discard(values["model"])
            found = re.search(r"\[\[PII_[^\]]+\]\]", prompt)
            answer = (
                self.output
                if self.output is not None
                else found[0]
                if found
                else '{"status":"ready"}'
                if "JSON object" in prompt
                else "4"
            )
            return httpx.Response(
                200,
                json={
                    "model": values["model"],
                    "response": answer,
                    "done": True,
                    "total_duration": 10000000,
                    "load_duration": 1000000,
                    "eval_duration": 5000000,
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                },
            )
        raise AssertionError("Unexpected transport destination")

    def count(self, path: str) -> int:
        return sum(item.url.path == path for item in self.calls)


def hardware() -> HardwareReport:
    # Test values are invented observations for structural validation, not a real hardware report.
    return fixture_hardware().model_copy(update={"evidence": "observed"})


async def broker_for(scenario: Scenario, path: Path, **options: Any) -> RealLocalBroker:
    result = await build_real_broker(
        catalog=scenario.catalog,
        hardware_probe=hardware,
        analyzer=MockAnalyzer(),
        transport=scenario.transport,
        lease=RuntimeLease(path),
        background_refresh=False,
        allow_mock_fallback=False,
        **options,
    )
    assert isinstance(result, RealLocalBroker)
    return result


@pytest.mark.parametrize("model_id", ["missing", "https://evil.invalid/model", "arbitrary:tag"])
def test_install_accepts_only_known_catalog_ids(model_id: str) -> None:
    with pytest.raises(InitialInstallError, match="unknown_catalog_model"):
        trusted_manifest(Scenario().catalog, model_id)


def test_placeholders_never_become_real_install_targets() -> None:
    catalog = load_catalog()
    placeholders = [item for item in catalog.models if item.verification == "unverified"]
    assert placeholders
    for item in placeholders:
        with pytest.raises(InitialInstallError, match="unverified_manifest"):
            trusted_manifest(catalog, item.id)


@pytest.mark.parametrize(
    "changes",
    [
        {"uri": "https://example.invalid/unapproved-source"},
        {"revision": "e" * 64},
    ],
)
def test_catalog_source_and_digest_are_bound(changes: dict[str, str]) -> None:
    item = Scenario().catalog.models[0]
    changed = ModelManifest.model_validate(
        {**item.model_dump(), "source": {**item.source.model_dump(), **changes}}
    )
    with pytest.raises(InitialInstallError, match="untrusted_source"):
        trusted_manifest(ModelCatalog(models=(changed,)), item.id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pinned_digest", "e" * 64),
        ("model_id", "other"),
        ("manifest_version", "e" * 64),
        ("endpoint", "http://127.0.0.1:9999"),
        ("model_store_id", "e" * 64),
        ("expires_at", datetime.now(UTC) - timedelta(seconds=1)),
    ],
)
def test_changed_or_expired_install_authorization_has_zero_calls(field: str, value: object) -> None:
    async def run() -> None:
        scenario = Scenario(3)
        del scenario.installed[scenario.catalog.models[-1].runtime_tag]
        control = build_local_control(scenario.catalog, transport=scenario.transport)
        report = await control.observe()
        plan = plan_install(
            scenario.catalog,
            scenario.catalog.models[-1].id,
            hardware(),
            report.inventory,
            control.endpoint,
        )
        before = len(scenario.calls)
        changed = plan.model_copy(update={field: value})
        with pytest.raises(InitialInstallError):
            await control.install_authorized(
                scenario.catalog.models[-1], changed, plan.approval_digest, hardware()
            )
        assert len(scenario.calls) == before
        await control.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("none", None),
        ("changed_remote", "upstream_identity_changed"),
        ("bad_post_digest", "installed_identity_unverified"),
    ],
)
def test_authorized_install_verifies_before_and_after_pull(
    failure: str, expected: str | None
) -> None:
    async def run() -> None:
        scenario = Scenario(3)
        model = scenario.catalog.models[-1]
        del scenario.installed[model.runtime_tag]
        scenario.changed_remote = failure == "changed_remote"
        scenario.bad_post_digest = failure == "bad_post_digest"
        control = build_local_control(scenario.catalog, transport=scenario.transport)
        runtime = await control.observe()
        plan = plan_install(
            scenario.catalog, model.id, hardware(), runtime.inventory, control.endpoint
        )
        if expected:
            with pytest.raises(InitialInstallError, match=expected):
                await control.install_authorized(model, plan, plan.approval_digest, hardware())
        else:
            result = await control.install_authorized(model, plan, plan.approval_digest, hardware())
            assert (
                next(item for item in result.models if item.runtime_tag == model.runtime_tag).digest
                == model.pinned_digest
            )
        assert scenario.count("/api/pull") == (0 if failure == "changed_remote" else 1)
        assert scenario.count("/api/generate") == 0
        assert not any(
            item.method == "DELETE" or item.url.path in {"/api/delete", "/api/copy"}
            for item in scenario.calls
        )
        if failure == "bad_post_digest":
            status = await control.observe()
            registry = ModelRegistry(scenario.catalog, hardware=hardware().context)
            assert model.id not in registry.reconcile(status.inventory).eligible_model_ids
        await control.aclose()

    asyncio.run(run())


def test_existing_mismatched_identity_is_not_overwritten_and_fourth_managed_install_refused() -> (
    None
):
    async def run() -> None:
        scenario = Scenario(4)
        fourth = scenario.catalog.models[-1]
        del scenario.installed[fourth.runtime_tag]
        control = build_local_control(scenario.catalog, transport=scenario.transport)
        report = await control.observe()
        with pytest.raises(InitialInstallError, match="managed_model_limit"):
            plan_install(
                scenario.catalog, fourth.id, hardware(), report.inventory, control.endpoint
            )
        scenario.installed[scenario.catalog.models[0].runtime_tag] = "f" * 64
        report = await control.observe()
        with pytest.raises(InitialInstallError, match="identity_conflict"):
            plan_install(
                scenario.catalog,
                scenario.catalog.models[0].id,
                hardware(),
                report.inventory,
                control.endpoint,
            )
        assert scenario.count("/api/pull") == 0
        await control.aclose()

    asyncio.run(run())


def test_install_refuses_unknown_disk_and_synthetic_hardware() -> None:
    item = Scenario().catalog.models[0]
    for report, code in [
        (hardware().model_copy(update={"model_store_free_bytes": None}), "disk_unknown"),
        (fixture_hardware(), "synthetic_hardware"),
    ]:
        with pytest.raises(InitialInstallError, match=code):
            plan_install(
                ModelCatalog(models=(item,)),
                item.id,
                report,
                RuntimeInventory(),
                "http://127.0.0.1:11434",
            )


def test_operation_lease_is_exclusive_and_preserves_replacement_file(tmp_path: Path) -> None:
    path = tmp_path / "runtime.lock"
    lease = RuntimeLease(path).acquire()
    with pytest.raises(InitialInstallError):
        RuntimeLease(path).acquire()
    path.unlink()
    path.write_text("synthetic unrelated file")
    lease.release()
    assert path.read_text() == "synthetic unrelated file"


def test_real_broker_masks_restores_and_reports_observed_metrics(tmp_path: Path) -> None:
    async def run() -> None:
        scenario = Scenario()
        broker = await broker_for(scenario, tmp_path / "runtime.lock")
        response = await broker.chat(ChatRequest(prompt="Repeat STU-1234-5678"))
        assert (
            isinstance(response, ChatResponse)
            and response.answer == "STU-1234-5678"
            and response.restored
            and not response.mock
        )
        assert broker.health_status().mode == "real"
        assert (
            response.generation_metrics
            and response.generation_metrics.output_tokens_per_second == 1000.0
        )
        generation = [
            json.loads(item.content) for item in scenario.calls if item.url.path == "/api/generate"
        ]
        assert len(generation) == 1 and "STU-1234-5678" not in generation[0]["prompt"]
        assert scenario.count("/api/pull") == 0
        await broker.aclose()
        assert not (tmp_path / "runtime.lock").exists()

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["privacy", "low_memory", "unknown_residency", "digest_changed"]
)
def test_real_path_faults_make_zero_generation_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def run() -> None:
        scenario = Scenario()
        broker = await broker_for(scenario, tmp_path / "runtime.lock")
        if failure == "privacy":

            def fail(*args: object) -> object:
                raise ValueError("Synthetic scanner fault")

            monkeypatch.setattr(broker._sanitizer, "sanitize", fail)
        elif failure == "low_memory":
            broker.monitor.probe = lambda: hardware().model_copy(
                update={"available_ram_bytes": GIB // 2}
            )
        elif failure == "unknown_residency":
            scenario.ps_unavailable = True
        else:
            scenario.installed = {key: "f" * 64 for key in scenario.installed}
        response = await broker.chat(ChatRequest(prompt="Synthetic request"))
        assert isinstance(response, ErrorResponse)
        assert scenario.count("/api/generate") == 0 and scenario.count("/api/pull") == 0
        await broker.aclose()

    asyncio.run(run())


def test_unavailable_runtime_fallback_is_explicit_and_skips_hardware_and_assets(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        scenario = Scenario()
        scenario.offline = True

        def forbidden() -> HardwareReport:
            raise AssertionError("Hardware probe must not run for unavailable fallback")

        result = await build_real_broker(
            catalog=scenario.catalog,
            hardware_probe=forbidden,
            transport=scenario.transport,
            lease=RuntimeLease(tmp_path / "runtime.lock"),
        )
        assert (
            result.health_status().mode == "mock"
            and result.health_status().notice == "ollama_unavailable_using_mock"
        )
        assert scenario.count("/api/generate") == 0
        await result.aclose()
        assert not (tmp_path / "runtime.lock").exists()

    asyncio.run(run())


def test_missing_privacy_asset_and_unverified_catalog_block_real_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        scenario = Scenario()

        def fail() -> None:
            raise AnalyzerUnavailableError("Synthetic missing asset")

        monkeypatch.setattr(PresidioAnalyzer, "preload", fail)
        with pytest.raises(AnalyzerUnavailableError):
            await build_real_broker(
                catalog=scenario.catalog,
                hardware_probe=hardware,
                transport=scenario.transport,
                lease=RuntimeLease(tmp_path / "runtime.lock"),
            )
        assert not (tmp_path / "runtime.lock").exists()
        assert scenario.count("/api/generate") == 0

    asyncio.run(run())


@pytest.mark.parametrize("feasibility", [False, True])
def test_bounded_smoke_uses_each_model_and_exports_no_text(
    tmp_path: Path, feasibility: bool
) -> None:
    async def run() -> None:
        scenario = Scenario()
        broker = await broker_for(scenario, tmp_path / "runtime.lock")
        plan = smoke_plan(broker, feasibility=feasibility)
        with pytest.raises(InitialInstallError, match="not_authorized"):
            await execute_smoke(broker, plan, "wrong")
        assert scenario.count("/api/generate") == 0
        report = await execute_smoke(broker, plan, plan.approval_digest, evidence="mocked_http")
        assert len(report.rows) == plan.planned_calls and not report.stopped
        assert {row.model_id for row in report.rows} == set(plan.model_ids)
        assert all(row.objective_quality == 1.0 and row.format_valid for row in report.rows)
        assert all(row.ttft_ms is None for row in report.rows)
        assert (
            "STU-1234-5678" not in report.model_dump_json()
            and "Reply only" not in report.model_dump_json()
        )
        assert report.evidence == "mocked_http" and report.cloud_calls == 0
        await broker.aclose()

    asyncio.run(run())


def test_real_smoke_stops_on_timeout_without_cloud_or_followup(tmp_path: Path) -> None:
    async def run() -> None:
        scenario = Scenario()
        scenario.generation_timeout = True
        broker = await broker_for(scenario, tmp_path / "runtime.lock")
        plan = smoke_plan(broker, feasibility=False)
        report = await execute_smoke(broker, plan, plan.approval_digest, evidence="mocked_http")
        assert report.stopped and len(report.rows) == 1 and report.rows[0].error_code == "timeout"
        assert scenario.count("/api/generate") == 1 and report.rows[0].objective_quality is None
        await broker.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["version", "tags", "duplicate", "oversize", "redirect"])
def test_invalid_runtime_metadata_is_safe_and_never_generates(kind: str) -> None:
    async def run() -> None:
        scenario = Scenario()

        def respond(request: httpx.Request) -> httpx.Response:
            if kind == "version" and request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "SYNTHETIC-PRIVATE-DIAGNOSTIC"})
            if kind == "tags" and request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": "SYNTHETIC-PRIVATE-DIAGNOSTIC"})
            if kind == "duplicate" and request.url.path == "/api/tags":
                entry = {
                    "name": scenario.catalog.models[0].runtime_tag,
                    "digest": scenario.catalog.models[0].pinned_digest,
                }
                return httpx.Response(200, json={"models": [entry, entry]})
            if kind == "oversize":
                return httpx.Response(200, content=b"x" * 1_048_577)
            if kind == "redirect":
                return httpx.Response(302, headers={"Location": "https://evil.invalid/"})
            return scenario.respond(request)

        control = build_local_control(scenario.catalog, transport=httpx.MockTransport(respond))
        report = await control.observe()
        assert not report.available and not report.inventory.available
        assert "SYNTHETIC-PRIVATE" not in report.model_dump_json()
        assert scenario.count("/api/generate") == 0
        await control.aclose()

    asyncio.run(run())


def test_external_runtime_names_are_not_exported_or_deleted() -> None:
    async def run() -> None:
        scenario = Scenario()
        scenario.installed["SYNTHETIC-PRIVATE-MODEL:local"] = "f" * 64
        control = build_local_control(scenario.catalog, transport=scenario.transport)
        report = await control.observe()
        assert report.installed_count == 3 and len(report.inventory.models) == 2
        assert "SYNTHETIC-PRIVATE-MODEL" not in report.model_dump_json()
        assert all(request.method == "GET" for request in scenario.calls)
        await control.aclose()

    asyncio.run(run())


def test_expiry_is_rechecked_after_registry_verification_before_pull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        scenario = Scenario(3)
        target = scenario.catalog.models[-1]
        del scenario.installed[target.runtime_tag]
        control = build_local_control(scenario.catalog, transport=scenario.transport)
        runtime = await control.observe()
        plan = plan_install(
            scenario.catalog, target.id, hardware(), runtime.inventory, control.endpoint
        )

        class Clock:
            count = 0

            @classmethod
            def now(cls, zone: object) -> datetime:
                cls.count += 1
                return plan.expires_at + timedelta(seconds=1 if cls.count > 1 else -1)

        monkeypatch.setattr("backend.providers.ollama_slm.datetime", Clock)
        with pytest.raises(InitialInstallError, match="approval_conflict"):
            await control.install_authorized(target, plan, plan.approval_digest, hardware())
        assert scenario.count("/api/pull") == 0
        await control.aclose()

    asyncio.run(run())


def test_failed_install_approval_cannot_be_replayed() -> None:
    async def run() -> None:
        scenario = Scenario(3)
        target = scenario.catalog.models[-1]
        del scenario.installed[target.runtime_tag]
        scenario.pull_failure = True
        control = build_local_control(scenario.catalog, transport=scenario.transport)
        runtime = await control.observe()
        plan = plan_install(
            scenario.catalog, target.id, hardware(), runtime.inventory, control.endpoint
        )
        with pytest.raises(InitialInstallError, match="install_failed"):
            await control.install_authorized(target, plan, plan.approval_digest, hardware())
        with pytest.raises(InitialInstallError, match="approval_conflict"):
            await control.install_authorized(target, plan, plan.approval_digest, hardware())
        assert scenario.count("/api/pull") == 1
        assert target.runtime_tag not in scenario.installed
        await control.aclose()

    asyncio.run(run())


def test_concurrent_install_consumes_approval_before_first_write() -> None:
    async def run() -> None:
        scenario = Scenario(3)
        target = scenario.catalog.models[-1]
        del scenario.installed[target.runtime_tag]
        scenario.pull_failure = True
        entered = asyncio.Event()
        resume = asyncio.Event()

        async def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/pull":
                entered.set()
                await resume.wait()
            return scenario.respond(request)

        control = build_local_control(scenario.catalog, transport=httpx.MockTransport(respond))
        runtime = await control.observe()
        plan = plan_install(
            scenario.catalog, target.id, hardware(), runtime.inventory, control.endpoint
        )
        first = asyncio.create_task(
            control.install_authorized(target, plan, plan.approval_digest, hardware())
        )
        await entered.wait()
        try:
            with pytest.raises(InitialInstallError, match="approval_conflict"):
                await control.install_authorized(target, plan, plan.approval_digest, hardware())
        finally:
            resume.set()
        with pytest.raises(InitialInstallError, match="install_failed"):
            await first
        assert scenario.count("/api/pull") == 1
        await control.aclose()

    asyncio.run(run())


def test_catalog_download_size_is_checked_against_pinned_remote_metadata() -> None:
    async def run() -> None:
        scenario = Scenario(3)
        target = scenario.catalog.models[-1].model_copy(update={"size_bytes": 1})
        scenario.catalog = ModelCatalog(models=(*scenario.catalog.models[:-1], target))
        del scenario.installed[target.runtime_tag]
        control = build_local_control(scenario.catalog, transport=scenario.transport)
        runtime = await control.observe()
        plan = plan_install(
            scenario.catalog, target.id, hardware(), runtime.inventory, control.endpoint
        )
        with pytest.raises(InitialInstallError, match="size_mismatch"):
            await control.install_authorized(target, plan, plan.approval_digest, hardware())
        assert scenario.count("/api/pull") == 0
        await control.aclose()

    asyncio.run(run())


def test_changed_inventory_or_disk_blocks_install_after_approval() -> None:
    async def run() -> None:
        scenario = Scenario(3)
        target = scenario.catalog.models[-1]
        del scenario.installed[target.runtime_tag]
        control = build_local_control(scenario.catalog, transport=scenario.transport)
        runtime = await control.observe()
        plan = plan_install(
            scenario.catalog, target.id, hardware(), runtime.inventory, control.endpoint
        )
        with pytest.raises(InitialInstallError, match="insufficient_disk"):
            await control.install_authorized(
                target,
                plan,
                plan.approval_digest,
                hardware().model_copy(update={"model_store_free_bytes": 0}),
            )
        del scenario.installed[scenario.catalog.models[0].runtime_tag]
        with pytest.raises(InitialInstallError, match="approval_conflict"):
            await control.install_authorized(target, plan, plan.approval_digest, hardware())
        assert scenario.count("/api/pull") == 0
        await control.aclose()

    asyncio.run(run())


def test_invalid_real_endpoint_does_not_leave_an_operation_lock(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "runtime.lock"
        with pytest.raises(ValueError, match="destination"):
            await build_real_broker(
                catalog=Scenario().catalog,
                endpoint="https://evil.invalid",
                hardware_probe=hardware,
                analyzer=MockAnalyzer(),
                lease=RuntimeLease(path),
            )
        assert not path.exists()

    asyncio.run(run())


def test_real_close_failure_still_releases_the_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        broker = await broker_for(Scenario(), tmp_path / "runtime.lock")

        async def fail() -> None:
            raise RuntimeError("Synthetic transport close failure")

        monkeypatch.setattr(broker.monitor.control, "aclose", fail)
        with pytest.raises(RuntimeError):
            await broker.aclose()
        assert not (tmp_path / "runtime.lock").exists()

    asyncio.run(run())


def test_real_monitor_clears_and_recovers_inventory_without_generation(tmp_path: Path) -> None:
    async def run() -> None:
        scenario = Scenario()
        broker = await broker_for(scenario, tmp_path / "runtime.lock")
        scenario.offline = True
        await broker.monitor.refresh()
        assert not broker.registry.snapshot().eligible_model_ids
        scenario.offline = False
        await broker.monitor.refresh()
        assert len(broker.registry.snapshot().eligible_model_ids) == 2
        broker.monitor.start()
        await broker.aclose()
        assert broker.monitor._task is None and scenario.count("/api/generate") == 0

    asyncio.run(run())


def test_memory_pressure_after_a_sample_stops_the_suite(tmp_path: Path) -> None:
    async def run() -> None:
        scenario = Scenario()
        broker = await broker_for(scenario, tmp_path / "runtime.lock")

        def changing() -> HardwareReport:
            return (
                hardware().model_copy(update={"available_ram_bytes": GIB // 2})
                if scenario.count("/api/generate")
                else hardware()
            )

        broker.monitor.probe = changing
        plan = smoke_plan(broker, feasibility=True)
        report = await execute_smoke(broker, plan, plan.approval_digest, evidence="mocked_http")
        assert (
            report.stopped and report.stop_reason == "post_request_resources_or_runtime_unavailable"
        )
        assert len(report.rows) == 1 and scenario.count("/api/generate") == 1
        await broker.aclose()

    asyncio.run(run())


def test_format_validity_and_objective_quality_stay_separate(tmp_path: Path) -> None:
    async def run() -> None:
        scenario = Scenario()
        scenario.output = "5"
        broker = await broker_for(scenario, tmp_path / "runtime.lock")
        plan = smoke_plan(broker, feasibility=False)
        report = await execute_smoke(broker, plan, plan.approval_digest, evidence="mocked_http")
        assert report.rows[0].format_valid and report.rows[0].objective_quality == 0.0
        assert report.rows[0].input_tokens == 10 and report.rows[0].output_tokens == 5
        assert report.rows[0].ttft_ms is None
        await broker.aclose()

    asyncio.run(run())


def test_native_api_is_started_only_in_lifespan_with_mocked_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        scenario = Scenario()
        started = False

        async def factory(**options: object) -> RealLocalBroker:
            nonlocal started
            started = True
            return await broker_for(scenario, tmp_path / "runtime.lock")

        monkeypatch.setattr("backend.api.main.build_real_broker", factory)
        token = "synthetic-local-write-" + "x" * 40
        app = create_app(environment={"MOCK_MODE": "0", "LOCAL_WRITE_TOKEN": token})
        assert not started and not scenario.calls
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:8000",
                headers={"X-Local-Token": token},
            ) as client,
        ):
            response = await client.get("/api/health")
            assert response.status_code == 200 and response.json()["mode"] == "real"
            assert scenario.count("/api/generate") == 0
            result = await client.post("/api/chat", json={"prompt": "Synthetic arithmetic request"})
            assert result.status_code == 200 and result.json()["mock"] is False
            assert result.json()["generation_metrics"]["input_tokens"] == 10
        assert not (tmp_path / "runtime.lock").exists()

    asyncio.run(run())


def test_mock_api_never_probes_or_constructs_a_real_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("Real operation forbidden in default mode")

        monkeypatch.setattr("backend.model_management.hardware.probe_hardware", forbidden)
        monkeypatch.setattr("backend.broker.orchestrator.build_local_control", forbidden)
        token = "synthetic-local-write-" + "x" * 40
        app = create_app(environment={"MOCK_MODE": "1", "LOCAL_WRITE_TOKEN": token})
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:8000",
                headers={"X-Local-Token": token},
            ) as client,
        ):
            result = await client.post("/api/chat", json={"prompt": "Synthetic fixture question"})
            assert result.status_code == 200 and result.json()["mock"]

    asyncio.run(run())
