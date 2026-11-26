"""Invented hardware observations only; no real profile, device query or benchmark."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.model_management import hardware as module
from backend.model_management.hardware import (
    GIB,
    HardwareReport,
    platform_context,
    resident_limit,
    resource_reason,
)
from backend.model_management.manifest import ModelManifest, Platform
from backend.model_management.registry import HardwareContext
from scripts.step09 import fixture_hardware


@pytest.mark.parametrize(
    ("system", "arch", "expected"),
    [
        ("Darwin", "arm64", Platform.MACOS_ARM64),
        ("Darwin", "x86_64", Platform.MACOS_X86_64),
        ("Linux", "aarch64", Platform.LINUX_ARM64),
        ("Linux", "x86_64", Platform.LINUX_X86_64),
        ("Windows", "AMD64", Platform.WINDOWS_X86_64),
        ("Other", "invented", None),
    ],
)
def test_platform_is_observed_not_assumed(
    system: str, arch: str, expected: Platform | None
) -> None:
    assert platform_context(system, arch)[0] == expected


def model(memory: int = GIB) -> ModelManifest:
    return ModelManifest.model_validate(
        {
            "id": "fixture",
            "runtime_tag": "fixture:q4",
            "source": {"uri": "https://example.invalid/synthetic"},
            "family": "synthetic",
            "quantization": "Q4",
            "size_bytes": GIB,
            "memory_bytes": memory,
            "platforms": ["linux-x86_64"],
        }
    )


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"context": HardwareContext()}, "platform_unknown"),
        ({"context": HardwareContext(platform=Platform.MACOS_ARM64)}, "platform_unsupported"),
        ({"context": HardwareContext(platform=Platform.LINUX_X86_64)}, "memory_unknown"),
        ({"available_ram_bytes": None}, "memory_unknown"),
        ({"available_ram_bytes": GIB // 2}, "memory_insufficient"),
        ({"model_store_free_bytes": None}, "model_store_disk_unknown"),
        ({"model_store_free_bytes": GIB}, "insufficient_disk"),
        ({}, None),
    ],
)
def test_resource_unknowns_low_memory_and_disk_are_explicit(
    updates: dict[str, object], reason: str | None
) -> None:
    report = HardwareReport.model_validate({**fixture_hardware().model_dump(), **updates})
    assert resource_reason(model(), report, installation=True) == reason


def test_memory_capacity_has_reserve_and_no_claim_of_measured_speed() -> None:
    report = fixture_hardware()
    assert resident_limit((model(4 * GIB), model(4 * GIB), model(4 * GIB)), report) == 3
    assert resident_limit((model(7 * GIB), model(7 * GIB)), report) == 1
    assert resident_limit((model(16 * GIB),), report) == 0
    assert resident_limit((model(),), HardwareReport()) == 0
    assert resource_reason(model(15 * GIB), report) == "memory_insufficient"
    assert "speed" not in report.model_dump()


def test_probe_extracts_only_injected_observations(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    monkeypatch.setattr("backend.model_management.hardware.platform.system", lambda: "Linux")
    monkeypatch.setattr("backend.model_management.hardware.platform.machine", lambda: "x86_64")
    monkeypatch.setattr(
        "backend.model_management.hardware.importlib.import_module",
        lambda name: SimpleNamespace(
            virtual_memory=lambda: SimpleNamespace(total=16 * GIB, available=12 * GIB),
            cpu_count=lambda: 4,
        ),
    )
    monkeypatch.setattr(module, "accelerator_observations", lambda system: ())
    monkeypatch.setattr(module, "_disk_free", lambda path: 32 * GIB)
    report = module.probe_hardware(tmp_path, tmp_path)
    assert report.context.ram_bytes == 16 * GIB
    assert report.context.cpu_threads == 4
    assert report.available_ram_bytes == 12 * GIB
    assert report.accelerator_status == "unknown" and report.context.vram_bytes is None
    assert str(tmp_path) not in report.model_dump_json()


def test_failed_probe_reports_unknowns_without_downloading_or_guessing(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)

    def fail(name: str) -> object:
        raise RuntimeError("SYNTHETIC-PRIVATE-DIAGNOSTIC")

    monkeypatch.setattr("backend.model_management.hardware.platform.system", lambda: "Unknown")
    monkeypatch.setattr("backend.model_management.hardware.platform.machine", lambda: "Unknown")
    monkeypatch.setattr("backend.model_management.hardware.importlib.import_module", fail)
    monkeypatch.setattr(module, "accelerator_observations", lambda system: ())
    monkeypatch.setattr(module, "_disk_free", lambda path: None)
    report = module.probe_hardware(tmp_path)
    assert report.context == HardwareContext() and report.pressure == "unknown"
    assert "SYNTHETIC-PRIVATE" not in report.model_dump_json()


@pytest.mark.privacy
def test_gpu_probe_filters_serials_and_unsupported_metal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module,
        "_command",
        lambda args: json.dumps(
            {
                "SPDisplaysDataType": [
                    {"spdisplays_metal": "spdisplays_metal3", "serial": "SYNTHETIC-PRIVATE-SERIAL"}
                ]
            }
        ),
    )
    observation = module.accelerator_observations("Darwin")
    assert len(observation) == 1 and observation[0].memory_bytes is None
    assert "SYNTHETIC-PRIVATE-SERIAL" not in observation[0].model_dump_json()
    monkeypatch.setattr(
        module,
        "_command",
        lambda args: json.dumps({"SPDisplaysDataType": [{"spdisplays_metal": "unsupported"}]}),
    )
    assert module.accelerator_observations("Darwin") == ()


def test_cuda_metadata_is_optional_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.model_management.hardware.shutil.which", lambda name: "/synthetic/nvidia-smi"
    )
    monkeypatch.setattr(module, "_command", lambda args: "4096\n8192\n")
    result = module.accelerator_observations("Linux")
    assert [item.memory_bytes for item in result] == [4 * GIB, 8 * GIB]
    monkeypatch.setattr(module, "_command", lambda args: "unknown")
    assert module.accelerator_observations("Linux") == ()


@pytest.mark.parametrize(
    "updates",
    [
        {"available_ram_bytes": 20 * GIB},
        {"available_ram_bytes": -1},
        {"project_free_bytes": True},
        {"secret_extra": "SYNTHETIC-PRIVATE"},
    ],
)
def test_invalid_hardware_reports_fail_validation(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HardwareReport.model_validate({**fixture_hardware().model_dump(), **updates})
