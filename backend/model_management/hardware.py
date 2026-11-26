"""Owner-invoked observations and pure conservative resource policy; no import-time probe."""

import importlib
import json
import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast

from pydantic import AwareDatetime, Field, StrictInt, StrictStr, model_validator

from backend.contracts import ContractModel
from backend.model_management.manifest import ModelManifest, Platform
from backend.model_management.registry import HardwareContext
from backend.versions import canonical_hash

GIB = 1024**3
Bytes = Annotated[StrictInt, Field(ge=0)]


class Accelerator(ContractModel):
    kind: Literal["metal", "cuda"]
    name: StrictStr = Field(min_length=1, max_length=128)
    memory_bytes: Bytes | None = None


class HardwareReport(ContractModel):
    schema_version: Literal["hardware-report-v1"] = "hardware-report-v1"
    evidence: Literal["observed", "synthetic"] = "observed"
    observed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    architecture: Literal["arm64", "x86_64", "unknown"] = "unknown"
    context: HardwareContext = Field(default_factory=HardwareContext)
    available_ram_bytes: Bytes | None = None
    project_free_bytes: Bytes | None = None
    model_store_free_bytes: Bytes | None = None
    model_store_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    accelerators: tuple[Accelerator, ...] = ()
    accelerator_status: Literal["observed", "unknown"] = "unknown"

    @model_validator(mode="after")
    def check_observations(self) -> "HardwareReport":
        if (
            self.available_ram_bytes is not None
            and self.context.ram_bytes is not None
            and self.available_ram_bytes > self.context.ram_bytes
        ):
            raise ValueError("Available memory exceeds physical memory")
        if self.model_store_free_bytes is not None and self.model_store_id is None:
            raise ValueError("Model-store observation requires an owner-selected store")
        return self

    @property
    def pressure(self) -> Literal["normal", "low_memory", "unknown"]:
        if self.context.ram_bytes is None or self.available_ram_bytes is None:
            return "unknown"
        return (
            "low_memory"
            if self.available_ram_bytes < max(GIB, self.context.ram_bytes // 10)
            else "normal"
        )


class _Memory(Protocol):
    total: int
    available: int


class _Stats(Protocol):
    def virtual_memory(self) -> _Memory: ...
    def cpu_count(self) -> int | None: ...


def platform_context(system: str, architecture: str) -> tuple[Platform | None, str]:
    arch = {"aarch64": "arm64", "arm64": "arm64", "AMD64": "x86_64", "x86_64": "x86_64"}.get(
        architecture, "unknown"
    )
    selected = {
        ("Darwin", "arm64"): Platform.MACOS_ARM64,
        ("Darwin", "x86_64"): Platform.MACOS_X86_64,
        ("Linux", "arm64"): Platform.LINUX_ARM64,
        ("Linux", "x86_64"): Platform.LINUX_X86_64,
        ("Windows", "x86_64"): Platform.WINDOWS_X86_64,
    }.get((system, arch))
    return selected, arch


def _command(arguments: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0 and len(result.stdout) <= 1_048_576:
            return result.stdout
    except (OSError, subprocess.SubprocessError, UnicodeError):
        pass
    return None


def accelerator_observations(system: str) -> tuple[Accelerator, ...]:
    """Extract an allowlist only; never export serials, UUIDs, raw command output or host names."""
    if system == "Darwin":
        raw = _command(("/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"))
        try:
            values = json.loads(raw or "{}").get("SPDisplaysDataType", [])
            return tuple(
                Accelerator(kind="metal", name="Metal-capable GPU")
                for value in values[:8]
                if isinstance(value, dict)
                and any(
                    "metal" in str(key).lower()
                    and isinstance(entry, str)
                    and "unsupported" not in entry.lower()
                    and "not_supported" not in entry.lower()
                    for key, entry in value.items()
                )
            )
        except (TypeError, ValueError, AttributeError):
            return ()
    executable = shutil.which("nvidia-smi")
    if executable:
        raw = _command((executable, "--query-gpu=memory.total", "--format=csv,noheader,nounits"))
        if raw and all(re.fullmatch(r"[0-9]{1,8}", line.strip()) for line in raw.splitlines()):
            return tuple(
                Accelerator(kind="cuda", name="CUDA GPU", memory_bytes=int(line) * 1024**2)
                for line in raw.splitlines()[:8]
            )
    return ()


def _disk_free(path: Path) -> int | None:
    try:
        current = path.resolve()
        while not current.exists() and current != current.parent:
            current = current.parent
        return shutil.disk_usage(current).free
    except OSError:
        return None


def probe_hardware(project_root: Path, model_store: Path | None = None) -> HardwareReport:
    """Run only from an explicit owner profile/startup/experiment, never an automatic test."""
    system = platform.system()
    target, arch = platform_context(system, platform.machine())
    ram = available = threads = None
    try:
        stats = cast(_Stats, importlib.import_module("psutil"))
        memory = stats.virtual_memory()
        ram = memory.total if type(memory.total) is int and memory.total > 0 else None
        available = (
            memory.available
            if type(memory.available) is int and ram is not None and 0 <= memory.available <= ram
            else None
        )
        observed_threads = stats.cpu_count()
        threads = (
            observed_threads if type(observed_threads) is int and observed_threads > 0 else None
        )
    except Exception:
        pass
    accelerators = accelerator_observations(system)
    return HardwareReport.model_validate(
        {
            "architecture": arch,
            "context": HardwareContext(
                platform=target,
                ram_bytes=ram,
                cpu_threads=threads,
                vram_bytes=sum(
                    item.memory_bytes for item in accelerators if item.memory_bytes is not None
                )
                if accelerators and all(item.memory_bytes is not None for item in accelerators)
                else None,
            ),
            "available_ram_bytes": available,
            "project_free_bytes": _disk_free(project_root),
            "model_store_free_bytes": _disk_free(model_store) if model_store is not None else None,
            "model_store_id": canonical_hash(str(model_store.resolve()), namespace="model-store-v1")
            if model_store is not None
            else None,
            "accelerators": accelerators,
            "accelerator_status": "observed" if accelerators else "unknown",
        }
    )


HardwareProbe = Callable[[], HardwareReport]


def memory_budget(report: HardwareReport) -> int | None:
    total = report.context.ram_bytes
    return max(0, total - max(2 * GIB, total // 4)) if total is not None else None


def resource_reason(
    manifest: ModelManifest, report: HardwareReport, *, installation: bool = False
) -> str | None:
    if report.context.platform is None:
        return "platform_unknown"
    if report.context.platform not in manifest.platforms:
        return "platform_unsupported"
    budget = memory_budget(report)
    if budget is None or manifest.memory_bytes is None or report.available_ram_bytes is None:
        return "memory_unknown"
    if (
        report.pressure == "low_memory"
        or manifest.memory_bytes > budget
        or manifest.memory_bytes > max(0, report.available_ram_bytes - GIB)
    ):
        return "memory_insufficient"
    if installation:
        if (
            report.model_store_free_bytes is None
            or report.model_store_id is None
            or manifest.size_bytes is None
        ):
            return "model_store_disk_unknown"
        if report.model_store_free_bytes < 2 * manifest.size_bytes + GIB:
            return "insufficient_disk"
    return None


def resident_limit(manifests: tuple[ModelManifest, ...], report: HardwareReport) -> int:
    """Conservative capacity policy, not a measurement of peak runtime memory."""
    budget = memory_budget(report)
    if budget is None or not manifests or any(item.memory_bytes is None for item in manifests):
        return 0
    ordered = sorted((item.memory_bytes or 0 for item in manifests), reverse=True)
    consumed = limit = 0
    for size in ordered[:3]:
        if consumed + size > budget:
            break
        consumed += size
        limit += 1
    return limit
