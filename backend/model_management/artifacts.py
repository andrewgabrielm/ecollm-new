"""Bounded private local evidence files under the project's ignored results root."""

import csv
import json
import os
from pathlib import Path
from uuid import uuid4

from backend.model_management.initial_install import ROOT, InitialInstallError


def run_directory(kind: str) -> Path:
    base = ROOT / "evaluation" / "results"
    if kind not in {
        "hardware",
        "catalog",
        "install",
        "real-smoke",
        "feasibility",
        "step09-fixture",
    }:
        raise InitialInstallError("invalid_artifact_kind")
    if base.parent.is_symlink() or base.is_symlink():
        raise InitialInstallError("unsafe_artifact_path")
    base.mkdir(parents=True, exist_ok=True)
    directory = base / f"{kind}-{uuid4().hex[:12]}"
    directory.mkdir(mode=0o700)
    return directory


def write_json(directory: Path, filename: str, value: object) -> Path:
    if filename not in {
        "hardware.json",
        "manifest.json",
        "smoke_report.json",
        "install_state.json",
        "operation.json",
    }:
        raise InitialInstallError("invalid_artifact_name")
    path = directory / filename
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    path.chmod(0o600)
    return path


def write_csv(directory: Path, rows: list[dict[str, object]], fields: tuple[str, ...]) -> Path:
    path = directory / "feasibility.csv"
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    path.chmod(0o600)
    return path
