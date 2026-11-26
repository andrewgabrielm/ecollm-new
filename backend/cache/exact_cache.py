"""Bounded exact cache backed by SQLite. Off by default; opt-in per profile."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from backend.storage.connection import LocalDatabase, StorageError

MAX_ENTRIES = 500
DEFAULT_TTL_SECONDS = 86400 * 7


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    answer: str
    model_id: str
    model_version: str
    task: str
    created_at: float


class ExactCache:
    def __init__(
        self,
        db: LocalDatabase,
        *,
        max_entries: int = MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("Cache bounds must be positive")
        self._db = db
        self._max_entries = max_entries
        self._ttl = ttl_seconds

    def lookup(self, cache_key: str) -> CachedAnswer | None:
        cutoff = time.time() - self._ttl
        try:
            with self._db.connect() as conn:
                row = conn.execute(
                    "SELECT answer, model_id, model_version, task, created_at "
                    "FROM exact_cache WHERE cache_key = ? AND created_at > ?",
                    (cache_key, cutoff),
                ).fetchone()
        except StorageError:
            return None
        if row is None:
            return None
        return CachedAnswer(
            answer=row[0],
            model_id=row[1],
            model_version=row[2],
            task=row[3],
            created_at=row[4],
        )

    def store(
        self,
        cache_key: str,
        answer: str,
        model_id: str,
        model_version: str,
        task: str,
    ) -> None:
        now = time.time()
        try:
            with self._db.write() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO exact_cache "
                    "(cache_key, answer, model_id, model_version, task, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (cache_key, answer, model_id, model_version, task, now),
                )
                self._evict(conn)
        except StorageError:
            pass

    def delete_all(self) -> int:
        try:
            with self._db.write() as conn:
                cursor = conn.execute("DELETE FROM exact_cache")
                return cursor.rowcount
        except StorageError:
            return 0

    def count(self) -> int:
        try:
            with self._db.connect() as conn:
                row = conn.execute("SELECT COUNT(*) FROM exact_cache").fetchone()
                return row[0] if row else 0
        except StorageError:
            return 0

    def _evict(self, conn: sqlite3.Connection) -> None:
        cutoff = time.time() - self._ttl
        conn.execute("DELETE FROM exact_cache WHERE created_at <= ?", (cutoff,))
        conn.execute(
            "DELETE FROM exact_cache WHERE cache_key NOT IN "
            "(SELECT cache_key FROM exact_cache ORDER BY created_at DESC LIMIT ?)",
            (self._max_entries,),
        )
