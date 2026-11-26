"""Step 13 training/validation/promotion tests — synthetic data, no real inference."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from time import time

import numpy as np
import pytest

from backend.contracts import TaskCategory
from backend.learning.collection.types import Observation, ObservationStatus
from backend.learning.training import (
    ArtifactLoadError,
    PromotionCriteria,
    PromotionError,
    TrainingError,
    build_vectors,
    load_artifact,
    observations_to_samples,
    promote_artifact,
    read_active_pointer,
    save_artifact,
    train_quality_model,
    validate_model,
)
from backend.learning.training.artifact import (
    canonical_training_hash,
    canonical_validation_hash,
)
from backend.learning.training.promotion import rollback_active
from backend.learning.training.trainer import TrainedModel, TrainingParameters
from backend.learning.training.types import TrainingArtifact, ValidationReport
from tests.learning._router_fixtures import (
    BAD_MODEL as _BAD_MODEL,
)
from tests.learning._router_fixtures import (
    GOOD_MODEL as _GOOD_MODEL,
)
from tests.learning._router_fixtures import (
    make_dataset as _dataset,
)
from tests.learning._router_fixtures import (
    make_observation as _observation,
)


def _train(dataset: Iterable[Observation], *, rounds: int = 60) -> TrainedModel:
    samples = observations_to_samples(dataset)
    return train_quality_model(
        samples, parameters=TrainingParameters(num_boost_round=rounds, min_data_in_leaf=1)
    )


class TestVectorizer:
    def test_filters_non_success_and_missing_features(self) -> None:
        dataset = [
            _observation(
                "task-a-v0",
                _GOOD_MODEL,
                split="dev_train",
                quality=0.9,
                task=TaskCategory.QA,
                quality_prior=0.9,
            ),
            _observation(
                "task-b-v0",
                _BAD_MODEL,
                split="dev_train",
                quality=None,
                task=TaskCategory.QA,
                quality_prior=0.4,
                status=ObservationStatus.PROVIDER_FAILURE,
            ),
        ]
        samples = observations_to_samples(dataset)
        assert len(samples) == 1
        assert samples[0].candidate_id == _GOOD_MODEL

    def test_build_vectors_stable_column_order(self) -> None:
        samples = observations_to_samples(_dataset(split="dev_train", task_count=4))
        vectors = build_vectors(samples)
        assert vectors.feature_order[:4] == (
            "sanitized_char_count",
            "sanitized_token_estimate",
            "line_count",
            "question_count",
        )
        assert set(vectors.categorical_features) == {
            "task",
            "candidate_id",
            "candidate_family",
            "candidate_quantization",
            "capability_provenance",
            "hardware_platform",
        }
        assert vectors.x.shape[0] == len(samples)
        assert vectors.x.shape[1] == len(vectors.feature_order)
        assert vectors.y.dtype == np.float32

    def test_encoder_learns_vocabularies_in_insertion_order(self) -> None:
        samples = observations_to_samples(_dataset(split="dev_train", task_count=3))
        vectors = build_vectors(samples)
        candidate_encoder = vectors.encoders["candidate_id"]
        assert set(candidate_encoder.vocabulary) == {_GOOD_MODEL, _BAD_MODEL}

    def test_encoder_reuse_detects_unseen_values(self) -> None:
        train_samples = observations_to_samples(_dataset(split="dev_train", task_count=3))
        base = build_vectors(train_samples)
        new_dataset = [
            _observation(
                "task-new-v0",
                "unseen-model",
                split="dev_validation",
                quality=0.5,
                task=TaskCategory.QA,
                quality_prior=0.5,
            )
        ]
        new_samples = observations_to_samples(new_dataset)
        reused = build_vectors(new_samples, encoders=base.encoders)
        candidate_col = reused.feature_order.index("candidate_id")
        assert reused.x[0][candidate_col] == -1  # unseen candidate → sentinel


class TestTrainer:
    def test_training_fits_a_good_vs_bad_candidate(self) -> None:
        trained = _train(_dataset(split="dev_train", task_count=8))
        feature_names = trained.vectors.feature_order
        predictions = trained.booster.predict(trained.vectors.x)
        predictions_array = np.asarray(predictions, dtype=np.float64)
        good_index = feature_names.index("candidate_id")
        good_code = trained.vectors.encoders["candidate_id"].vocabulary.index(_GOOD_MODEL)
        good_mask = trained.vectors.x[:, good_index] == good_code
        bad_mask = ~good_mask
        assert predictions_array[good_mask].mean() > predictions_array[bad_mask].mean() + 0.1

    def test_empty_samples_raise(self) -> None:
        with pytest.raises(TrainingError):
            train_quality_model([])


class TestValidator:
    def test_group_aware_metrics_and_promotion_pass(self) -> None:
        trained = _train(_dataset(split="dev_train", task_count=16))
        val_samples = observations_to_samples(_dataset(split="dev_validation", task_count=6))
        criteria = PromotionCriteria(
            min_train_samples=1,
            min_validation_samples=1,
            min_distinct_families=2,
            min_distinct_tasks=2,
            min_distinct_candidates=2,
            max_validation_mae=0.4,
        )
        report = validate_model(trained, val_samples, criteria=criteria)
        assert report.sample_count == len(val_samples)
        assert report.distinct_candidates == 2
        assert report.distinct_tasks == 6
        assert report.router_quality_mean >= report.rules_quality_mean
        assert report.promotion_passed, report.promotion_blockers

    def test_rejects_when_regret_does_not_improve(self) -> None:
        # Trained to prefer "bad" model via inverted qualities on training set.
        inverted_train = _dataset(
            split="dev_train",
            task_count=12,
            good_quality=0.2,
            bad_quality=0.95,
            good_prior=0.9,
            bad_prior=0.4,
        )
        trained = _train(inverted_train)
        val_samples = observations_to_samples(_dataset(split="dev_validation", task_count=8))
        criteria = PromotionCriteria(
            min_train_samples=1,
            min_validation_samples=1,
            min_distinct_families=2,
            min_distinct_tasks=2,
            min_distinct_candidates=2,
            max_validation_mae=1.0,
            min_regret_improvement=0.05,
        )
        report = validate_model(trained, val_samples, criteria=criteria)
        assert not report.promotion_passed
        assert "regret_improvement_below_minimum" in report.promotion_blockers

    def test_detects_unseen_candidate_in_validation(self) -> None:
        trained = _train(_dataset(split="dev_train", task_count=8))
        val_samples = observations_to_samples(
            [
                _observation(
                    "task-unseen-v0",
                    "never-seen-model",
                    split="dev_validation",
                    quality=0.6,
                    task=TaskCategory.QA,
                    quality_prior=0.5,
                ),
                _observation(
                    "task-unseen-v1",
                    _GOOD_MODEL,
                    split="dev_validation",
                    quality=0.9,
                    task=TaskCategory.QA,
                    quality_prior=0.9,
                ),
            ]
        )
        report = validate_model(
            trained,
            val_samples,
            criteria=PromotionCriteria(
                min_train_samples=1,
                min_validation_samples=1,
                min_distinct_families=1,
                min_distinct_tasks=1,
                min_distinct_candidates=1,
            ),
        )
        assert report.unseen_candidate_count == 1
        assert not report.promotion_passed
        assert "validation_contains_unseen_candidate" in report.promotion_blockers


def _save_passing_artifact(tmp_path: Path, artifact_id: str = "router-pass-1") -> TrainingArtifact:
    trained = _train(_dataset(split="dev_train", task_count=16))
    val_samples = observations_to_samples(_dataset(split="dev_validation", task_count=6))
    criteria = PromotionCriteria(
        min_train_samples=1,
        min_validation_samples=1,
        min_distinct_families=2,
        min_distinct_tasks=2,
        min_distinct_candidates=2,
        max_validation_mae=0.4,
    )
    report = validate_model(trained, val_samples, criteria=criteria)
    assert report.promotion_passed, report.promotion_blockers
    return save_artifact(
        artifact_id=artifact_id,
        trained=trained,
        report=report,
        training_hash=canonical_training_hash(["t1"], [_GOOD_MODEL, _BAD_MODEL]),
        validation_hash=canonical_validation_hash(["t2"], [_GOOD_MODEL, _BAD_MODEL]),
        root=tmp_path,
        created_at=time(),
    )


class TestArtifact:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        router_root = tmp_path / "routers"
        router_root.mkdir()
        saved = _save_passing_artifact(router_root)
        reloaded = load_artifact(root=router_root, artifact_id=saved.artifact_id)
        assert reloaded.booster_sha256 == saved.booster_sha256
        assert reloaded.feature_order == saved.feature_order
        assert reloaded.categorical_vocabularies == saved.categorical_vocabularies
        assert reloaded.validation_report.promotion_passed

    def test_missing_artifact_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ArtifactLoadError):
            load_artifact(root=tmp_path, artifact_id="never-saved")

    def test_corrupt_booster_detected(self, tmp_path: Path) -> None:
        saved = _save_passing_artifact(tmp_path)
        path = tmp_path / saved.artifact_id / "artifact.json"
        payload = json.loads(path.read_text())
        payload["booster_model"] = payload["booster_model"] + "\n# noise\n"
        path.write_text(json.dumps(payload))
        with pytest.raises(ArtifactLoadError):
            load_artifact(root=tmp_path, artifact_id=saved.artifact_id)

    def test_schema_version_mismatch(self, tmp_path: Path) -> None:
        saved = _save_passing_artifact(tmp_path)
        path = tmp_path / saved.artifact_id / "artifact.json"
        payload = json.loads(path.read_text())
        payload["schema_version"] = "router-artifact-v99"
        path.write_text(json.dumps(payload))
        with pytest.raises(ArtifactLoadError):
            load_artifact(root=tmp_path, artifact_id=saved.artifact_id)


class TestPromotion:
    def test_promote_and_read_pointer(self, tmp_path: Path) -> None:
        saved = _save_passing_artifact(tmp_path)
        result = promote_artifact(root=tmp_path, artifact_id=saved.artifact_id)
        assert result.artifact_id == saved.artifact_id
        assert result.previous_artifact_id is None
        assert read_active_pointer(tmp_path) == saved.artifact_id

    def test_promote_blocks_when_criteria_fail(self, tmp_path: Path) -> None:
        # Save an artifact whose report indicates a blocker so promotion refuses it.
        passing = _save_passing_artifact(tmp_path, artifact_id="router-passing")
        bad_report: ValidationReport = passing.validation_report.model_copy(
            update={
                "promotion_passed": False,
                "promotion_blockers": ("validation_mae_above_maximum",),
            }
        )
        broken_path = tmp_path / passing.artifact_id / "artifact.json"
        payload = json.loads(broken_path.read_text())
        payload["validation_report"] = bad_report.model_dump(mode="json")
        broken_path.write_text(json.dumps(payload))
        with pytest.raises(PromotionError):
            promote_artifact(root=tmp_path, artifact_id=passing.artifact_id)

    def test_promote_missing_artifact(self, tmp_path: Path) -> None:
        with pytest.raises(PromotionError):
            promote_artifact(root=tmp_path, artifact_id="router-does-not-exist")

    def test_rollback_restores_previous(self, tmp_path: Path) -> None:
        first = _save_passing_artifact(tmp_path, artifact_id="router-first")
        second = _save_passing_artifact(tmp_path, artifact_id="router-second")
        promote_artifact(root=tmp_path, artifact_id=first.artifact_id)
        promote_artifact(root=tmp_path, artifact_id=second.artifact_id)
        restored = rollback_active(tmp_path)
        assert restored == first.artifact_id
        assert read_active_pointer(tmp_path) == first.artifact_id

    def test_rollback_without_backup_clears_pointer(self, tmp_path: Path) -> None:
        saved = _save_passing_artifact(tmp_path)
        promote_artifact(root=tmp_path, artifact_id=saved.artifact_id)
        (tmp_path / "active.json.bak").unlink(missing_ok=True)
        restored = rollback_active(tmp_path)
        assert restored is None
        assert read_active_pointer(tmp_path) is None
