"""Command validation, fixture isolation and private report files; no real resource calls."""

import json
from pathlib import Path
from typing import NoReturn

import pytest

from backend.model_management import artifacts
from backend.model_management.hardware import HardwareReport
from backend.model_management.initial_install import InitialInstallError
from scripts import step09

pytestmark = pytest.mark.privacy


@pytest.mark.parametrize("command", ["profile-hardware", "catalog-status"])
def test_fixture_cli_never_probes_connects_or_generates(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("Real operation forbidden")

    monkeypatch.setattr(step09, "probe_hardware", forbidden)
    monkeypatch.setattr(step09, "build_local_control", forbidden)
    monkeypatch.setattr(step09, "build_real_broker", forbidden)
    monkeypatch.setattr(step09, "run_directory", lambda kind: tmp_path)
    monkeypatch.setattr("sys.argv", ["step09.py", command, "--fixture"])
    assert step09.main() == 0
    value = json.loads((tmp_path / "manifest.json").read_text())
    assert value["evidence"] == "synthetic"
    assert value["generation_calls"] == value["network_calls"] == value["hardware_probes"] == 0
    hardware = HardwareReport.model_validate_json((tmp_path / "hardware.json").read_text())
    assert hardware.evidence == "synthetic"
    assert value["registry"]["hardware_version"] == hardware.context.version
    assert value["registry"]["hardware"] == hardware.context.model_dump(mode="json")
    assert "SYNTHETIC METADATA ONLY" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    [
        ["model-install"],
        ["model-install", "--model", "https://evil.invalid/arbitrary"],
        ["model-install", "--model", "placeholder-general"],
        ["model-install", "--fixture"],
        ["real-smoke", "--fixture"],
        ["feasibility", "--fixture"],
    ],
)
def test_invalid_or_unverified_cli_actions_fail_before_any_real_operation(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        nonlocal calls
        calls += 1
        raise AssertionError("Real operation forbidden")

    monkeypatch.setattr(step09, "probe_hardware", forbidden)
    monkeypatch.setattr(step09, "build_local_control", forbidden)
    monkeypatch.setattr(step09, "build_real_broker", forbidden)
    monkeypatch.setattr("sys.argv", ["step09.py", *arguments])
    assert step09.main() == 1 and calls == 0
    output = capsys.readouterr()
    assert "Step 09 blocked:" in output.err
    assert "https://evil.invalid" not in output.err


def test_noninteractive_execution_cannot_approve_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert not step09._confirm("APPROVE synthetic-bound-plan")


def test_report_writes_do_not_overwrite_and_unknown_metrics_stay_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifacts, "ROOT", tmp_path)
    directory = artifacts.run_directory("feasibility")
    path = artifacts.write_json(
        directory, "smoke_report.json", {"input_tokens": None, "ttft_ms": None}
    )
    assert json.loads(path.read_text()) == {"input_tokens": None, "ttft_ms": None}
    assert path.stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        artifacts.write_json(directory, "smoke_report.json", {"changed": True})
    csv = artifacts.write_csv(directory, [{"tokens": None, "ttft": None}], ("tokens", "ttft"))
    assert csv.read_text().splitlines() == ["tokens,ttft", ","]


def test_report_rejects_unsafe_names_nonfinite_values_and_symlink_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifacts, "ROOT", tmp_path)
    directory = artifacts.run_directory("hardware")
    with pytest.raises(InitialInstallError):
        artifacts.write_json(directory, "../outside.json", {})
    with pytest.raises(ValueError):
        artifacts.write_json(directory, "hardware.json", {"invalid": float("nan")})
    assert not (directory / "hardware.json").exists()
    with pytest.raises(InitialInstallError):
        artifacts.run_directory("../outside")
    (tmp_path / "other").mkdir()
    monkeypatch.setattr(artifacts, "ROOT", tmp_path / "other")
    (tmp_path / "other" / "evaluation").symlink_to(
        tmp_path / "evaluation", target_is_directory=True
    )
    with pytest.raises(InitialInstallError):
        artifacts.run_directory("hardware")
