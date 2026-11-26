"""Read-only registry contracts with invented runtime/model observations only."""

import pickle
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from pydantic import ValidationError

from backend.model_management.fixtures import build_mock_catalog
from backend.model_management.manifest import ModelCatalog, ModelManifest, Platform
from backend.model_management.registry import (
    EligibilityReason,
    HardwareContext,
    ModelRegistry,
    RegistryError,
    RegistrySnapshot,
    RuntimeInventory,
    RuntimeObservation,
)


def verified_catalog() -> ModelCatalog:
    """Structural verification fixtures only; no installed or reviewed real model claim."""
    data = build_mock_catalog().models[0].model_dump(mode="json")
    data.update(
        pinned_digest="a" * 64,
        source={"uri": "https://example.invalid/synthetic-source", "revision": "b" * 40},
        license_review={
            "status": "approved",
            "identifier": "INVENTED-TEST-LICENSE",
            "reference": {"uri": "https://example.invalid/synthetic-license"},
        },
        verification="verified",
        size_bytes=100,
        memory_bytes=128,
        platforms=["linux-x86_64"],
    )
    return ModelCatalog(models=(ModelManifest.model_validate(data),))


def inventory(catalog: ModelCatalog, **changes: Any) -> RuntimeInventory:
    return RuntimeInventory(
        models=tuple(
            RuntimeObservation.model_validate(
                {
                    "runtime_tag": manifest.runtime_tag,
                    "digest": manifest.pinned_digest,
                    "healthy": True,
                    **changes,
                }
            )
            for manifest in catalog.models
        )
    )


def real_registry(*, hardware: HardwareContext | None = None) -> ModelRegistry:
    return ModelRegistry(
        verified_catalog(),
        hardware=hardware or HardwareContext(platform=Platform.LINUX_X86_64, ram_bytes=1024),
    )


def test_default_mock_roster_is_two_of_three_explicit_unverified_fixtures() -> None:
    registry = ModelRegistry.mock()
    snapshot = registry.snapshot()
    assert snapshot.mock_mode
    assert snapshot.eligible_model_ids == ("mock-general", "mock-specialist")
    assert len(snapshot.roster.entries) == 2
    assert len(snapshot.catalog_statuses) == 3
    statuses = {status.model_id: status for status in snapshot.catalog_statuses}
    assert statuses["mock-general"].states == ("catalogued", "installed", "unverified")
    assert statuses["mock-third"].states == ("catalogued", "unverified")
    assert statuses["mock-third"].installed is False
    assert all(not status.verified for status in snapshot.catalog_statuses)
    assert all(model.manifest.pinned_digest is None for model in snapshot.roster.entries)
    with pytest.raises(ValidationError):
        snapshot.mock_mode = False


def test_unknown_runtime_is_distinct_from_empty_runtime_and_unknown_residency() -> None:
    registry = real_registry()
    unknown = registry.snapshot()
    assert not unknown.runtime_available
    assert unknown.catalog_statuses[0].installed is None
    assert not unknown.eligible_model_ids
    empty = registry.reconcile(RuntimeInventory())
    assert empty.catalog_statuses[0].installed is False
    observed = registry.reconcile(inventory(verified_catalog()))
    assert observed.roster.entries[0].resident is None
    assert observed.catalog_statuses[0].resident is None
    assert observed.eligible_model_ids == ("mock-general",)
    resident = registry.reconcile(inventory(verified_catalog(), resident=True))
    assert "resident" in resident.catalog_statuses[0].states
    assert resident.roster.entries[0].resident is True


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"digest": "c" * 64}, EligibilityReason.IDENTITY_MISMATCH),
        ({"digest": None}, EligibilityReason.IDENTITY_MISMATCH),
        ({"runtime_tag": "other:fixture"}, EligibilityReason.CATALOG_ONLY),
        ({"healthy": False}, EligibilityReason.UNHEALTHY),
        ({"healthy": None}, EligibilityReason.UNHEALTHY),
    ],
)
def test_real_eligibility_requires_exact_observed_identity_and_health(
    changes: dict[str, object],
    expected: EligibilityReason,
) -> None:
    snapshot = real_registry().reconcile(inventory(verified_catalog(), **changes))
    assert not snapshot.eligible_model_ids
    assert snapshot.catalog_statuses[0].reason == expected


