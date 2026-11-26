"""Shared synthetic data helpers for Step 13/14 router tests.

This module is deliberately named with a leading underscore so pytest does not
collect it as a test module. Tests import from it directly.
"""

from __future__ import annotations

from backend.broker.feature_extractor import FeatureRow
from backend.contracts import TaskCategory
from backend.learning.collection.types import Observation, ObservationStatus
from backend.model_management.manifest import BenchmarkProvenance, Platform
from backend.versions import POLICY_VERSION

GOOD_MODEL = "mock-general"
BAD_MODEL = "mock-specialist"


def make_feature_row(
    task: TaskCategory,
    model_id: str,
    *,
    quality_prior: float,
    char_count: int = 20,
    question_count: int = 1,
    resident: bool = True,
) -> FeatureRow:
    return FeatureRow(
        task=task,
        sanitized_char_count=char_count,
        sanitized_token_estimate=max(1, char_count // 4),
        line_count=1,
        question_count=question_count,
        digit_count=0,
        bullet_line_count=0,
        bracket_count=0,
        has_json_cue=False,
        has_code_fence=False,
        candidate_id=model_id,
        candidate_manifest_version="d" * 64,
        candidate_weight_digest="a" * 64,
        candidate_family="synthetic-" + model_id.split("-")[-1],
        candidate_quantization="q4_0",
        capability_quality=quality_prior,
        capability_latency_ms=400.0,
        capability_provenance=BenchmarkProvenance.SYNTHETIC,
        capability_benchmark_version="b" * 64,
        installed=True,
        healthy=True,
        resident=resident,
        hardware_platform=Platform.MACOS_ARM64,
        hardware_ram_bytes=32_000_000_000,
        hardware_vram_bytes=None,
        hardware_cpu_threads=8,
        hardware_version="c" * 64,
    )


def make_observation(
    task_id: str,
    model_id: str,
    *,
    split: str,
    quality: float | None,
    task: TaskCategory,
    quality_prior: float,
    status: ObservationStatus = ObservationStatus.SUCCESS,
    features: FeatureRow | None = None,
) -> Observation:
    row = features or make_feature_row(task, model_id, quality_prior=quality_prior)
    return Observation(
        run_id="run-synthetic",
        task_id=task_id,
        model_id=model_id,
        model_version="v1",
        split=split,  # type: ignore[arg-type]
        task_category=task,
        feature_hash="0" * 64,
        features=row,
        status=status,
        quality=quality,
        format_valid=True if quality is not None else None,
        total_ms=10.0,
        privacy_ms=0.5,
        routing_ms=0.5,
        generation_ms=7.0,
        inspection_ms=1.0,
        resident=True,
        policy_version=POLICY_VERSION,
        mock=True,
        created_at=1.0,
    )


def make_dataset(
    *,
    split: str,
    task_count: int,
    task: TaskCategory = TaskCategory.QA,
    good_quality: float = 0.95,
    bad_quality: float = 0.3,
    good_prior: float = 0.9,
    bad_prior: float = 0.4,
) -> list[Observation]:
    observations: list[Observation] = []
    for idx in range(task_count):
        task_id = f"task-{split}-{idx}-v0"
        observations.append(
            make_observation(
                task_id,
                GOOD_MODEL,
                split=split,
                quality=good_quality,
                task=task,
                quality_prior=good_prior,
            )
        )
        observations.append(
            make_observation(
                task_id,
                BAD_MODEL,
                split=split,
                quality=bad_quality,
                task=task,
                quality_prior=bad_prior,
            )
        )
    return observations
