"""Immutable managed-roster views and read-only runtime reconciliation.

This module performs no provider calls or model operations. A supplied inventory
is an observation, not permission to download, unload or remove any model.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from threading import Lock
from typing import Annotated, Literal, NoReturn, Protocol, Self, SupportsIndex
from uuid import UUID, uuid4

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from backend.contracts import ContractModel
from backend.model_management.manifest import (
    Digest,
    Identifier,
    LicenseStatus,
    ModelCatalog,
    Platform,
    RosterEntry,
    RosterSnapshot,
    VerificationStatus,
)
from backend.versions import canonical_hash


class RegistryError(RuntimeError):
    """Safe registry failure with no runtime responses or private input attached."""


class HardwareContext(ContractModel):
    """Explicit observations only; no hardware probing occurs in Step 06."""

    platform: Platform | None = None
    ram_bytes: Annotated[StrictInt, Field(gt=0)] | None = None
    vram_bytes: Annotated[StrictInt, Field(ge=0)] | None = None
    cpu_threads: Annotated[StrictInt, Field(gt=0)] | None = None

    @property
    def version(self) -> str:
        return canonical_hash(
            self.model_dump(mode="json", warnings=False), namespace="hardware-context-v1"
        )


class RuntimeObservation(ContractModel):
    runtime_tag: StrictStr = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*:[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    digest: Digest | None = None
    resident: StrictBool | None = None
    healthy: StrictBool | None = None


class RuntimeInventory(ContractModel):
    available: StrictBool = True
    models: tuple[RuntimeObservation, ...] = Field(default=(), max_length=4096)

    @model_validator(mode="after")
    def check_inventory(self) -> Self:
        if not self.available and self.models:
            raise ValueError("Unavailable runtime cannot claim fresh model observations")
        if len({model.runtime_tag for model in self.models}) != len(self.models):
            raise ValueError("Runtime inventory contains ambiguous model identities")
        return self


class RuntimeInventorySource(Protocol):
    """A future dispatcher/runtime bridge may supply read-only metadata here."""

    def capture(self) -> RuntimeInventory: ...


class EligibilityReason(StrEnum):
    ELIGIBLE = "eligible"
    CATALOG_ONLY = "catalog_only"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    UNVERIFIED = "unverified"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNHEALTHY = "unhealthy_or_unknown"
    PLATFORM_UNKNOWN = "platform_unknown"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    MEMORY_UNKNOWN = "memory_unknown"
    MEMORY_INSUFFICIENT = "memory_insufficient"


class CatalogModelStatus(ContractModel):
    model_id: Identifier
    manifest_version: Digest
    runtime_tag: StrictStr | None = None
    runtime_digest: Digest | None = None
    installed: StrictBool | None = None
    resident: StrictBool | None = None
    healthy: StrictBool | None = None
    verified: StrictBool = False
    identity_matches: StrictBool = False
    eligible: StrictBool = False
    reason: EligibilityReason

    @model_validator(mode="after")
    def check_status(self) -> Self:
        if (self.installed is True) != (self.runtime_tag is not None):
            raise ValueError("Installed status requires a matching runtime observation")
        if self.resident is True and self.installed is not True:
            raise ValueError("Resident status requires an installed observation")
        if self.healthy is True and self.installed is not True:
            raise ValueError("Healthy status requires an installed observation")
        if self.eligible != (self.reason == EligibilityReason.ELIGIBLE):
            raise ValueError("Eligibility reason and status disagree")
        if self.eligible and not (
            self.installed is True and self.healthy is True and self.identity_matches
        ):
            raise ValueError("Eligible model requires installed, healthy, matching identity")
        return self

    @property
    def states(self) -> tuple[str, ...]:
        return (
            "catalogued",
            *(("installed",) if self.installed is True else ()),
            *(("resident",) if self.resident is True else ()),
            *(("unverified",) if not self.verified else ()),
        )


class RegistrySnapshot(ContractModel):
    schema_version: Literal["registry-v1"] = "registry-v1"
    generation: Annotated[StrictInt, Field(ge=0)]
    mock_mode: StrictBool
    runtime_available: StrictBool
    hardware_version: Digest
    hardware: HardwareContext = Field(default_factory=HardwareContext)
    roster: RosterSnapshot
    catalog_statuses: tuple[CatalogModelStatus, ...]
    eligible_model_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        if self.hardware.version != self.hardware_version:
            raise ValueError("Hardware context version mismatch")
        statuses = {status.model_id: status for status in self.catalog_statuses}
        if len(statuses) != len(self.catalog_statuses):
            raise ValueError("Duplicate catalog statuses")
        if len(set(self.eligible_model_ids)) != len(self.eligible_model_ids):
            raise ValueError("Duplicate eligible model IDs")
        expected = {status.model_id for status in self.catalog_statuses if status.eligible}
        if set(self.eligible_model_ids) != expected:
            raise ValueError("Eligible IDs disagree with catalog statuses")
        entries = {entry.manifest.id: entry for entry in self.roster.entries}
        if {status.model_id for status in self.catalog_statuses if status.installed is True} != set(
            entries
        ):
            raise ValueError("Installed catalog statuses must match the managed roster")
        if not expected <= entries.keys():
            raise ValueError("Eligible IDs must appear in the managed roster")
        for model_id, entry in entries.items():
            status = statuses.get(model_id)
            if (
                status is None
                or status.manifest_version != entry.manifest.version
                or status.installed is not True
                or not entry.installed
                or entry.healthy != (status.healthy is True)
                or entry.resident != status.resident
                or status.verified != (entry.manifest.verification == VerificationStatus.VERIFIED)
            ):
                raise ValueError("Roster and catalog observations disagree")
            if status.eligible and not self.mock_mode and not status.verified:
                raise ValueError("Unverified model cannot be eligible in real mode")
            if (
                status.eligible
                and not self.mock_mode
                and (
                    status.runtime_tag != entry.manifest.runtime_tag
                    or status.runtime_digest != entry.manifest.pinned_digest
                    or self.hardware.platform is None
                    or self.hardware.platform not in entry.manifest.platforms
                    or self.hardware.ram_bytes is None
                    or entry.manifest.memory_bytes is None
                    or entry.manifest.memory_bytes > self.hardware.ram_bytes
                    or entry.manifest.license_review.status != LicenseStatus.APPROVED
                )
            ):
                raise ValueError(
                    "Real model eligibility requires compatible observed identity and resources"
                )
        if not self.runtime_available and expected:
            raise ValueError("Unavailable runtime has no eligible models")
        return self

    @property
    def version(self) -> str:
        return canonical_hash(self.model_dump(mode="json", warnings=False), namespace="registry-v1")


class RosterLease:
    """Keep a captured view alive; future lifecycle operations must consult counts."""

    __slots__ = ("_registry", "_identity", "_snapshot", "_model_ids")

    def __init__(
        self,
        registry: "ModelRegistry",
        identity: UUID,
        snapshot: RegistrySnapshot,
        model_ids: tuple[str, ...],
    ) -> None:
        self._registry = registry
        self._identity = identity
        self._snapshot = snapshot
        self._model_ids = model_ids

    @property
    def snapshot(self) -> RegistrySnapshot:
        return self._snapshot

    @property
    def model_ids(self) -> tuple[str, ...]:
        return self._model_ids

    @property
    def active(self) -> bool:
        return self._registry._lease_active(self._identity)

    def release(self) -> None:
        self._registry._release(self._identity)

    def __enter__(self) -> "RosterLease":
        if not self.active:
            raise RegistryError("Roster lease is already released")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    def __repr__(self) -> str:
        return "RosterLease(<snapshot>)"

    def __getstate__(self) -> NoReturn:
        raise TypeError("roster lease serialization is forbidden")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("roster lease serialization is forbidden")


class ModelRegistry:
    def __init__(
        self,
        catalog: ModelCatalog,
        *,
        hardware: HardwareContext | None = None,
        mock_mode: bool = False,
    ) -> None:
        try:
            if not isinstance(mock_mode, bool):
                raise RegistryError("Invalid registry mode")
            self._catalog = ModelCatalog.model_validate(catalog.model_dump(warnings=False))
            if len({model.runtime_tag for model in self._catalog.models}) != len(
                self._catalog.models
            ):
                raise RegistryError("Catalog IDs must identify distinct runtime tags")
            self._hardware = HardwareContext.model_validate(
                (hardware or HardwareContext()).model_dump(warnings=False)
            )
        except (AttributeError, TypeError, ValueError):
            raise RegistryError("Invalid registry configuration") from None
        self._mock_mode = mock_mode
        self._lock = Lock()
        self._leases: dict[UUID, tuple[tuple[str, str], ...]] = {}
        self._snapshot = self._build(RuntimeInventory(available=False), generation=0)

    @classmethod
    def mock(cls, *, installed_ids: tuple[str, ...] | None = None) -> "ModelRegistry":
        from backend.model_management.fixtures import build_mock_catalog

        catalog = build_mock_catalog()
        chosen = (
            tuple(model.id for model in catalog.models[:2])
            if installed_ids is None
            else installed_ids
        )
        if len(chosen) != len(set(chosen)) or not set(chosen) <= {
            model.id for model in catalog.models
        }:
            raise RegistryError("Invalid mock installed model IDs")
        registry = cls(catalog, mock_mode=True)
        registry.reconcile(
            RuntimeInventory(
                models=tuple(
                    RuntimeObservation(runtime_tag=model.runtime_tag, resident=False, healthy=True)
                    for model in catalog.models
                    if model.id in chosen
                )
            )
        )
        return registry

    @property
    def hardware(self) -> HardwareContext:
        return self._hardware

    def snapshot(self) -> RegistrySnapshot:
        with self._lock:
            return self._snapshot

    def _build(self, inventory: RuntimeInventory, *, generation: int) -> RegistrySnapshot:
        observed = {model.runtime_tag: model for model in inventory.models}
        entries: list[RosterEntry] = []
        statuses: list[CatalogModelStatus] = []
        for manifest in sorted(self._catalog.models, key=lambda model: model.id):
            observation = observed.get(manifest.runtime_tag)
            installed = observation is not None if inventory.available else None
            verified = manifest.verification == VerificationStatus.VERIFIED
            matches = observation is not None and (
                self._mock_mode or (verified and observation.digest == manifest.pinned_digest)
            )
            if not inventory.available:
                reason = EligibilityReason.RUNTIME_UNAVAILABLE
            elif observation is None:
                reason = EligibilityReason.CATALOG_ONLY
            elif not self._mock_mode and not verified:
                reason = EligibilityReason.UNVERIFIED
            elif not matches:
                reason = EligibilityReason.IDENTITY_MISMATCH
            elif observation.healthy is not True:
                reason = EligibilityReason.UNHEALTHY
            elif not self._mock_mode and self._hardware.platform is None:
                reason = EligibilityReason.PLATFORM_UNKNOWN
            elif not self._mock_mode and self._hardware.platform not in manifest.platforms:
                reason = EligibilityReason.PLATFORM_UNSUPPORTED
            elif not self._mock_mode and self._hardware.ram_bytes is None:
                reason = EligibilityReason.MEMORY_UNKNOWN
            elif not self._mock_mode and (
                manifest.memory_bytes is None
                or manifest.memory_bytes > (self._hardware.ram_bytes or 0)
                or manifest.license_review.status != LicenseStatus.APPROVED
            ):
                reason = EligibilityReason.MEMORY_INSUFFICIENT
            else:
                reason = EligibilityReason.ELIGIBLE
            status = CatalogModelStatus(
                model_id=manifest.id,
                manifest_version=manifest.version,
                runtime_tag=observation.runtime_tag if observation else None,
                runtime_digest=observation.digest if observation else None,
                installed=installed,
                resident=observation.resident if observation else None,
                healthy=observation.healthy if observation else None,
                verified=verified,
                identity_matches=matches,
                eligible=reason == EligibilityReason.ELIGIBLE,
                reason=reason,
            )
            statuses.append(status)
            if installed:
                entries.append(
                    RosterEntry(
                        manifest=manifest,
                        installed=True,
                        resident=status.resident,
                        healthy=status.healthy is True,
                    )
                )
        if len(entries) > 3:
            raise RegistryError("Observed managed roster exceeds three installed models")
        return RegistrySnapshot(
            generation=generation,
            mock_mode=self._mock_mode,
            runtime_available=inventory.available,
            hardware_version=self._hardware.version,
            hardware=self._hardware,
            roster=RosterSnapshot(entries=tuple(entries)),
            catalog_statuses=tuple(statuses),
            eligible_model_ids=tuple(status.model_id for status in statuses if status.eligible),
        )

    def reconcile(self, inventory: RuntimeInventory) -> RegistrySnapshot:
        try:
            validated = RuntimeInventory.model_validate(inventory.model_dump(warnings=False))
            with self._lock:
                candidate = self._build(validated, generation=self._snapshot.generation + 1)
                self._snapshot = candidate
                return candidate
        except RegistryError:
            raise
        except (AttributeError, TypeError, ValueError):
            raise RegistryError("Invalid runtime inventory") from None

    def refresh(self, source: RuntimeInventorySource) -> RegistrySnapshot:
        try:
            inventory = source.capture()
        except Exception:
            self.reconcile(RuntimeInventory(available=False))
            raise RegistryError("Runtime inventory is unavailable") from None
        return self.reconcile(inventory)

    def lease(
        self,
        model_ids: tuple[str, ...],
        *,
        snapshot: RegistrySnapshot | None = None,
    ) -> RosterLease:
        with self._lock:
            captured = self._snapshot if snapshot is None else snapshot
            if (
                not isinstance(captured, RegistrySnapshot)
                or not isinstance(model_ids, tuple)
                or not all(isinstance(model_id, str) for model_id in model_ids)
                or not model_ids
                or len(model_ids) > 3
                or len(set(model_ids)) != len(model_ids)
                or captured.version != self._snapshot.version
                or not set(model_ids) <= set(captured.eligible_model_ids)
            ):
                raise RegistryError("Roster lease requires current eligible model identities")
            versions = {
                entry.manifest.id: entry.manifest.version for entry in captured.roster.entries
            }
            identity = uuid4()
            self._leases[identity] = tuple((model_id, versions[model_id]) for model_id in model_ids)
            return RosterLease(self, identity, captured, tuple(model_ids))

    @contextmanager
    def leased(self, model_ids: tuple[str, ...]) -> Iterator[RosterLease]:
        lease = self.lease(model_ids)
        try:
            yield lease
        finally:
            lease.release()

    def active_leases(self, model_id: str, manifest_version: str | None = None) -> int:
        with self._lock:
            return sum(
                1
                for identities in self._leases.values()
                if any(
                    identity == model_id
                    and (manifest_version is None or version == manifest_version)
                    for identity, version in identities
                )
            )

    def _lease_active(self, identity: UUID) -> bool:
        with self._lock:
            return identity in self._leases

    def _release(self, identity: UUID) -> None:
        with self._lock:
            self._leases.pop(identity, None)