@pytest.mark.parametrize(
    ("hardware", "reason"),
    [
        (HardwareContext(), EligibilityReason.PLATFORM_UNKNOWN),
        (
            HardwareContext(platform=Platform.MACOS_ARM64, ram_bytes=1024),
            EligibilityReason.PLATFORM_UNSUPPORTED,
        ),
        (HardwareContext(platform=Platform.LINUX_X86_64), EligibilityReason.MEMORY_UNKNOWN),
        (
            HardwareContext(platform=Platform.LINUX_X86_64, ram_bytes=64),
            EligibilityReason.MEMORY_INSUFFICIENT,
        ),
    ],
)
def test_unknown_or_incompatible_hardware_is_not_assumed_eligible(
    hardware: HardwareContext,
    reason: EligibilityReason,
) -> None:
    snapshot = real_registry(hardware=hardware).reconcile(inventory(verified_catalog()))
    assert snapshot.catalog_statuses[0].reason == reason
    assert not snapshot.eligible_model_ids


def test_unverified_catalog_never_becomes_real_eligible_merely_by_being_installed() -> None:
    catalog = build_mock_catalog()
    registry = ModelRegistry(catalog)
    snapshot = registry.reconcile(inventory(catalog))
    assert len(snapshot.roster.entries) == 3
    assert not snapshot.eligible_model_ids
    assert all(
        status.reason == EligibilityReason.UNVERIFIED for status in snapshot.catalog_statuses
    )


def test_external_models_are_ignored_and_no_runtime_operations_are_exposed() -> None:
    registry = real_registry()
    models = inventory(verified_catalog()).models + tuple(
        RuntimeObservation(runtime_tag=f"external-{index}:fixture", digest="d" * 64)
        for index in range(10)
    )
    snapshot = registry.reconcile(RuntimeInventory(models=models))
    assert len(snapshot.roster.entries) == 1
    assert len(snapshot.catalog_statuses) == 1
    assert not any(hasattr(registry, method) for method in ("pull", "unload", "remove", "generate"))


def test_fourth_managed_install_is_rejected_atomically() -> None:
    catalog = build_mock_catalog()
    fourth = ModelManifest.model_validate(
        {
            **catalog.models[0].model_dump(),
            "id": "mock-fourth",
            "runtime_tag": "mock-fourth:fixture",
        }
    )
    registry = ModelRegistry(ModelCatalog(models=(*catalog.models, fourth)), mock_mode=True)
    previous = registry.snapshot()
    with pytest.raises(RegistryError, match="three installed"):
        registry.reconcile(inventory(ModelCatalog(models=(*catalog.models, fourth))))
    assert registry.snapshot() is previous


def test_failed_reader_marks_current_runtime_unavailable_without_echoing_source() -> None:
    class FailedSource:
        def capture(self) -> RuntimeInventory:
            raise RuntimeError("invented-private-runtime-marker")

    registry = real_registry()
    previous = registry.reconcile(inventory(verified_catalog()))
    with pytest.raises(RegistryError) as caught:
        registry.refresh(FailedSource())
    assert "invented-private" not in str(caught.value)
    assert not registry.snapshot().runtime_available
    assert not registry.snapshot().eligible_model_ids
    assert previous.eligible_model_ids == ("mock-general",)


def test_read_only_metadata_source_and_snapshot_roundtrip() -> None:
    class Source:
        def capture(self) -> RuntimeInventory:
            return inventory(verified_catalog())

    registry = real_registry()
    snapshot = registry.refresh(Source())
    assert (
        RegistrySnapshot.model_validate_json(snapshot.model_dump_json()).version == snapshot.version
    )
    assert snapshot.hardware.version == snapshot.hardware_version


