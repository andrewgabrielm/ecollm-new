"""Owner CLI: hardware/status, approved initial installs, and bounded local-only checks.

No operation runs on import. --fixture is metadata-only and never probes, connects,
downloads or generates. Real operations are not part of test/CI defaults.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from backend.broker.local_runtime import RealLocalBroker
from backend.broker.orchestrator import build_local_control, build_real_broker
from backend.broker.real_smoke import SmokeRow, execute_smoke, smoke_plan
from backend.config.loader import ConfigError, load_catalog
from backend.model_management.artifacts import run_directory, write_csv, write_json
from backend.model_management.fixtures import build_mock_catalog
from backend.model_management.hardware import GIB, HardwareReport, probe_hardware, resident_limit
from backend.model_management.initial_install import (
    ROOT,
    InitialInstallError,
    RuntimeLease,
    plan_install,
    trusted_manifest,
)
from backend.model_management.manifest import Platform
from backend.model_management.registry import (
    HardwareContext,
    ModelRegistry,
    RuntimeInventory,
    RuntimeObservation,
)
from backend.privacy.analyzer import PrivacyError


def fixture_hardware() -> HardwareReport:
    return HardwareReport(
        evidence="synthetic",
        architecture="x86_64",
        context=HardwareContext(platform=Platform.LINUX_X86_64, ram_bytes=16 * GIB, cpu_threads=4),
        available_ram_bytes=12 * GIB,
        project_free_bytes=32 * GIB,
        model_store_free_bytes=32 * GIB,
        model_store_id="f" * 64,
    )


def _confirm(phrase: str) -> bool:
    if not sys.stdin.isatty():
        return False
    print("No inference/download has started. Review the plan above.")
    print("To authorize, type this entire line exactly (including the leading words):")
    print(f"    {phrase}")
    return input("> ").strip() == phrase


def _hardware_artifact(report: HardwareReport, kind: str) -> Path:
    directory = run_directory(kind)
    path = write_json(directory, "hardware.json", report.model_dump(mode="json"))
    print(
        f"Hardware evidence: {report.evidence}; platform: {report.context.platform or 'unknown'}; "
        f"RAM: {report.context.ram_bytes}; available: {report.available_ram_bytes}; "
        f"model-store free: {report.model_store_free_bytes}; pressure: {report.pressure}"
    )
    print(f"Artifact: {path}")
    return directory


async def run(args: argparse.Namespace) -> int:
    if args.fixture:
        if args.command not in {"profile-hardware", "catalog-status"}:
            raise InitialInstallError("fixture_cannot_authorize_real_operation")
        hardware = fixture_hardware()
        directory = _hardware_artifact(hardware, "step09-fixture")
        fixture_catalog = build_mock_catalog()
        registry = ModelRegistry(fixture_catalog, hardware=hardware.context, mock_mode=True)
        registry.reconcile(
            RuntimeInventory(
                models=tuple(
                    RuntimeObservation(runtime_tag=model.runtime_tag, resident=False, healthy=True)
                    for model in fixture_catalog.models[:2]
                )
            )
        )
        snapshot = registry.snapshot()
        path = write_json(
            directory,
            "manifest.json",
            {
                "evidence": "synthetic",
                "generation_calls": 0,
                "network_calls": 0,
                "hardware_probes": 0,
                "registry": snapshot.model_dump(mode="json"),
            },
        )
        print("SYNTHETIC METADATA ONLY; no hardware probe, network, download or inference")
        print(f"Artifact: {path}")
        return 0
    store = args.model_store.expanduser() if args.model_store is not None else None
    if args.command == "profile-hardware":
        report = probe_hardware(ROOT, store)
        _hardware_artifact(report, "hardware")
        return (
            0 if report.context.platform is not None and report.context.ram_bytes is not None else 1
        )
    catalog = load_catalog()
    if args.command == "model-install" and not args.model:
        raise InitialInstallError("catalog_id_required")
    if args.command == "model-install":
        trusted_manifest(catalog, args.model)
    if args.command == "catalog-status":
        control = build_local_control(catalog, endpoint=args.endpoint)
        try:
            runtime = await control.observe()
        finally:
            await control.aclose()
        hardware = probe_hardware(ROOT, store)
        registry = ModelRegistry(catalog, hardware=hardware.context)
        registry.reconcile(runtime.inventory)
        directory = _hardware_artifact(hardware, "catalog")
        selected = tuple(
            item.manifest
            for item in registry.snapshot().roster.entries
            if item.manifest.id in registry.snapshot().eligible_model_ids
        )
        path = write_json(
            directory,
            "manifest.json",
            {
                "evidence": "observed",
                "runtime": runtime.model_dump(mode="json"),
                "catalog": catalog.model_dump(mode="json"),
                "registry": registry.snapshot().model_dump(mode="json"),
                "resident_limit_policy": resident_limit(selected, hardware),
                "selection_status": "waiting_for_owner_review",
                "generation_calls": 0,
            },
        )
        print(
            f"Runtime: {runtime.reason}; version: {runtime.version or 'unknown'}; "
            f"eligible managed: {len(selected)}"
        )
        for item in registry.snapshot().catalog_statuses:
            print(f"{item.model_id}: {', '.join(item.states)}; {item.reason.value}")
        print(
            "Actual candidate selection/download approval requires owner hardware/runtime review."
        )
        print(f"Artifact: {path}")
        return 0 if runtime.available else 1
    if args.command == "model-install":
        operation = RuntimeLease()
        control = build_local_control(catalog, endpoint=args.endpoint)
        try:
            operation.acquire()
            runtime = await control.observe()
            hardware = probe_hardware(ROOT, store)
            plan = plan_install(catalog, args.model, hardware, runtime.inventory, args.endpoint)
            manifest = trusted_manifest(catalog, args.model)
            directory = _hardware_artifact(hardware, "install")
            path = write_json(
                directory,
                "manifest.json",
                {
                    "evidence": "observed",
                    "plan": plan.model_dump(mode="json"),
                    "approval_digest": plan.approval_digest,
                    "model": manifest.model_dump(mode="json"),
                    "risk": "mutable_runtime_tag_post_install_digest_check_required; "
                    "no_existing_model_replacement_or_removal",
                },
            )
            print(
                f"Catalog ID: {manifest.id}; source: {manifest.source.uri}; "
                f"digest: {manifest.pinned_digest}; model bytes: {manifest.size_bytes}; "
                f"required free bytes: {plan.required_disk_bytes}; "
                f"license: {manifest.license_review.identifier}"
            )
            print(f"Plan: {path}")
            if args.plan_only:
                print("PLAN ONLY; no download started")
                return 0
            if not _confirm("APPROVE " + plan.approval_digest):
                raise InitialInstallError("install_not_authorized")
            write_json(
                directory,
                "operation.json",
                {
                    "schema_version": "initial-install-operation-v1",
                    "model_id": manifest.id,
                    "manifest_version": manifest.version,
                    "approval_digest": plan.approval_digest,
                    "state": "authorized_pending_result",
                },
            )
            hardware = probe_hardware(ROOT, store)
            try:
                inventory = await control.install_authorized(
                    manifest, plan, plan.approval_digest, hardware
                )
            except InitialInstallError as error:
                failed = write_json(
                    directory,
                    "install_state.json",
                    {
                        "state": "failed_unverified",
                        "model_id": manifest.id,
                        "manifest_version": manifest.version,
                        "error_code": error.code,
                        "eligible": False,
                    },
                )
                print(f"Artifact: {failed}")
                raise
            registry = ModelRegistry(catalog, hardware=hardware.context)
            registry.reconcile(inventory)
            status = next(
                item
                for item in registry.snapshot().catalog_statuses
                if item.model_id == manifest.id
            )
            state = write_json(
                directory,
                "install_state.json",
                {
                    "state": "verified" if status.eligible else "failed_unverified",
                    "model_id": manifest.id,
                    "manifest_version": manifest.version,
                    "installed_digest": status.runtime_digest,
                    "identity_matches": status.identity_matches,
                    "eligible": status.eligible,
                    "reason": status.reason.value,
                },
            )
            print(
                f"Installed identity verified: {status.identity_matches}; "
                f"eligible: {status.eligible}"
            )
            print(f"Artifact: {state}")
            return 0 if status.eligible else 1
        finally:
            try:
                await control.aclose()
            finally:
                operation.release()
    broker = await build_real_broker(
        catalog=catalog,
        endpoint=args.endpoint,
        model_store=store,
        allow_mock_fallback=False,
        background_refresh=False,
    )
    try:
        if not isinstance(broker, RealLocalBroker):
            raise InitialInstallError("real_broker_required")
        run_plan = smoke_plan(broker, feasibility=args.command == "feasibility")
        directory = _hardware_artifact(broker.monitor.hardware, args.command)
        path = write_json(
            directory,
            "manifest.json",
            {
                "plan": run_plan.model_dump(mode="json"),
                "approval_digest": run_plan.approval_digest,
                "registry": broker.registry.snapshot().model_dump(mode="json"),
                "evidence": "real",
                "execution": "not_started",
            },
        )
        print(
            f"PLANNED: {run_plan.planned_calls} local requests across two verified models; "
            f"at most {run_plan.planned_calls * 2} provider attempts including connection retries; "
            f"cloud calls 0; total deadline {run_plan.maximum_seconds}s. "
            "No warm-up or force-unload is performed."
        )
        print(f"Plan: {path}")
        if args.plan_only:
            print("PLAN ONLY; no generation executed")
            return 0
        if not _confirm("RUN LOCAL " + run_plan.approval_digest):
            raise InitialInstallError("inference_not_authorized")
        smoke_result = await execute_smoke(broker, run_plan, run_plan.approval_digest)
        path = write_json(directory, "smoke_report.json", smoke_result.model_dump(mode="json"))
        csv_path = write_csv(
            directory,
            [item.model_dump(mode="json") for item in smoke_result.rows],
            tuple(SmokeRow.model_fields),
        )
        successful = sum(item.status == "success" for item in smoke_result.rows)
        checks_passed = sum(item.objective_quality == 1.0 for item in smoke_result.rows)
        print(
            f"Observed local results: {successful}/{run_plan.planned_calls} success; "
            f"objective checks passed: {checks_passed}; stopped: {smoke_result.stopped}; "
            "TTFT unavailable (non-streaming); quality limited to declared synthetic exact checks."
        )
        print(f"Artifact: {path}")
        print(f"Artifact: {csv_path}")
        return (
            0
            if not smoke_result.stopped
            and successful == run_plan.planned_calls
            and checks_passed == run_plan.planned_calls
            else 1
        )
    finally:
        await broker.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "profile-hardware",
            "catalog-status",
            "model-install",
            "real-smoke",
            "feasibility",
        ),
    )
    parser.add_argument("--model", default=os.environ.get("MODEL"))
    parser.add_argument(
        "--model-store",
        type=Path,
        default=Path(os.environ["MODEL_STORE"]) if os.environ.get("MODEL_STORE") else None,
    )
    parser.add_argument(
        "--endpoint", default=os.environ.get("OLLAMA_ENDPOINT") or "http://127.0.0.1:11434"
    )
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Synthetic metadata-only offline check; never real installation/inference",
    )
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    if args.plan_only and args.command not in {"model-install", "real-smoke", "feasibility"}:
        parser.error("--plan-only is supported only for install/smoke/feasibility")
    try:
        return asyncio.run(run(args))
    except InitialInstallError as error:
        print(f"Step 09 blocked: {error.code}", file=sys.stderr)
    except PrivacyError:
        print(
            "Step 09 blocked: local_privacy_asset_missing_or_failed; use explicit owner setup",
            file=sys.stderr,
        )
    except (ConfigError, OSError, ValueError):
        print("Step 09 blocked: invalid_configuration_or_local_resource", file=sys.stderr)
    except Exception:
        print("Step 09 blocked: internal_error", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
