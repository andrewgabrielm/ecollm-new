"""Initial owner-approved installs only; no swap, retirement, unload or delete implementation."""

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from pydantic import AwareDatetime, Field, StrictInt

from backend.contracts import ContractModel
from backend.model_management.hardware import HardwareReport, resource_reason
from backend.model_management.manifest import (
    Digest,
    Identifier,
    ModelCatalog,
    ModelManifest,
    VerificationStatus,
)
from backend.model_management.registry import RuntimeInventory
from backend.versions import canonical_hash

ROOT = Path(__file__).resolve().parents[2]


class InitialInstallError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RuntimeLease:
    """One initial-install/real-broker/experiment process per project; crash fails closed."""

    def __init__(self, path: Path = ROOT / "var" / "runtime.lock") -> None:
        self.path = path
        self._identity: tuple[int, int] | None = None

    def acquire(self) -> "RuntimeLease":
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise InitialInstallError("unsafe_operation_path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            raise InitialInstallError("runtime_operation_active_or_stale_lock") from None
        with os.fdopen(descriptor, "w") as output:
            identity = os.fstat(output.fileno())
            self._identity = identity.st_dev, identity.st_ino
            output.write("owned-local-runtime-operation\n")
        return self

    def release(self) -> None:
        try:
            current = self.path.lstat()
            if self._identity == (current.st_dev, current.st_ino):
                self.path.unlink()
        except FileNotFoundError:
            pass
        self._identity = None


def trusted_manifest(catalog: ModelCatalog, model_id: str) -> ModelManifest:
    """Only a reviewed official-library source bound to the runtime manifest digest."""
    try:
        validated = ModelCatalog.model_validate(catalog.model_dump(warnings=False))
        if len({item.runtime_tag for item in validated.models}) != len(validated.models):
            raise InitialInstallError("ambiguous_catalog_runtime_identity")
        manifest = next(model for model in validated.models if model.id == model_id)
        name, tag = manifest.runtime_tag.split(":", 1)
    except (AttributeError, TypeError, ValueError, StopIteration):
        raise InitialInstallError("unknown_catalog_model") from None
    if manifest.verification != VerificationStatus.VERIFIED:
        raise InitialInstallError("unverified_manifest")
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name)
        or manifest.source.uri != f"https://registry.ollama.ai/v2/library/{name}/manifests/{tag}"
        or manifest.source.revision != manifest.pinned_digest
        or any(item.provenance == "synthetic" for item in manifest.capability_benchmark)
    ):
        raise InitialInstallError("untrusted_source_or_digest")
    return manifest


class InstallPlan(ContractModel):
    schema_version: Literal["initial-install-v1"] = "initial-install-v1"
    model_id: Identifier
    manifest_version: Digest
    pinned_digest: Digest
    inventory_version: Digest
    hardware_version: Digest
    model_store_id: Digest
    endpoint: str
    required_disk_bytes: StrictInt = Field(gt=0)
    expires_at: AwareDatetime

    @property
    def approval_digest(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), namespace="initial-install-approval-v1")


def plan_install(
    catalog: ModelCatalog,
    model_id: str,
    hardware: HardwareReport,
    inventory: RuntimeInventory,
    endpoint: str,
) -> InstallPlan:
    from backend.config.endpoints import validate_endpoint

    manifest = trusted_manifest(catalog, model_id)
    hardware = HardwareReport.model_validate(hardware.model_dump(warnings=False))
    inventory = RuntimeInventory.model_validate(inventory.model_dump(warnings=False))
    if hardware.evidence != "observed":
        raise InitialInstallError("synthetic_hardware_forbidden")
    if not inventory.available:
        raise InitialInstallError("runtime_unavailable")
    if reason := resource_reason(manifest, hardware, installation=True):
        raise InitialInstallError(reason)
    known_tags = {model.runtime_tag for model in catalog.models}
    managed = [item for item in inventory.models if item.runtime_tag in known_tags]
    matching = [item for item in managed if item.runtime_tag == manifest.runtime_tag]
    if matching and matching[0].digest != manifest.pinned_digest:
        raise InitialInstallError("existing_model_identity_conflict")
    if len(managed) >= 3 and not matching:
        raise InitialInstallError("managed_model_limit")
    assert (
        manifest.pinned_digest is not None
        and manifest.size_bytes is not None
        and hardware.model_store_id is not None
    )
    return InstallPlan(
        model_id=model_id,
        manifest_version=manifest.version,
        pinned_digest=manifest.pinned_digest,
        inventory_version=canonical_hash(
            inventory.model_dump(mode="json"), namespace="runtime-inventory-v1"
        ),
        hardware_version=hardware.context.version,
        model_store_id=hardware.model_store_id,
        endpoint=validate_endpoint(endpoint, local=True),
        required_disk_bytes=2 * manifest.size_bytes + 1024**3,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


class InitialInstallControl(Protocol):
    async def install_authorized(
        self,
        manifest: ModelManifest,
        plan: InstallPlan,
        approval_digest: str,
        hardware: HardwareReport,
    ) -> RuntimeInventory: ...
