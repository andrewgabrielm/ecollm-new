"""Step 13 CLI: train the LightGBM quality model on recorded observations.

Reads observations from a SQLite database (default ``var/bench``), filters out
rows without quality or features, trains on ``TRAIN_RUN`` and validates on
``VAL_RUN`` (both may name the same run with ``SPLIT`` differentiating). The
resulting artifact lands under ``var/routers/<artifact_id>/``. The artifact is
NEVER activated by this command — use ``scripts/promote_router.py`` for that.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from time import time

from backend.learning.collection import Observation, ObservationStore
from backend.learning.training import (
    PromotionCriteria,
    TrainingError,
    observations_to_samples,
    save_artifact,
    train_quality_model,
    validate_model,
)
from backend.learning.training.artifact import (
    canonical_training_hash,
    canonical_validation_hash,
)
from backend.learning.training.trainer import TrainingParameters
from backend.storage.connection import LocalDatabase

DEFAULT_STORE = Path("var/bench")
DEFAULT_ROUTER_ROOT = Path("var/routers")


def _open_store(path: Path) -> ObservationStore:
    resolved = path if path.is_absolute() else (Path.cwd() / path).resolve()
    if not resolved.is_dir():
        raise SystemExit(f"observations store not found: {resolved}")
    database = LocalDatabase(resolved)
    database.migrate()
    return ObservationStore(database)


def _run_observations(store: ObservationStore, run_id: str) -> tuple[Observation, ...]:
    rows = store.load_all(run_id=run_id)
    return tuple(rows)


def _task_ids(observations: tuple[Observation, ...]) -> list[str]:
    return [observation.task_id for observation in observations]


def _candidate_ids(observations: tuple[Observation, ...]) -> list[str]:
    return [observation.model_id for observation in observations]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="train_router",
        description="Train the LightGBM router over recorded observations",
    )
    parser.add_argument(
        "--train-run",
        default=os.environ.get("TRAIN_RUN"),
        required=os.environ.get("TRAIN_RUN") is None,
        help="run_id used for training observations",
    )
    parser.add_argument(
        "--val-run",
        default=os.environ.get("VAL_RUN"),
        required=os.environ.get("VAL_RUN") is None,
        help="run_id used for validation observations",
    )
    parser.add_argument(
        "--artifact-id",
        default=os.environ.get("ARTIFACT_ID"),
        help="artifact identifier (default: router-<epoch-seconds>)",
    )
    parser.add_argument(
        "--store",
        default=os.environ.get("BENCH_STORE", str(DEFAULT_STORE)),
        help="observations SQLite directory",
    )
    parser.add_argument(
        "--router-root",
        default=os.environ.get("ROUTER_ROOT", str(DEFAULT_ROUTER_ROOT)),
        help="artifact output directory",
    )
    parser.add_argument(
        "--num-boost-round",
        type=int,
        default=int(os.environ.get("NUM_BOOST_ROUND", "60")),
        help="LightGBM boosting rounds",
    )
    args = parser.parse_args(argv)
    store = _open_store(Path(args.store))
    train_observations = _run_observations(store, args.train_run)
    val_observations = _run_observations(store, args.val_run)
    if not train_observations:
        raise SystemExit(f"training run {args.train_run!r} has no observations")
    if not val_observations:
        raise SystemExit(f"validation run {args.val_run!r} has no observations")
    train_samples = observations_to_samples(train_observations)
    val_samples = observations_to_samples(val_observations)
    if len(train_samples) < PromotionCriteria().min_train_samples:
        raise SystemExit(
            f"training set too small: {len(train_samples)} samples (need "
            f"{PromotionCriteria().min_train_samples})"
        )
    try:
        trained = train_quality_model(
            train_samples,
            parameters=TrainingParameters(num_boost_round=args.num_boost_round),
        )
    except TrainingError as error:
        raise SystemExit(f"training failed: {error}") from error
    report = validate_model(trained, val_samples)
    artifact_id = args.artifact_id or f"router-{int(time())}"
    router_root = Path(args.router_root)
    router_root = router_root if router_root.is_absolute() else (Path.cwd() / router_root).resolve()
    router_root.mkdir(parents=True, exist_ok=True)
    artifact = save_artifact(
        artifact_id=artifact_id,
        trained=trained,
        report=report,
        training_hash=canonical_training_hash(
            _task_ids(train_observations), _candidate_ids(train_observations)
        ),
        validation_hash=canonical_validation_hash(
            _task_ids(val_observations), _candidate_ids(val_observations)
        ),
        root=router_root,
        created_at=time(),
    )
    print(f"artifact_id:        {artifact.artifact_id}")
    print(f"training_samples:   {artifact.training_sample_count}")
    print(f"validation_samples: {report.sample_count}")
    print(f"validation_mae:     {report.mae:.4f}")
    print(f"validation_r2:      {report.r2:.4f}")
    print(f"regret_improvement: {report.regret_improvement:+.4f}")
    print(f"promotion_passed:   {report.promotion_passed}")
    if report.promotion_blockers:
        print("promotion_blockers:")
        for blocker in report.promotion_blockers:
            print(f"  - {blocker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
