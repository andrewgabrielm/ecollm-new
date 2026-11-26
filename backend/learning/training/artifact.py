"""Versioned on-disk artifact for the trained quality model.

An artifact lives in a directory shaped like::

    var/routers/<artifact_id>/
        manifest.json      # ArtifactManifest, small and cheap to list
        artifact.json      # TrainingArtifact in full, holds the booster text

Writing is a two-step dance:
1. Write both files into a temporary sibling directory.
2. ``os.replace`` that sibling onto the final directory, which is atomic on
   POSIX filesystems. If anything in step 1 fails, the final directory is left
   alone and the sibling can be cleaned up.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from backend.learning.training.trainer import TrainedModel
from backend.learning.training.types import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactManifest,
    TrainingArtifact,
    ValidationReport,
)
from backend.versions import POLICY_VERSION, canonical_hash

_ARTIFACT_FILENAME = "artifact.json"
_MANIFEST_FILENAME = "manifest.json"


class ArtifactLoadError(RuntimeError):
    """Safe failure during artifact read; never contains file paths or values."""


def save_artifact(
    *,
    artifact_id: str,
    trained: TrainedModel,
    report: ValidationReport,
    training_hash: str,
    validation_hash: str,
    root: Path,
    created_at: float,
) -> TrainingArtifact:
    """Write ``manifest.json`` and ``artifact.json`` atomically under ``root``."""
    if not root.is_absolute():
        raise ArtifactLoadError("Artifact root must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    booster_text = trained.booster.model_to_string()
    booster_sha = hashlib.sha256(booster_text.encode("utf-8")).hexdigest()
    quality_values = trained.vectors.y.astype(float).tolist()
    quality_mean = sum(quality_values) / len(quality_values)
    quality_variance = sum((value - quality_mean) ** 2 for value in quality_values) / len(
        quality_values
    )
    artifact = TrainingArtifact(
        artifact_id=artifact_id,
        feature_order=trained.vectors.feature_order,
        categorical_features=trained.vectors.categorical_features,
        categorical_vocabularies={
            column: encoder.vocabulary for column, encoder in trained.vectors.encoders.items()
        },
        booster_model=booster_text,
        booster_sha256=booster_sha,
        training_sample_count=int(trained.vectors.x.shape[0]),
        training_family_count=len({group for group in trained.vectors.group_ids}),
        training_quality_mean=_clamp_quality(quality_mean),
        training_quality_variance=max(0.0, quality_variance),
        training_hash=training_hash,
        validation_hash=validation_hash,
        validation_report=report,
        policy_version=POLICY_VERSION,
        created_at=created_at,
    )
    artifact_path = root / artifact_id
    manifest = ArtifactManifest(
        artifact_id=artifact_id,
        artifact_path=f"{artifact_id}/{_ARTIFACT_FILENAME}",
        artifact_sha256=hashlib.sha256(
            artifact.model_dump_json(by_alias=False).encode("utf-8")
        ).hexdigest(),
        training_sample_count=artifact.training_sample_count,
        validation_report_summary=report,
        policy_version=POLICY_VERSION,
        created_at=created_at,
    )
    staging = Path(tempfile.mkdtemp(prefix=f".{artifact_id}.", dir=root))
    try:
        (staging / _ARTIFACT_FILENAME).write_text(artifact.model_dump_json(indent=2))
        (staging / _MANIFEST_FILENAME).write_text(manifest.model_dump_json(indent=2))
        if artifact_path.exists():
            shutil.rmtree(artifact_path)
        os.replace(staging, artifact_path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return artifact


def load_artifact(*, root: Path, artifact_id: str) -> TrainingArtifact:
    if not root.is_absolute():
        raise ArtifactLoadError("Artifact root must be absolute")
    artifact_file = root / artifact_id / _ARTIFACT_FILENAME
    if not artifact_file.is_file():
        raise ArtifactLoadError("Artifact file is missing")
    try:
        payload = json.loads(artifact_file.read_text())
    except (OSError, ValueError) as error:
        raise ArtifactLoadError("Artifact file is unreadable") from error
    if not isinstance(payload, dict):
        raise ArtifactLoadError("Artifact file is malformed")
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactLoadError("Artifact schema version is unsupported")
    try:
        artifact = TrainingArtifact.model_validate(payload)
    except PydanticValidationError as error:
        raise ArtifactLoadError("Artifact payload failed validation") from error
    expected_sha = hashlib.sha256(artifact.booster_model.encode("utf-8")).hexdigest()
    if expected_sha != artifact.booster_sha256:
        raise ArtifactLoadError("Artifact booster checksum mismatch")
    return artifact


def canonical_training_hash(sample_task_ids: list[str], candidate_ids: list[str]) -> str:
    return canonical_hash(
        {"task_ids": sorted(sample_task_ids), "candidate_ids": sorted(candidate_ids)},
        namespace="router-training-set-v1",
    )


def canonical_validation_hash(sample_task_ids: list[str], candidate_ids: list[str]) -> str:
    return canonical_hash(
        {"task_ids": sorted(sample_task_ids), "candidate_ids": sorted(candidate_ids)},
        namespace="router-validation-set-v1",
    )


def _clamp_quality(value: float) -> float:
    return max(0.0, min(1.0, value))
