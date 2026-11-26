"""SQLite-backed telemetry store with bounded retention and explicit delete.

Records are opt-in; the caller decides whether consent is in force. Every row
flows through :class:`TelemetryRecord`, so unknown fields are rejected before
they ever reach SQLite. Retention is enforced at insert time by trimming
records older than a configurable number of days; callers may also delete all
records explicitly.
"""

from __future__ import annotations

import json
import sqlite3
from time import time

from backend.api.schemas import CacheStatus, PrivacyTypeCount
from backend.contracts import ErrorCode, TaskCategory
from backend.storage.connection import LocalDatabase
from backend.telemetry.types import (
    TELEMETRY_SCHEMA_VERSION,
    RouteLabel,
    TelemetryOutcome,
    TelemetryRecord,
)


class TelemetryStore:
    def __init__(self, database: LocalDatabase, *, retention_days: int = 30) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        self._database = database
        self._retention_seconds = float(retention_days) * 86400.0
        self.retention_days = retention_days

    def insert(self, record: TelemetryRecord) -> None:
        if record.schema_version != TELEMETRY_SCHEMA_VERSION:
            raise ValueError("Telemetry schema_version mismatch")
        TelemetryRecord.model_validate(record.model_dump(mode="json", warnings=False))
        pii_payload = json.dumps(
            [item.model_dump(mode="json") for item in record.pii_type_counts],
            sort_keys=True,
        )
        cutoff = time() - self._retention_seconds
        with self._database.write() as connection:
            connection.execute("DELETE FROM telemetry WHERE created_at < ?", (cutoff,))
            connection.execute(
                "INSERT OR REPLACE INTO telemetry ("
                "schema_version, request_id, created_at, task, route, reason, "
                "model_id, model_version, router_type, router_version, outcome, "
                "error_code, cache_status, total_ms, privacy_ms, routing_ms, "
                "generation_ms, input_tokens, output_tokens, pii_type_counts_json, "
                "mock, policy_version"
                ") VALUES ("
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
                ")",
                (
                    record.schema_version,
                    str(record.request_id),
                    record.created_at,
                    record.task.value,
                    record.route.value,
                    record.reason,
                    record.model_id,
                    record.model_version,
                    record.router_type,
                    record.router_version,
                    record.outcome.value,
                    record.error_code.value if record.error_code is not None else None,
                    record.cache_status.value,
                    record.total_ms,
                    record.privacy_ms,
                    record.routing_ms,
                    record.generation_ms,
                    record.input_tokens,
                    record.output_tokens,
                    pii_payload,
                    1 if record.mock else 0,
                    record.policy_version,
                ),
            )

    def count(self) -> int:
        with self._database.connect() as connection:
            cursor = connection.execute("SELECT COUNT(*) FROM telemetry")
            return int(cursor.fetchone()[0])

    def iter_rows(self) -> tuple[TelemetryRecord, ...]:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "SELECT schema_version, request_id, created_at, task, route, reason, "
                "model_id, model_version, router_type, router_version, outcome, "
                "error_code, cache_status, total_ms, privacy_ms, routing_ms, "
                "generation_ms, input_tokens, output_tokens, pii_type_counts_json, "
                "mock, policy_version FROM telemetry ORDER BY created_at DESC"
            )
            return tuple(_row_to_record(row) for row in cursor.fetchall())

    def delete_all(self) -> int:
        with self._database.write() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0])
            connection.execute("DELETE FROM telemetry")
            return count

    def prune_expired(self, *, now: float | None = None) -> int:
        cutoff = (now if now is not None else time()) - self._retention_seconds
        with self._database.write() as connection:
            cursor = connection.execute("DELETE FROM telemetry WHERE created_at < ?", (cutoff,))
            return int(cursor.rowcount)


def _row_to_record(row: sqlite3.Row | tuple[object, ...]) -> TelemetryRecord:
    (
        schema_version,
        request_id,
        created_at,
        task,
        route,
        reason,
        model_id,
        model_version,
        router_type,
        router_version,
        outcome,
        error_code,
        cache_status,
        total_ms,
        privacy_ms,
        routing_ms,
        generation_ms,
        input_tokens,
        output_tokens,
        pii_type_counts_json,
        mock,
        policy_version,
    ) = row
    if schema_version != TELEMETRY_SCHEMA_VERSION:
        raise ValueError("Telemetry schema_version mismatch (stored row)")
    pii_items = json.loads(_as_str(pii_type_counts_json, "pii_type_counts_json"))
    if not isinstance(pii_items, list):
        raise ValueError("Telemetry pii_type_counts_json must decode to a list")
    pii_counts = tuple(PrivacyTypeCount.model_validate(item) for item in pii_items)
    router_type_str = _as_router_type(router_type)
    return TelemetryRecord(
        request_id=_as_str(request_id, "request_id"),  # type: ignore[arg-type]
        created_at=float(_coerce_number(created_at)),
        task=TaskCategory(_as_str(task, "task")),
        route=RouteLabel(_as_str(route, "route")),
        reason=_as_str(reason, "reason"),
        model_id=_opt_str(model_id),
        model_version=_opt_str(model_version),
        router_type="lightgbm" if router_type_str == "lightgbm" else "rules",
        router_version=_as_str(router_version, "router_version"),
        outcome=TelemetryOutcome(_as_str(outcome, "outcome")),
        error_code=(
            ErrorCode(_as_str(error_code, "error_code")) if error_code is not None else None
        ),
        cache_status=CacheStatus(_as_str(cache_status, "cache_status")),
        total_ms=_opt_float(total_ms),
        privacy_ms=_opt_float(privacy_ms),
        routing_ms=_opt_float(routing_ms),
        generation_ms=_opt_float(generation_ms),
        input_tokens=_opt_int(input_tokens),
        output_tokens=_opt_int(output_tokens),
        pii_type_counts=pii_counts,
        mock=bool(mock),
        policy_version=_as_str(policy_version, "policy_version"),
    )


def _as_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"stored telemetry {field} must be a string")
    return value


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    return _as_str(value, "optional_str")


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    return float(_coerce_number(value))


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, int):
        raise ValueError("stored telemetry integer column must be int or null")
    return value


def _coerce_number(value: object) -> int | float:
    if isinstance(value, bool):
        raise ValueError("stored telemetry numeric column must not be boolean")
    if isinstance(value, (int, float)):
        return value
    raise ValueError("stored telemetry numeric column must be int, float or null")


def _as_router_type(value: object) -> str:
    text = _as_str(value, "router_type")
    if text not in ("rules", "lightgbm"):
        raise ValueError("stored telemetry router_type must be rules or lightgbm")
    return text
