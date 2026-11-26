"""Step 15 telemetry store tests — synthetic data only, no prompt content."""

from __future__ import annotations

from pathlib import Path
from time import time as _time
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from backend.api.schemas import CacheStatus, PrivacyTypeCount
from backend.contracts import ErrorCode, TaskCategory
from backend.storage.connection import LocalDatabase
from backend.telemetry import TelemetryRecord, TelemetryStore
from backend.telemetry.types import RouteLabel, TelemetryOutcome
from backend.versions import POLICY_VERSION


def _store(tmp_path: Path, *, retention_days: int = 30) -> TelemetryStore:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    return TelemetryStore(database, retention_days=retention_days)


def _record(
    *,
    created_at: float | None = None,
    request_id: UUID | None = None,
    task: TaskCategory = TaskCategory.QA,
    route: RouteLabel = RouteLabel.LOCAL,
    model_id: str | None = "mock-general",
    model_version: str | None = "v1",
    router_type: str = "rules",
    router_version: str = "rules-v1",
    outcome: TelemetryOutcome = TelemetryOutcome.SUCCESS,
    cache_status: CacheStatus = CacheStatus.DISABLED,
    total_ms: float | None = 42.0,
    privacy_ms: float | None = 1.5,
    routing_ms: float | None = 0.5,
    generation_ms: float | None = 10.0,
    input_tokens: int | None = 20,
    output_tokens: int | None = 5,
    pii_type_counts: tuple[PrivacyTypeCount, ...] = (),
    reason: str = "rules_ranked",
) -> TelemetryRecord:
    return TelemetryRecord(
        request_id=request_id or uuid4(),
        created_at=created_at if created_at is not None else _time(),
        task=task,
        route=route,
        reason=reason,
        model_id=model_id,
        model_version=model_version,
        router_type=router_type,  # type: ignore[arg-type]
        router_version=router_version,
        outcome=outcome,
        cache_status=cache_status,
        total_ms=total_ms,
        privacy_ms=privacy_ms,
        routing_ms=routing_ms,
        generation_ms=generation_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pii_type_counts=pii_type_counts,
        mock=True,
        policy_version=POLICY_VERSION,
    )


class TestTelemetryStore:
    def test_insert_and_roundtrip(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        record = _record()
        store.insert(record)
        rows = store.iter_rows()
        assert len(rows) == 1
        assert rows[0].request_id == record.request_id
        assert rows[0].total_ms == record.total_ms

    def test_unknown_field_rejected(self, tmp_path: Path) -> None:
        _store(tmp_path)
        with pytest.raises(ValidationError):
            TelemetryRecord.model_validate(
                {**_record().model_dump(mode="json"), "prompt": "never store this"}
            )

    def test_delete_all_clears_table(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.insert(_record())
        store.insert(_record())
        removed = store.delete_all()
        assert removed == 2
        assert store.count() == 0

    def test_retention_prunes_older_records(self, tmp_path: Path) -> None:
        store = _store(tmp_path, retention_days=1)
        fresh = _record(created_at=_time())
        store.insert(fresh)
        # Insert a second record with a timestamp 10 days in the past; the next insert
        # with a current timestamp should prune it as part of its retention sweep.
        old = _record(created_at=_time() - 10 * 86400)
        store.insert(old)
        store.insert(_record(created_at=_time()))
        # Retention kicks in on each insert; the stale record is dropped on the final insert.
        assert store.count() == 2
        ids = {row.request_id for row in store.iter_rows()}
        assert fresh.request_id in ids
        assert old.request_id not in ids

    def test_no_prompt_text_in_stored_bytes(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.insert(_record(reason="rules_ranked"))
        root = tmp_path / "var" / "broker.sqlite3"
        contents = root.read_bytes()
        secret_markers = [
            b"THIS IS A SECRET PROMPT",
            b"Simple factual question",
            b"[[PII_",
        ]
        for marker in secret_markers:
            assert marker not in contents

    def test_record_rejects_extra_keys(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryRecord(
                request_id=uuid4(),
                created_at=1.0,
                task=TaskCategory.QA,
                route=RouteLabel.LOCAL,
                reason="rules_ranked",
                router_type="rules",
                router_version="rules-v1",
                outcome=TelemetryOutcome.SUCCESS,
                policy_version=POLICY_VERSION,
                extra_field="forbidden",  # type: ignore[call-arg]
            )

    def test_schema_version_mismatch_raises(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        record = _record()
        bad = record.model_copy(update={"schema_version": "telemetry-v99"})
        with pytest.raises(ValueError):
            store.insert(bad)

    def test_error_code_persisted(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        record = _record().model_copy(
            update={
                "outcome": TelemetryOutcome.UNAVAILABLE,
                "model_id": None,
                "model_version": None,
                "route": RouteLabel.NONE,
                "reason": "unavailable",
                "total_ms": None,
                "privacy_ms": None,
                "routing_ms": None,
                "generation_ms": None,
                "input_tokens": None,
                "output_tokens": None,
                "pii_type_counts": (),
            }
        )
        record = record.model_copy(update={"error_code": ErrorCode.UNAVAILABLE})
        store.insert(record)
        rows = store.iter_rows()
        assert rows[0].error_code == ErrorCode.UNAVAILABLE
