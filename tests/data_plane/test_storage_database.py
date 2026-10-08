"""Database lifecycle: schema creation, transactions, and file handling."""

import sqlite3

import pytest

from demand_radar.data_plane.storage import Database, SignalRepository


def test_schema_is_created_with_all_tables(database):
    with database.read() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = {row["name"] for row in cursor.fetchall()}
    assert {"signals", "classifications", "collection_state"} <= tables


def test_foreign_keys_are_enforced(database):
    with database.read() as cursor:
        cursor.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1


def test_reopening_an_existing_file_is_idempotent(tmp_path, make_signal):
    path = tmp_path / "radar.db"
    with Database(path) as database:
        SignalRepository(database).save([make_signal()])
    with Database(path) as database:
        assert SignalRepository(database).count() == 1


def test_parent_directories_are_created(tmp_path):
    path = tmp_path / "nested" / "deeper" / "radar.db"
    with Database(path) as database:
        assert database.path.endswith("radar.db")
    assert path.exists()


def test_a_failed_write_rolls_back(database, signals, make_signal):
    signals.save([make_signal()])
    with pytest.raises(sqlite3.IntegrityError):
        with database.write() as cursor:
            cursor.execute("DELETE FROM signals")
            cursor.execute(
                "INSERT INTO classifications (signal_id, hypothesis, relevant, classified_at) "
                "VALUES ('ghost', 'h', 1, '2024-01-01T00:00:00+00:00')"
            )
    assert signals.count() == 1


def test_successful_write_commits(database, signals, make_signal):
    signals.save([make_signal()])
    assert signals.count() == 1