def test_lease_preserves_captured_version_until_explicit_idempotent_release() -> None:
    registry = ModelRegistry.mock()
    old = registry.snapshot()
    model_id = old.eligible_model_ids[0]
    version = old.roster.entries[0].manifest.version
    lease = registry.lease((model_id,), snapshot=old)
    assert lease.active
    assert registry.active_leases(model_id, version) == 1
    registry.reconcile(RuntimeInventory())
    assert lease.snapshot is old
    assert model_id in lease.snapshot.eligible_model_ids
    assert registry.active_leases(model_id, version) == 1
    with pytest.raises(RegistryError):
        registry.lease((model_id,), snapshot=old)
    with pytest.raises(TypeError):
        pickle.dumps(lease)
    lease.release()
    lease.release()
    assert not lease.active
    assert registry.active_leases(model_id, version) == 0


def test_lease_context_manager_releases_on_exception_and_concurrent_users() -> None:
    registry = ModelRegistry.mock()
    model_id = registry.snapshot().eligible_model_ids[0]
    with pytest.raises(RuntimeError), registry.leased((model_id,)):
        assert registry.active_leases(model_id) == 1
        raise RuntimeError("synthetic")
    assert registry.active_leases(model_id) == 0

    def use_lease(_: int) -> bool:
        with registry.leased((model_id,)) as lease:
            return lease.active

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(use_lease, range(20)))
    assert registry.active_leases(model_id) == 0


@pytest.mark.parametrize("model_ids", [(), ("unknown",), ("mock-general", "mock-general")])
def test_invalid_lease_identities_are_rejected(model_ids: tuple[str, ...]) -> None:
    with pytest.raises(RegistryError):
        ModelRegistry.mock().lease(model_ids)


def test_inventory_and_snapshot_tampering_cannot_forge_eligibility() -> None:
    observation = RuntimeObservation(runtime_tag="mock-general:fixture")
    with pytest.raises(ValidationError):
        RuntimeInventory(models=(observation, observation))
    with pytest.raises(ValidationError):
        RuntimeInventory(available=False, models=(observation,))
    with pytest.raises(ValidationError):
        RuntimeInventory.model_validate({"models": [], "prompt": "invented-private-marker"})
    registry = ModelRegistry.mock()
    previous = registry.snapshot()
    with pytest.raises(RegistryError):
        registry.reconcile(RuntimeInventory.model_construct(available="yes", models=()))
    assert registry.snapshot() is previous
    with pytest.raises(ValidationError):
        RegistrySnapshot.model_validate(
            {**previous.model_dump(), "eligible_model_ids": ("unknown",)}
        )
    with pytest.raises(ValidationError):
        RegistrySnapshot.model_validate({**previous.model_dump(), "hardware_version": "a" * 64})


def test_snapshot_cannot_promote_a_resource_or_identity_blocked_model() -> None:
    blocked = real_registry(
        hardware=HardwareContext(platform=Platform.LINUX_X86_64, ram_bytes=64)
    ).reconcile(inventory(verified_catalog()))
    data = blocked.model_dump()
    data["catalog_statuses"][0].update(eligible=True, reason="eligible")
    data["eligible_model_ids"] = ("mock-general",)
    with pytest.raises(ValidationError):
        RegistrySnapshot.model_validate(data)
    eligible = real_registry().reconcile(inventory(verified_catalog())).model_dump()
    eligible["catalog_statuses"][0]["runtime_digest"] = "c" * 64
    with pytest.raises(ValidationError):
        RegistrySnapshot.model_validate(eligible)


def test_two_catalog_ids_cannot_alias_one_runtime_identity() -> None:
    catalog = verified_catalog()
    alias = ModelManifest.model_validate({**catalog.models[0].model_dump(), "id": "other-id"})
    with pytest.raises(RegistryError, match="distinct runtime tags"):
        ModelRegistry(ModelCatalog(models=(*catalog.models, alias)))
