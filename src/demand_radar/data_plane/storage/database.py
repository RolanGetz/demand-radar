"""SQLite connection handling shared by the repositories.

:class:`Database` owns the connection and the schema; it holds no queries of its
own. Repositories borrow the connection, which keeps one transaction boundary
per logical write while letting each repository stay small and independently
testable.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from demand_radar.data_plane.storage.schema import ALL_STATEMENTS, SCHEMA_VERSION

MEMORY = ":memory:"


class Database:
    def __init__(self, path: str | Path = "demand-radar.db"):
        raw_path = str(path)
        self.path = raw_path if raw_path == MEMORY else str(Path(raw_path).expanduser())
        if self.path != MEMORY:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.execute("PRAGMA foreign_keys = ON")
        if self.path != MEMORY:
            # WAL lets a long read (a report) run while a collection writes.
            self.connection.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with closing(self.connection.cursor()) as cursor:
            cursor.execute("PRAGMA user_version")
            if cursor.fetchone()[0] >= SCHEMA_VERSION:
                return
            cursor.executescript(ALL_STATEMENTS)
            cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.connection.commit()

    @contextmanager
    def write(self):
        """Run a write in one immediate transaction, rolling back on failure."""
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            yield cursor
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
        finally:
            cursor.close()

    @contextmanager
    def read(self):
        cursor = self.connection.cursor()
        try:
            yield cursor
        finally:
            cursor.close()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
