"""SQLite-backed preference profile storage. Single-user, one profile."""

from __future__ import annotations

import time

from backend.profiles.preferences import PreferenceProfile
from backend.storage.connection import LocalDatabase, StorageError


class ProfileStore:
    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    def load(self, profile_id: str = "local") -> PreferenceProfile:
        try:
            with self._db.connect() as conn:
                row = conn.execute(
                    "SELECT data FROM preferences WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()
        except StorageError:
            return PreferenceProfile(profile_id=profile_id)
        if row is None:
            return PreferenceProfile(profile_id=profile_id)
        try:
            return PreferenceProfile.model_validate_json(row[0])
        except Exception:
            return PreferenceProfile(profile_id=profile_id)

    def save(self, profile: PreferenceProfile) -> None:
        data = profile.model_dump_json()
        now = time.time()
        with self._db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO preferences (profile_id, data, updated_at) "
                "VALUES (?, ?, ?)",
                (profile.profile_id, data, now),
            )

    def reset(self, profile_id: str = "local") -> PreferenceProfile:
        default = PreferenceProfile(profile_id=profile_id)
        with self._db.write() as conn:
            conn.execute(
                "DELETE FROM preferences WHERE profile_id = ?",
                (profile_id,),
            )
        return default
