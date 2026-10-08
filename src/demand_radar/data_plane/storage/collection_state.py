"""Per-source paging state, so collection resumes instead of refetching.

Each (hypothesis, query, source) triple remembers where its last run stopped.
A backfill walks older pages from ``cursor`` until the source reports no more;
a continuous radar asks only for what is newer than ``newest_at``. This is what
makes "reuse collected data across many hypotheses without fetching the same
content repeatedly" true in practice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from demand_radar.data_plane.storage.database import Database
from demand_radar.domain import utcnow


@dataclass
class CollectionState:
    hypothesis: str
    query: str
    source: str
    cursor: str | None = None
    newest_at: datetime | None = None
    oldest_at: datetime | None = None
    complete: bool = False
    last_success_at: datetime | None = None
    last_error: str | None = None


class CollectionStateRepository:
    def __init__(self, database: Database):
        self.db = database

    def get(self, hypothesis: str, query: str, source: str) -> CollectionState:
        """Return stored state, or a blank state for a first run."""
        with self.db.read() as cursor:
            cursor.execute(
                """
                SELECT cursor, newest_at, oldest_at, complete, last_success_at, last_error
                FROM collection_state
                WHERE hypothesis = ? AND query = ? AND source = ?
                """,
                (hypothesis, query, source),
            )
            row = cursor.fetchone()
        if row is None:
            return CollectionState(hypothesis=hypothesis, query=query, source=source)
        return CollectionState(
            hypothesis=hypothesis,
            query=query,
            source=source,
            cursor=row["cursor"],
            newest_at=_parse(row["newest_at"]),
            oldest_at=_parse(row["oldest_at"]),
            complete=bool(row["complete"]),
            last_success_at=_parse(row["last_success_at"]),
            last_error=row["last_error"],
        )

    def record_success(
        self,
        hypothesis: str,
        query: str,
        source: str,
        *,
        cursor: str | None,
        newest_at: datetime | None,
        oldest_at: datetime | None,
        complete: bool,
    ) -> None:
        """Persist progress after a successful fetch.

        The timestamp bounds only ever widen: a run that collected nothing (or
        a narrower slice) must not shrink the range already known to be covered.
        """
        now = utcnow().isoformat()
        with self.db.write() as db_cursor:
            db_cursor.execute(
                """
                INSERT INTO collection_state
                    (hypothesis, query, source, cursor, newest_at, oldest_at, complete,
                     last_success_at, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(hypothesis, query, source) DO UPDATE SET
                    cursor = excluded.cursor,
                    newest_at = MAX(COALESCE(collection_state.newest_at, ''), COALESCE(excluded.newest_at, '')),
                    oldest_at = CASE
                        WHEN collection_state.oldest_at IS NULL THEN excluded.oldest_at
                        WHEN excluded.oldest_at IS NULL THEN collection_state.oldest_at
                        ELSE MIN(collection_state.oldest_at, excluded.oldest_at)
                    END,
                    complete = excluded.complete,
                    last_success_at = excluded.last_success_at,
                    last_error = NULL
                """,
                (
                    hypothesis,
                    query,
                    source,
                    cursor,
                    newest_at.isoformat() if newest_at else None,
                    oldest_at.isoformat() if oldest_at else None,
                    int(complete),
                    now,
                ),
            )

    def record_error(self, hypothesis: str, query: str, source: str, error: str) -> None:
        """Record a failure without discarding the cursor already reached."""
        with self.db.write() as cursor:
            cursor.execute(
                """
                INSERT INTO collection_state (hypothesis, query, source, last_error)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(hypothesis, query, source) DO UPDATE SET
                    last_error = excluded.last_error
                """,
                (hypothesis, query, source, error[:500]),
            )

    def for_hypothesis(self, hypothesis: str) -> list[CollectionState]:
        with self.db.read() as cursor:
            cursor.execute(
                "SELECT query, source FROM collection_state WHERE hypothesis = ? ORDER BY query, source",
                (hypothesis,),
            )
            keys = [(row["query"], row["source"]) for row in cursor.fetchall()]
        return [self.get(hypothesis, query, source) for query, source in keys]


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
