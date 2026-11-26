"""Step 17 safe model lifecycle tests — mock RuntimeControl, no real I/O."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from backend.contracts import ErrorCode, TaskCategory
from backend.model_management.lifecycle import (
    SwapCoordinator,
    SwapError,
    SwapOperation,
    SwapProposal,
)
from backend.model_management.manifest import (
    BenchmarkProvenance,
    CapabilityBenchmark,
    LicenseReview,
    LicenseStatus,
    ManifestSource,
    ModelCatalog,
    ModelManifest,
    Platform,
    RosterEntry,
    VerificationStatus,
)
from backend.model_management.registry import ModelRegistry, RuntimeInventory, RuntimeObservation
from backend.providers.base import RuntimeModel, RuntimeOperationResult


@dataclass
class _MockRuntime:
    pulled: list[str] = field(default_factory=list)
    unloaded: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    pull_outcome: str = "success"
    unload_outcome: str = "success"
    remove_outcome: str = "success"

    async def list_models(self) -> tuple[RuntimeModel, ...]:
        return ()

    async def pull(self, manifest_id: str, pinned_digest: str) -> RuntimeOperationResult:
        self.pulled.append(manifest_id)
        return _result(self.pull_outcome, manifest_id)

    async def unload(self, model_id: str) -> RuntimeOperationResult:
        self.unloaded.append(model_id)
        return _result(self.unload_outcome, model_id)

    async def remove(self, model_id: str) -> RuntimeOperationResult:
        self.removed.append(model_id)
        return _result(self.remove_outcome, model_id)


def _result(outcome: str, model_id: str) -> RuntimeOperationResult:
    if outcome == "success":
        return RuntimeOperationResult(status="success", model_id=model_id)
    return RuntimeOperationResult(
        status="error",
        model_id=model_id,
        error_code=ErrorCode.UNAVAILABLE,
    )


def _verified_manifest(name: str) -> ModelManifest:
    return ModelManifest(
        id=name,
        pinned_digest="a" * 64,
        source=ManifestSource(uri=f"https://example.invalid/{name}", revision="b" * 40),
        runtime_tag=f"{name}:fixture",
        family="invented-routing-fixture",
        license_review=LicenseReview(
            status=LicenseStatus.APPROVED,
            identifier="synthetic-license-id",
            reference=ManifestSource(uri="https://example.invalid/license"),
        ),
        quantization="fixture-q4",
        capability_benchmark=tuple(
            CapabilityBenchmark(
                task=task,
                provenance=BenchmarkProvenance.SYNTHETIC,
                quality=0.5,
                latency_ms=1000.0,
                benchmark_version="b" * 64,
            )
            for task in TaskCategory
        ),
        size_bytes=1024 * 1024 * 1024,
        memory_bytes=512 * 1024 * 1024,
        platforms=(Platform.MACOS_ARM64,),
        verification=VerificationStatus.VERIFIED,
    )


def _unverified_manifest(name: str) -> ModelManifest:
    return ModelManifest(
        id=name,
        source=ManifestSource(uri=f"https://example.invalid/{name}"),
        runtime_tag=f"{name}:unverified",
        family="invented-routing-fixture",
        quantization="unknown",
        capability_benchmark=(),
        verification=VerificationStatus.UNVERIFIED,
    )


def _registry_with(
    *verifications: VerificationStatus,
) -> tuple[ModelRegistry, tuple[RosterEntry, ...]]:
    manifests: list[ModelManifest] = []
    for idx, status in enumerate(verifications):
        name = f"mock-swap-{idx}"
        if status == VerificationStatus.VERIFIED:
            manifests.append(_verified_manifest(name))
        else:
            manifests.append(_unverified_manifest(name))
    catalog = ModelCatalog(models=tuple(manifests))
    registry = ModelRegistry(catalog, mock_mode=True)
    registry.reconcile(
        RuntimeInventory(
            models=tuple(
                RuntimeObservation(
                    runtime_tag=manifest.runtime_tag,
                    resident=False,
                    healthy=True,
                )
                for manifest in manifests
            )
        )
    )
    entries = registry.snapshot().roster.entries
    return registry, entries


def _mock_registry() -> tuple[ModelRegistry, tuple[RosterEntry, ...]]:
    return _registry_with(
        VerificationStatus.VERIFIED,
        VerificationStatus.VERIFIED,
        VerificationStatus.VERIFIED,
    )


def _coordinator() -> tuple[SwapCoordinator, _MockRuntime, tuple[RosterEntry, ...]]:
    registry, entries = _mock_registry()
    runtime = _MockRuntime()
    coordinator = SwapCoordinator(registry, runtime, now_factory=lambda: 10.0)
    return coordinator, runtime, entries


class TestProposal:
    def test_fresh_proposal_is_bound(self) -> None:
        coordinator, _, entries = _coordinator()
        proposal = coordinator.propose(
            target_manifest=entries[2],
            replaced_model_id=entries[1].manifest.id,
            delete_replaced_files=False,
            available_disk_bytes=10 * 1024 * 1024 * 1024,
            minimum_disk_bytes=4 * 1024 * 1024 * 1024,
        )
        assert isinstance(proposal, SwapProposal)
        assert proposal.target_model_id == entries[2].manifest.id
        state = coordinator.get(proposal.proposal_id)
        assert state.operation == SwapOperation.PROPOSAL

    def test_low_disk_rejects_proposal(self) -> None:
        coordinator, _, entries = _coordinator()
        with pytest.raises(SwapError):
            coordinator.propose(
                target_manifest=entries[2],
                replaced_model_id=entries[1].manifest.id,
                delete_replaced_files=False,
                available_disk_bytes=0,
                minimum_disk_bytes=1024,
            )

    def test_unverified_target_rejected(self) -> None:
        registry, entries = _registry_with(
            VerificationStatus.VERIFIED,
            VerificationStatus.VERIFIED,
            VerificationStatus.UNVERIFIED,
        )
        coordinator = SwapCoordinator(registry, _MockRuntime(), now_factory=lambda: 10.0)
        with pytest.raises(SwapError):
            coordinator.propose(
                target_manifest=entries[2],
                replaced_model_id=entries[1].manifest.id,
                delete_replaced_files=False,
                available_disk_bytes=10 * 1024 * 1024 * 1024,
                minimum_disk_bytes=1024,
            )

    def test_external_model_cannot_be_target(self) -> None:
        coordinator, _, entries = _coordinator()
        # Constructing a manifest NOT in the mock catalog simulates an
        # external runtime model that must never enter a managed proposal.
        alien = RosterEntry(
            manifest=_verified_manifest("alien-external"),
            installed=False,
            healthy=False,
        )
        with pytest.raises(SwapError):
            coordinator.propose(
                target_manifest=alien,
                replaced_model_id=entries[1].manifest.id,
                delete_replaced_files=False,
                available_disk_bytes=10 * 1024 * 1024 * 1024,
                minimum_disk_bytes=1024,
            )


class TestPipelineSuccess:
    def test_staged_success_end_to_end(self) -> None:
        coordinator, runtime, entries = _coordinator()
        proposal = coordinator.propose(
            target_manifest=entries[2],
            replaced_model_id=entries[1].manifest.id,
            delete_replaced_files=False,
            available_disk_bytes=10 * 1024 * 1024 * 1024,
            minimum_disk_bytes=1024,
        )
        coordinator.approve(proposal.proposal_id, approval_token="synthetic-approval-token")
        asyncio.run(coordinator.stage(proposal.proposal_id))
        coordinator.warm(proposal.proposal_id, succeeded=True)
        coordinator.activate(proposal.proposal_id)
        asyncio.run(coordinator.drain(proposal.proposal_id))
        final = asyncio.run(coordinator.retire(proposal.proposal_id))
        assert final.operation == SwapOperation.RETIRED
        assert runtime.pulled == [entries[2].manifest.id]
        assert runtime.unloaded == [entries[1].manifest.id]
        # delete_replaced_files was False → runtime.remove must not run.
        assert runtime.removed == []

    def test_delete_files_flag_removes_only_when_authorized(self) -> None:
        coordinator, runtime, entries = _coordinator()
        proposal = coordinator.propose(
            target_manifest=entries[2],
            replaced_model_id=entries[1].manifest.id,
            delete_replaced_files=True,
            available_disk_bytes=10 * 1024 * 1024 * 1024,
            minimum_disk_bytes=1024,
        )
        coordinator.approve(proposal.proposal_id, approval_token="synthetic-approval-token")
        asyncio.run(coordinator.stage(proposal.proposal_id))
        coordinator.warm(proposal.proposal_id, succeeded=True)
        coordinator.activate(proposal.proposal_id)
        asyncio.run(coordinator.drain(proposal.proposal_id))
        asyncio.run(coordinator.retire(proposal.proposal_id))
        assert runtime.removed == [entries[1].manifest.id]


class TestFailurePaths:
    def test_runtime_pull_failure_marks_failed(self) -> None:
        registry, entries = _mock_registry()
        runtime = _MockRuntime(pull_outcome="error")
        coordinator = SwapCoordinator(registry, runtime, now_factory=lambda: 10.0)
        proposal = coordinator.propose(
            target_manifest=entries[2],
            replaced_model_id=entries[1].manifest.id,
            delete_replaced_files=False,
            available_disk_bytes=10 * 1024 * 1024 * 1024,
            minimum_disk_bytes=1024,
        )
        coordinator.approve(proposal.proposal_id, approval_token="synthetic-approval-token")
        with pytest.raises(SwapError):
            asyncio.run(coordinator.stage(proposal.proposal_id))
        assert coordinator.get(proposal.proposal_id).operation == SwapOperation.FAILED

    def test_warm_up_failure_marks_failed(self) -> None:
        coordinator, _, entries = _coordinator()
        proposal = coordinator.propose(
            target_manifest=entries[2],
            replaced_model_id=entries[1].manifest.id,
            delete_replaced_files=False,
            available_disk_bytes=10 * 1024 * 1024 * 1024,
            minimum_disk_bytes=1024,
        )
        coordinator.approve(proposal.proposal_id, approval_token="synthetic-approval-token")
        asyncio.run(coordinator.stage(proposal.proposal_id))
        with pytest.raises(SwapError):
            coordinator.warm(proposal.proposal_id, succeeded=False)
        assert coordinator.get(proposal.proposal_id).operation == SwapOperation.FAILED

    def test_no_approval_no_runtime_call(self) -> None:
        coordinator, runtime, entries = _coordinator()
        proposal = coordinator.propose(
            target_manifest=entries[2],
            replaced_model_id=entries[1].manifest.id,
            delete_replaced_files=False,
            available_disk_bytes=10 * 1024 * 1024 * 1024,
            minimum_disk_bytes=1024,
        )
        # Skip approve: stage must refuse.
        with pytest.raises(SwapError):
            asyncio.run(coordinator.stage(proposal.proposal_id))
        assert runtime.pulled == []

    def test_cannot_retire_external_model(self) -> None:
        coordinator, _, entries = _coordinator()
        proposal = coordinator.propose(
            target_manifest=entries[2],
            replaced_model_id=entries[1].manifest.id,
            delete_replaced_files=False,
            available_disk_bytes=10 * 1024 * 1024 * 1024,
            minimum_disk_bytes=1024,
        )
        coordinator.approve(proposal.proposal_id, approval_token="synthetic-approval-token")
        asyncio.run(coordinator.stage(proposal.proposal_id))
        coordinator.warm(proposal.proposal_id, succeeded=True)
        coordinator.activate(proposal.proposal_id)
        asyncio.run(coordinator.drain(proposal.proposal_id))
        # Tamper the state in memory to point at an external model.
        state = coordinator.get(proposal.proposal_id)
        tampered = state.proposal.model_copy(update={"replaced_model_id": "alien-external"})
        new_state = state.__class__(
            proposal=tampered,
            operation=state.operation,
            audit=state.audit,
            runtime_identity_verified=state.runtime_identity_verified,
            warm_up_passed=state.warm_up_passed,
            activated_manifest_version=state.activated_manifest_version,
        )
        coordinator._replace(new_state)
        with pytest.raises(SwapError):
            asyncio.run(coordinator.retire(proposal.proposal_id))


class TestCancelAndRollback:
    def test_cancel_before_activation(self) -> None:
        coordinator, _, entries = _coordinator()
        proposal = coordinator.propose(
            target_manifest=entries[2],
            replaced_model_id=entries[1].manifest.id,
            delete_replaced_files=False,
            available_disk_bytes=10 * 1024 * 1024 * 1024,
            minimum_disk_bytes=1024,
        )
        coordinator.approve(proposal.proposal_id, approval_token="synthetic-approval-token")
        asyncio.run(coordinator.stage(proposal.proposal_id))
        coordinator.cancel(proposal.proposal_id, reason="owner_declined")
        final = coordinator.get(proposal.proposal_id)
        assert final.operation == SwapOperation.CANCELLED

    def test_rollback_from_active(self) -> None:
        coordinator, _, entries = _coordinator()
        proposal = coordinator.propose(
            target_manifest=entries[2],
            replaced_model_id=entries[1].manifest.id,
            delete_replaced_files=False,
            available_disk_bytes=10 * 1024 * 1024 * 1024,
            minimum_disk_bytes=1024,
        )
        coordinator.approve(proposal.proposal_id, approval_token="synthetic-approval-token")
        asyncio.run(coordinator.stage(proposal.proposal_id))
        coordinator.warm(proposal.proposal_id, succeeded=True)
        coordinator.activate(proposal.proposal_id)
        rolled = coordinator.rollback(proposal.proposal_id)
        assert rolled.operation == SwapOperation.CANCELLED
