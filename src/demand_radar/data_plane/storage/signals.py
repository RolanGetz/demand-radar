"""The signal repository: raw collected data in, raw collected data out.

Deduplication lives here, in :meth:`SignalRepository.save`. Signals arrive with
a stable content-hash id, so the same post collected twice — by two expanded
queries, or by a re-run that overlaps an earlier page — converges on one row
instead of accumulating near-copies.
"""

from __future__ import annotations

import json
from datetime import datetime

from demand_radar.data_plane.storage.database import Database
from demand_radar.domain import Signal, utcnow

_COLUMNS = (
    "id, source, source_id, query, author, title, text, url, created_at, "
    "score, lang, community, raw, collected_at"
)

# A re-collection refreshes volatile fields (a score changes, a title is edited)
# but never rewrites `query` or `collected_at`: the first query that found a
# signal and the moment it entered the dataset are part of its provenance.
_UPSERT = f"""
INSERT INTO signals ({_COLUMNS})
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(id) DO UPDATE SET
    source     = excluded.source,
    source_id  = excluded.source_id,
    author     = excluded.author,
    title      = excluded.title,
    text       = excluded.text,
    url        = excluded.url,
    created_at = excluded.created_at,
    score      = excluded.score,
    lang       = COALESCE(excluded.lang, signals.lang),
    community  = COALESCE(excluded.community, signals.community),
    raw        = excluded.raw
"""


class SignalRepository:
    def __init__(self, database: Database):
        self.db = database

    def save(self, signals: list[Signal]) -> int:
        """Upsert signals, returning how many were newly stored.

        Duplicates within the batch collapse before the write, so a count of
        "new" reflects distinct signals rather than rows touched.
        """
        if not signals:
            return 0
        deduplicated = list({signal.id: signal for signal in signals}.values())
        collected_at = utcnow().isoformat()

        with self.db.write() as cursor:
            known = self._existing_ids(cursor, [signal.id for signal in deduplicated])
            cursor.executemany(
                _UPSERT,
                [
                    (
                        signal.id,
                        signal.source,
                        signal.source_id,
                        signal.query,
                        signal.author,
                        signal.title,
                        signal.text,
                        signal.url,
                        signal.created_at.isoformat(),
                        signal.score,
                        signal.lang,
                        signal.community,
                        json.dumps(signal.raw, ensure_ascii=False, default=str),
                        collected_at,
                    )
                    for signal in deduplicated
                ],
            )
        return len(deduplicated) - len(known)

    def get(self, signal_id: str) -> Signal | None:
        with self.db.read() as cursor:
            cursor.execute(f"SELECT {_COLUMNS} FROM signals WHERE id = ?", (signal_id,))
            row = cursor.fetchone()
        return row_to_signal(row) if row else None

    def list(
        self,
        *,
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        unclassified_for: str | None = None,
        limit: int | None = None,
    ) -> list[Signal]:
        """Read signals back, newest first.

        ``unclassified_for`` restricts the result to signals this hypothesis has
        not screened yet — the staged pipeline's way of never paying twice for
        the same verdict.
        """
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        sql = f"SELECT {_COLUMNS} FROM signals WHERE 1=1"
        args: list = []
        if source:
            sql += " AND source = ?"
            args.append(source)
        if since:
            sql += " AND created_at >= ?"
            args.append(since.isoformat())
        if until:
            sql += " AND created_at < ?"
            args.append(until.isoformat())
        if unclassified_for:
            sql += """
                AND NOT EXISTS (
                    SELECT 1 FROM classifications
                    WHERE classifications.signal_id = signals.id
                      AND classifications.hypothesis = ?
                )
            """
            args.append(unclassified_for)
        sql += " ORDER BY created_at DESC, id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)

        with self.db.read() as cursor:
            cursor.execute(sql, args)
            return [row_to_signal(row) for row in cursor.fetchall()]

    def count(self) -> int:
        with self.db.read() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM signals")
            return cursor.fetchone()["total"]

    def sources(self) -> dict[str, int]:
        """Signal counts per source, for coverage reporting."""
        with self.db.read() as cursor:
            cursor.execute(
                "SELECT source, COUNT(*) AS total FROM signals GROUP BY source ORDER BY total DESC"
            )
            return {row["source"]: row["total"] for row in cursor.fetchall()}

    @staticmethod
    def _existing_ids(cursor, ids: list[str]) -> set[str]:
        """Which ids are already stored, chunked under SQLite's variable limit."""
        found: set[str] = set()
        unique = list(dict.fromkeys(ids))
        for start in range(0, len(unique), 900):
            chunk = unique[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(f"SELECT id FROM signals WHERE id IN ({placeholders})", chunk)
            found.update(row["id"] for row in cursor.fetchall())
        return found


def row_to_signal(row) -> Signal:
    return Signal(
        id=row["id"],
        source=row["source"],
        source_id=row["source_id"],
        query=row["query"],
        author=row["author"],
        title=row["title"],
        text=row["text"] or "",
        url=row["url"],
        created_at=datetime.fromisoformat(row["created_at"]),
        score=row["score"],
        lang=row["lang"],
        community=row["community"],
        raw=json.loads(row["raw"]) if row["raw"] else {},
    )
