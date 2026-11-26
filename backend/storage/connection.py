"""Local SQLite connections, bounded writes and trusted packaged migrations.

Paths are checked when each connection opens. This is a single-process local
storage boundary, not protection against an attacker controlling the machine.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
from threading import Lock

from backend.storage.migrations import MIGRATIONS, Migration

_LOCKS: dict[Path, Lock] = {}
_LOCKS_GUARD = Lock()
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "var"


class StorageError(RuntimeError):
    """Safe storage failure; messages contain neither SQL nor its parameters."""


class StorageBusyError(StorageError):
    """The bounded local writer wait expired."""


def _without_symlinks(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise StorageError("Storage paths must not contain symbolic links")


class LocalDatabase:
    """Keep one SQLite file beneath a configured local state directory.

    ``connect`` is intended for reads; use ``write`` for serialized transactions.
    Connections never load extensions, accept SQLite URIs or read migration SQL
    from caller-controlled paths. A write timeout is a lock-wait bound, not an
    execution deadline for arbitrary caller code inside the transaction.
    """

    def __init__(
        self,
        var_root: Path | None = None,
        database: str = "broker.sqlite3",
        write_timeout: float = 5.0,
    ) -> None:
        if (
            not database
            or ":" in database
            or "\\" in database
            or "\x00" in database
            or Path(database).is_absolute()
            or any(part in {"", ".", ".."} for part in database.split("/"))
        ):
            raise StorageError("Database must be a relative local filename")
        if not isfinite(write_timeout) or not 0 < write_timeout <= 60:
            raise StorageError("Writer timeout must be greater than zero and at most 60 seconds")
        root = _DEFAULT_ROOT if var_root is None else var_root
        if not root.is_absolute() or ".." in root.parts:
            raise StorageError("Storage root must be an absolute local path")
        _without_symlinks(root)
        self.root = root
        self.path = root / database
        self.write_timeout = write_timeout
        self._validate_paths()
        with _LOCKS_GUARD:
            self._write_lock = _LOCKS.setdefault(self.path, Lock())

    def _validate_paths(self) -> None:
        _without_symlinks(self.root)
        _without_symlinks(self.path)
        for suffix in ("-wal", "-shm", "-journal"):
            _without_symlinks(Path(str(self.path) + suffix))
        if self.path.exists() and not self.path.is_file():
            raise StorageError("Database path must identify a file")
        if self.root.exists() and not self.root.is_dir():
            raise StorageError("Storage root must identify a directory")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a configured connection and always close it on exit."""
        connection: sqlite3.Connection | None = None
        try:
            self._validate_paths()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._validate_paths()
            connection = sqlite3.connect(
                self.path,
                timeout=self.write_timeout,
                isolation_level=None,
            )
            connection.execute("PRAGMA foreign_keys = ON")
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if mode != ("wal",):
                raise StorageError("SQLite WAL mode is unavailable")
            yield connection
        except (sqlite3.Error, OSError):
            raise StorageError("Local SQLite operation failed") from None
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Serialize writers in this process and commit or roll back atomically."""
        if not self._write_lock.acquire(timeout=self.write_timeout):
            raise StorageBusyError("Local writer wait timed out")
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        finally:
            self._write_lock.release()

    def migrate(self) -> int:
        """Verify the existing history and atomically apply missing migrations."""
        _validate_migration_source(MIGRATIONS)
        with self.write() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > len(MIGRATIONS):
                raise StorageError("Database schema is newer than this application")
            existing = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
            if "schema_migrations" not in existing:
                if version != 0 or existing:
                    raise StorageError("Database migration history is missing")
                connection.execute(
                    "CREATE TABLE schema_migrations ("
                    "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL)"
                )
            history = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            if len(history) > len(MIGRATIONS):
                raise StorageError("Database schema is newer than this application")
            if len(history) != version:
                raise StorageError("Database migration version is inconsistent")
            for index, (applied_version, name, checksum) in enumerate(history):
                source = MIGRATIONS[index]
                if (applied_version, name, checksum) != (
                    source.version,
                    source.name,
                    source.checksum,
                ):
                    raise StorageError("Database migration history is inconsistent")
            for migration in MIGRATIONS[version:]:
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                    (migration.version, migration.name, migration.checksum),
                )
                # The integer comes only from the validated packaged source.
                connection.execute(f"PRAGMA user_version = {migration.version}")
            return len(MIGRATIONS)


def _validate_migration_source(migrations: tuple[Migration, ...]) -> None:
    if not migrations or tuple(item.version for item in migrations) != tuple(
        range(1, len(migrations) + 1)
    ):
        raise StorageError("Packaged migrations must have consecutive versions")
