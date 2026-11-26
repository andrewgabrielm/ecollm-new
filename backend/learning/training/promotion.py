"""Atomic activation and rollback for router artifacts.

The "active" router is a tiny pointer file at ``<root>/active.json`` that names
an artifact id. Promotion writes a new pointer atomically with ``os.replace``;
rollback restores a backup written before the swap. A missing pointer or an
unreadable active artifact means the broker stays on the rules router (callers
handle this at load time).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from backend.learning.training.artifact import ArtifactLoadError, load_artifact
from backend.learning.training.types import TrainingArtifact

_POINTER_FILENAME = "active.json"
_BACKUP_FILENAME = "active.json.bak"


class PromotionError(RuntimeError):
    """Safe promotion failure; contains no caller values."""


@dataclass(frozen=True)
class PromotionResult:
    artifact_id: str
    previous_artifact_id: str | None


def promote_artifact(
    *,
    root: Path,
    artifact_id: str,
) -> PromotionResult:
    """Validate the target artifact and atomically point ``active.json`` at it."""
    if not root.is_absolute():
        raise PromotionError("Router root must be absolute")
    if not artifact_id:
        raise PromotionError("Promotion requires an artifact id")
    try:
        artifact = load_artifact(root=root, artifact_id=artifact_id)
    except ArtifactLoadError as error:
        raise PromotionError("Target artifact failed to load") from error
    if not artifact.validation_report.promotion_passed:
        raise PromotionError("Target artifact did not pass promotion criteria")
    previous = read_active_pointer(root)
    pointer_payload = {
        "schema_version": "router-active-pointer-v1",
        "artifact_id": artifact_id,
        "policy_version": artifact.policy_version,
        "promoted_at": artifact.created_at,
    }
    pointer_path = root / _POINTER_FILENAME
    backup_path = root / _BACKUP_FILENAME
    if pointer_path.exists():
        try:
            backup_path.write_bytes(pointer_path.read_bytes())
        except OSError as error:
            raise PromotionError("Failed to back up previous pointer") from error
    _write_atomic(pointer_path, json.dumps(pointer_payload, indent=2))
    return PromotionResult(artifact_id=artifact_id, previous_artifact_id=previous)


def read_active_pointer(root: Path) -> str | None:
    pointer_path = root / _POINTER_FILENAME
    if not pointer_path.is_file():
        return None
    try:
        payload = json.loads(pointer_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("artifact_id")
    if not isinstance(value, str) or not value:
        return None
    return value


def load_active_artifact(root: Path) -> TrainingArtifact | None:
    pointer = read_active_pointer(root)
    if pointer is None:
        return None
    try:
        return load_artifact(root=root, artifact_id=pointer)
    except ArtifactLoadError:
        return None


def rollback_active(root: Path) -> str | None:
    """Restore the previous pointer; returns the restored artifact id or None."""
    backup_path = root / _BACKUP_FILENAME
    pointer_path = root / _POINTER_FILENAME
    if not backup_path.is_file():
        try:
            pointer_path.unlink(missing_ok=True)
        except OSError as error:
            raise PromotionError("Failed to clear active pointer") from error
        return None
    try:
        payload = json.loads(backup_path.read_text())
    except (OSError, ValueError) as error:
        raise PromotionError("Backup pointer is unreadable") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("artifact_id"), str):
        raise PromotionError("Backup pointer is malformed")
    _write_atomic(pointer_path, backup_path.read_text())
    with contextlib.suppress(OSError):
        backup_path.unlink(missing_ok=True)
    return str(payload["artifact_id"])


def _write_atomic(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=target.name, dir=target.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.replace(temp_name, target)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
