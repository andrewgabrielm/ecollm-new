"""SQLite-backed observation persistence.

Writes are atomic per-row via the ``LocalDatabase.write`` context. The primary
key ``(run_id, task_id, model_id, model_version)`` blocks duplicates and gives
``contains`` cheap resume semantics. All reads validate rows through the
pydantic ``Observation`` model, so a schema mismatch surfaces as a validation
error rather than silently drifting.
"""

from __future__ import annotations

import json
import sqlite3

from backend.broker.feature_extractor import FeatureRow
from backend.contracts import ErrorCode, TaskCategory
from backend.learning.benchmarks.splits import SplitName
from backend.learning.collection.types import (
    OBSERVATION_SCHEMA_VERSION,
    Observation,
    ObservationStatus,
)
from backend.storage.connection import LocalDatabase


class ObservationStore:
    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def insert(self, observation: Observation) -> None:
        if observation.schema_version != OBSERVATION_SCHEMA_VERSION:
            raise ValueError("observation schema_version does not match this build")
        row = _to_row(observation)
        with self._database.write() as connection:
            connection.execute(
                "INSERT INTO observations ("
                "run_id, task_id, model_id, model_version, split, task_category, "
                "schema_version, feature_hash, features_json, status, error_code, "
                "quality, format_valid, input_tokens, output_tokens, total_ms, "
                "privacy_ms, routing_ms, generation_ms, inspection_ms, load_ms, "
                "prompt_ms, resident, policy_version, mock, created_at"
                ") VALUES ("
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?"
                ")",
                row,
            )

    def contains(self, *, run_id: str, task_id: str, model_id: str, model_version: str) -> bool:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "SELECT 1 FROM observations "
                "WHERE run_id = ? AND task_id = ? "
                "AND model_id = ? AND model_version = ? LIMIT 1",
                (run_id, task_id, model_id, model_version),
            )
            return cursor.fetchone() is not None

    def completed_keys(self, run_id: str) -> set[tuple[str, str, str]]:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "SELECT task_id, model_id, model_version FROM observations WHERE run_id = ?",
                (run_id,),
            )
            return {(row[0], row[1], row[2]) for row in cursor.fetchall()}

    def count(self, *, run_id: str | None = None) -> int:
        with self._database.connect() as connection:
            if run_id is None:
                cursor = connection.execute("SELECT COUNT(*) FROM observations")
            else:
                cursor = connection.execute(
                    "SELECT COUNT(*) FROM observations WHERE run_id = ?", (run_id,)
                )
            return int(cursor.fetchone()[0])

    def status_counts(self, run_id: str) -> dict[ObservationStatus, int]:
        counts: dict[ObservationStatus, int] = {status: 0 for status in ObservationStatus}
        with self._database.connect() as connection:
            cursor = connection.execute(
                "SELECT status, COUNT(*) FROM observations WHERE run_id = ? GROUP BY status",
                (run_id,),
            )
            for status_name, count in cursor.fetchall():
                counts[ObservationStatus(status_name)] = int(count)
        return counts

    def load_all(self, *, run_id: str | None = None) -> tuple[Observation, ...]:
        with self._database.connect() as connection:
            if run_id is None:
                cursor = connection.execute(_SELECT_ALL)
            else:
                cursor = connection.execute(_SELECT_ALL + " WHERE run_id = ?", (run_id,))
            return tuple(_from_row(row) for row in cursor.fetchall())


_SELECT_ALL = (
    "SELECT run_id, task_id, model_id, model_version, split, task_category, "
    "schema_version, feature_hash, features_json, status, error_code, quality, "
    "format_valid, input_tokens, output_tokens, total_ms, privacy_ms, "
    "routing_ms, generation_ms, inspection_ms, load_ms, prompt_ms, resident, "
    "policy_version, mock, created_at FROM observations"
)


def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _int_to_bool(value: int | None) -> bool | None:
    if value is None:
        return None
    return value != 0


def _to_row(observation: Observation) -> tuple[object, ...]:
    return (
        observation.run_id,
        observation.task_id,
        observation.model_id,
        observation.model_version,
        observation.split,
        observation.task_category.value,
        observation.schema_version,
        observation.feature_hash,
        (
            json.dumps(observation.features.model_dump(mode="json"), sort_keys=True)
            if observation.features is not None
            else None
        ),
        observation.status.value,
        observation.error_code.value if observation.error_code is not None else None,
        observation.quality,
        _bool_to_int(observation.format_valid),
        observation.input_tokens,
        observation.output_tokens,
        observation.total_ms,
        observation.privacy_ms,
        observation.routing_ms,
        observation.generation_ms,
        observation.inspection_ms,
        observation.load_ms,
        observation.prompt_ms,
        _bool_to_int(observation.resident),
        observation.policy_version,
        1 if observation.mock else 0,
        observation.created_at,
    )


def _from_row(row: sqlite3.Row | tuple[object, ...]) -> Observation:
    (
        run_id,
        task_id,
        model_id,
        model_version,
        split,
        task_category,
        schema_version,
        feature_hash,
        features_json,
        status,
        error_code,
        quality,
        format_valid,
        input_tokens,
        output_tokens,
        total_ms,
        privacy_ms,
        routing_ms,
        generation_ms,
        inspection_ms,
        load_ms,
        prompt_ms,
        resident,
        policy_version,
        mock,
        created_at,
    ) = row
    if schema_version != OBSERVATION_SCHEMA_VERSION:
        raise ValueError("stored observation schema_version differs from this build")
    features: FeatureRow | None = None
    if features_json is not None:
        features = FeatureRow.model_validate(json.loads(_as_str(features_json, "features_json")))
    payload: dict[str, object | None] = {
        "run_id": run_id,
        "task_id": task_id,
        "model_id": model_id,
        "model_version": model_version,
        "split": _validated_split(split),
        "task_category": TaskCategory(_as_str(task_category, "task_category")),
        "feature_hash": feature_hash,
        "features": features,
        "status": ObservationStatus(_as_str(status, "status")),
        "error_code": (
            ErrorCode(_as_str(error_code, "error_code")) if error_code is not None else None
        ),
        "quality": quality,
        "format_valid": _int_to_bool(_as_int(format_valid)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_ms": total_ms,
        "privacy_ms": privacy_ms,
        "routing_ms": routing_ms,
        "generation_ms": generation_ms,
        "inspection_ms": inspection_ms,
        "load_ms": load_ms,
        "prompt_ms": prompt_ms,
        "resident": _int_to_bool(_as_int(resident)),
        "policy_version": policy_version,
        "mock": bool(mock),
        "created_at": created_at,
    }
    return Observation.model_validate(payload)


def _as_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"stored observation {field} must be a string")
    return value


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, int):
        raise ValueError("stored observation integer column must be int or null")
    return value


def _validated_split(name: object) -> SplitName:
    if name not in ("dev_train", "dev_validation"):
        raise ValueError("stored observation has an unknown split name")
    return name  # type: ignore[return-value]
