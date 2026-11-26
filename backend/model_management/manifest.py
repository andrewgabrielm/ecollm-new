"""Validated catalog identities and immutable roster/swap contracts; no runtime I/O."""

import re
from enum import StrEnum
from typing import Annotated, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, StrictStr, model_validator

from backend.contracts import ContractModel, TaskCategory
from backend.versions import POLICY_VERSION, canonical_hash

Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Digest = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
PositiveBytes = Annotated[StrictInt, Field(gt=0)]


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"


class LicenseStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class Platform(StrEnum):
    MACOS_ARM64 = "macos-arm64"
    MACOS_X86_64 = "macos-x86_64"
    LINUX_X86_64 = "linux-x86_64"
    LINUX_ARM64 = "linux-arm64"
    WINDOWS_X86_64 = "windows-x86_64"


class BenchmarkProvenance(StrEnum):
    UNKNOWN = "unknown"
    SYNTHETIC = "synthetic"
    MEASURED = "measured"


class CapabilityBenchmark(ContractModel):
    task: TaskCategory
    provenance: BenchmarkProvenance = BenchmarkProvenance.UNKNOWN
    quality: float | None = Field(default=None, ge=0, le=1, strict=True)
    latency_ms: float | None = Field(default=None, ge=0, strict=True)
    benchmark_version: Digest | None = None

    @model_validator(mode="after")
    def check_measurement(self) -> Self:
        has_values = self.quality is not None or self.latency_ms is not None
        if self.provenance == BenchmarkProvenance.UNKNOWN and (
            has_values or self.benchmark_version is not None
        ):
            raise ValueError("Unknown benchmark cannot claim observations")
        if self.provenance != BenchmarkProvenance.UNKNOWN and (
            not has_values or self.benchmark_version is None
        ):
            raise ValueError("Benchmark observations require a version and provenance")
        return self


class ManifestSource(ContractModel):
    uri: StrictStr
    revision: Annotated[StrictStr, Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")] | None = None

    @model_validator(mode="after")
    def check_source(self) -> Self:
        try:
            parsed = urlsplit(self.uri)
            hostname = parsed.hostname or ""
            valid = (
                parsed.scheme == "https"
                and not any(
                    char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self.uri
                )
                and "\\" not in self.uri
                and re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*",
                    hostname,
                )
                is not None
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and parsed.port in {None, 443}
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Manifest source must be a credential-free HTTPS source")
        return self


class LicenseReview(ContractModel):
    status: LicenseStatus = LicenseStatus.UNREVIEWED
    identifier: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    reference: ManifestSource | None = None

    @model_validator(mode="after")
    def check_review(self) -> Self:
        if self.status == LicenseStatus.APPROVED and (
            self.identifier is None or self.reference is None
        ):
            raise ValueError("Approved license review requires identity and reference")
        return self


class ModelManifest(ContractModel):
    id: Identifier
    pinned_digest: Digest | None = None
    source: ManifestSource
    runtime_tag: Annotated[
        StrictStr,
        Field(
            pattern=r"^[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*:[a-zA-Z0-9][a-zA-Z0-9._-]*$"
        ),
    ]
    family: StrictStr = Field(min_length=1, max_length=128)
    license_review: LicenseReview = Field(default_factory=LicenseReview)
    quantization: StrictStr = Field(min_length=1, max_length=64)
    capability_benchmark: tuple[CapabilityBenchmark, ...] = ()
    size_bytes: PositiveBytes | None = None
    memory_bytes: PositiveBytes | None = None
    platforms: tuple[Platform, ...] = ()
    verification: VerificationStatus = VerificationStatus.UNVERIFIED

    @model_validator(mode="after")
    def check_verified_identity(self) -> Self:
        if len({entry.task for entry in self.capability_benchmark}) != len(
            self.capability_benchmark
        ):
            raise ValueError("Duplicate capability categories")
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("Duplicate supported platforms")
        if self.verification == VerificationStatus.VERIFIED and (
            self.pinned_digest is None
            or self.source.revision is None
            or self.license_review.status != LicenseStatus.APPROVED
            or self.quantization == "unknown"
            or self.size_bytes is None
            or self.memory_bytes is None
            or not self.platforms
        ):
            raise ValueError("Verified manifest requires pinned identity and reviewed resources")
        return self

    @property
    def version(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), namespace="model-manifest-v1")


class ModelCatalog(ContractModel):
    schema_version: StrictStr = "catalog-v1"
    models: tuple[ModelManifest, ...]

    @model_validator(mode="after")
    def check_catalog(self) -> Self:
        if self.schema_version != "catalog-v1":
            raise ValueError("Unsupported catalog schema")
        if len({model.id for model in self.models}) != len(self.models):
            raise ValueError("Duplicate catalog IDs")
        return self

    def eligible_manifests(self, *, mock_mode: bool) -> tuple[ModelManifest, ...]:
        """Identity eligibility only; installed/health/resource checks belong to the registry."""
        return tuple(
            model
            for model in self.models
            if mock_mode or model.verification == VerificationStatus.VERIFIED
        )


class RosterEntry(ContractModel):
    manifest: ModelManifest
    installed: StrictBool = False
    resident: StrictBool | None = False
    healthy: StrictBool = False

    @model_validator(mode="after")
    def check_residency(self) -> Self:
        if (self.resident or self.healthy) and not self.installed:
            raise ValueError("Resident or healthy roster entry must be installed")
        return self


class RosterSnapshot(ContractModel):
    entries: tuple[RosterEntry, ...] = Field(default=(), max_length=3)
    schema_version: StrictStr = "roster-v1"

    @model_validator(mode="after")
    def check_roster(self) -> Self:
        if self.schema_version != "roster-v1":
            raise ValueError("Unsupported roster schema")
        if len({entry.manifest.id for entry in self.entries}) != len(self.entries):
            raise ValueError("Duplicate roster IDs")
        return self

    @property
    def version(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), namespace="roster-v1")


class SwapReason(StrEnum):
    MANUAL = "manual"
    WORKLOAD_SHIFT = "workload_shift"


class SwapProposal(ContractModel):
    proposal_id: UUID
    install_manifest: ModelManifest
    expected_roster_version: Digest
    retire_model_ids: tuple[Identifier, ...] = Field(default=(), max_length=3)
    delete_retired: StrictBool = False
    disk_headroom_bytes: Annotated[StrictInt, Field(ge=0)]
    required_staging_bytes: PositiveBytes
    policy_version: StrictStr = POLICY_VERSION
    reason: SwapReason = SwapReason.MANUAL
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def check_proposal(self) -> Self:
        if self.install_manifest.verification != VerificationStatus.VERIFIED:
            raise ValueError("Swap proposal requires a verified manifest")
        if len(set(self.retire_model_ids)) != len(self.retire_model_ids):
            raise ValueError("Duplicate retirement IDs")
        if self.install_manifest.id in self.retire_model_ids:
            raise ValueError("Install target cannot also be retired")
        if self.delete_retired and not self.retire_model_ids:
            raise ValueError("Deletion intent requires retirement IDs")
        if self.disk_headroom_bytes < self.required_staging_bytes:
            raise ValueError("Insufficient staging disk headroom")
        if self.policy_version != POLICY_VERSION:
            raise ValueError("Unsupported swap policy version")
        return self

    @property
    def version(self) -> str:
        return canonical_hash(self.model_dump(mode="json"), namespace="swap-proposal-v1")
