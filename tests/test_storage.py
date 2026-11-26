"""Step 02 local storage tests; no private content or external services."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from backend.storage import connection as storage
from backend.storage.connection import LocalDatabase, StorageBusyError, StorageError
from backend.storage.migrations import MIGRATIONS, Migration

LATEST_MIGRATION = max(item.version for item in MIGRATIONS)


def test_migrate_reopen_wal_and_foreign_keys(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "var")
    assert database.migrate() == LATEST_MIGRATION
    assert database.migrate() == LATEST_MIGRATION
    reopened = LocalDatabase(tmp_path / "var")
    with reopened.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (LATEST_MIGRATION,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (
            LATEST_MIGRATION,
        )


def test_forward_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    monkeypatch.setattr(
        storage,
        "MIGRATIONS",
        (
            *MIGRATIONS,
            Migration(
                LATEST_MIGRATION + 1,
                "test_forward",
                ("CREATE TABLE test_forward (id INTEGER)",),
            ),
        ),
    )
    assert database.migrate() == LATEST_MIGRATION + 1
    assert database.migrate() == LATEST_MIGRATION + 1
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM test_forward").fetchone() == (0,)
        assert connection.execute("PRAGMA user_version").fetchone() == (LATEST_MIGRATION + 1,)


def test_migration_failure_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    marker = "synthetic_sql_marker"
    monkeypatch.setattr(
        storage,
        "MIGRATIONS",
        (
            *MIGRATIONS,
            Migration(6, "broken", ("CREATE TABLE rolled_back (id INTEGER)", marker)),
        ),
    )
    with pytest.raises(StorageError) as caught:
        database.migrate()
    assert marker not in str(caught.value)
    with database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (LATEST_MIGRATION,)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (
            LATEST_MIGRATION,
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'rolled_back'"
            ).fetchone()
            is None
        )


def test_write_failure_rolls_back_and_hides_values(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    with pytest.raises(StorageError) as caught, database.write() as connection:
        connection.execute("INSERT INTO schema_metadata VALUES ('temporary', 'test')")
        connection.execute("INSERT INTO synthetic_secret_table VALUES ('synthetic-secret-marker')")
    assert "synthetic" not in str(caught.value)
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'temporary'"
            ).fetchone()
            is None
        )


def test_foreign_key_constraint_is_enforced(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "var")
    with database.write() as connection:
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
    with pytest.raises(StorageError), database.write() as connection:
        connection.execute("INSERT INTO child VALUES (7)")


@pytest.mark.parametrize(
    "database",
    [
        "../outside.db",
        "nested/../outside.db",
        "/tmp/outside.db",
        "file:test?mode=memory",
        "https://example.invalid/file.db",
        "",
        "./test.db",
        "folder//test.db",
        "test\\file.db",
    ],
)
def test_rejects_nonlocal_or_traversal_names(tmp_path: Path, database: str) -> None:
    with pytest.raises(StorageError):
        LocalDatabase(tmp_path / "var", database)


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_rejects_symlinks(tmp_path: Path, suffix: str) -> None:
    root = tmp_path / "var"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.touch()
    (root / f"broker.sqlite3{suffix}").symlink_to(outside)
    with pytest.raises(StorageError):
        LocalDatabase(root)


def test_rejects_symlink_root_and_checks_again_on_connect(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "var"
    root.symlink_to(outside)
    with pytest.raises(StorageError):
        LocalDatabase(root)
    root.unlink()
    database = LocalDatabase(root)
    root.symlink_to(outside)
    with pytest.raises(StorageError), database.connect():
        pass


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), float("inf")])
def test_rejects_invalid_writer_timeout(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(StorageError):
        LocalDatabase(tmp_path / "var", write_timeout=timeout)


def test_rejects_relative_root() -> None:
    with pytest.raises(StorageError):
        LocalDatabase(Path("var"))


@pytest.mark.parametrize("corruption", ["checksum", "gap", "version", "newer", "missing"])
def test_rejects_corrupt_or_future_history(tmp_path: Path, corruption: str) -> None:
    database = LocalDatabase(tmp_path / "var")
    database.migrate()
    with database.write() as connection:
        if corruption == "checksum":
            connection.execute("UPDATE schema_migrations SET checksum = 'wrong'")
        elif corruption == "gap":
            connection.execute("UPDATE schema_migrations SET version = 99 WHERE version = 1")
        elif corruption == "version":
            connection.execute("PRAGMA user_version = 0")
        elif corruption == "newer":
            connection.execute("PRAGMA user_version = 100")
        else:
            connection.execute("DROP TABLE schema_migrations")
    with pytest.raises(StorageError):
        database.migrate()


def test_packaged_migration_gaps_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "MIGRATIONS", (Migration(2, "gap", ()),))
    with pytest.raises(StorageError):
        LocalDatabase(tmp_path / "var").migrate()


def test_writer_lock_is_shared_and_bounded(tmp_path: Path) -> None:
    root = tmp_path / "var"
    first = LocalDatabase(root)
    second = LocalDatabase(root, write_timeout=0.05)
    entered = Event()
    release = Event()

    def hold_writer() -> None:
        with first.write():
            entered.set()
            assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(hold_writer)
        try:
            assert entered.wait(timeout=5)
            with pytest.raises(StorageBusyError), second.write():
                pass
        finally:
            release.set()
            worker.result(timeout=5)
    with second.write() as connection:
        connection.execute("CREATE TABLE available_again (id INTEGER)")


def test_sqlite_connection_always_closes(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "var")
    with database.connect() as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
