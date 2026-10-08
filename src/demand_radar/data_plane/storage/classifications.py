"""The classification repository: derived verdicts, scoped per hypothesis.

Writes here never touch the ``signals`` table. Re-running a hypothesis replaces
its own verdicts and nothing else, so raw data is never lost to a reclassification
and two hypotheses can disagree about the same signal without conflict.
"""

from __future__ import annotations

import json
from datetime import datetime

from demand_radar.data_plane.storage.database import Database
from demand_radar.data_plane.storage.signals import row_to_signal
from demand_radar.domain import (
    Classification,
    CommercialIntent,
    PainType,
    Relevance,
    Signal,
    Urgency,
)

_COLUMNS = (
    "signal_id, hypothesis, relevant, confidence, reason, stage, pain_type, pain_summary, "
    "role, industry, b2b_context, native_language, conversation_language, commercial_intent, "
    "urgency, solution_seeking, competitor_mentioned, existing_solution_dissatisfaction, "
    "distribution_opportunity, quotes, model, classified_at"
)

_UPSERT = f"""
INSERT INTO classifications ({_COLUMNS})
VALUES ({",".join("?" * 22)})
ON CONFLICT(signal_id, hypothesis) DO UPDATE SET
    relevant = excluded.relevant,
    confidence = excluded.confidence,
    reason = excluded.reason,
    stage = excluded.stage,
    pain_type = excluded.pain_type,
    pain_summary = excluded.pain_summary,
    role = excluded.role,
    industry = excluded.industry,
    b2b_context = excluded.b2b_context,
    native_language = excluded.native_language,
    conversation_language = excluded.conversation_language,
    commercial_intent = excluded.commercial_intent,
    urgency = excluded.urgency,
    solution_seeking = excluded.solution_seeking,
    competitor_mentioned = excluded.competitor_mentioned,
    existing_solution_dissatisfaction = excluded.existing_solution_dissatisfaction,
    distribution_opportunity = excluded.distribution_opportunity,
    quotes = excluded.quotes,
    model = excluded.model,
    classified_at = excluded.classified_at
"""


class ClassificationRepository:
    def __init__(self, database: Database):
        self.db = database

    def save(self, classifications: list[Classification]) -> int:
        """Upsert verdicts; returns how many rows were written."""
        if not classifications:
            return 0
        rows = [_to_row(classification) for classification in classifications]
        with self.db.write() as cursor:
            cursor.executemany(_UPSERT, rows)
        return len(rows)

    def get(self, signal_id: str, hypothesis: str) -> Classification | None:
        with self.db.read() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM classifications WHERE signal_id = ? AND hypothesis = ?",
                (signal_id, hypothesis),
            )
            row = cursor.fetchone()
        return _to_classification(row) if row else None

    def list(
        self,
        hypothesis: str,
        *,
        relevant_only: bool = False,
        limit: int | None = None,
    ) -> list[Classification]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        sql = f"SELECT {_COLUMNS} FROM classifications WHERE hypothesis = ?"
        args: list = [hypothesis]
        if relevant_only:
            sql += " AND relevant = 1"
        sql += " ORDER BY classified_at DESC, signal_id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self.db.read() as cursor:
            cursor.execute(sql, args)
            return [_to_classification(row) for row in cursor.fetchall()]

    def list_with_signals(
        self,
        hypothesis: str,
        *,
        relevant_only: bool = True,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[tuple[Signal, Classification]]:
        """Join verdicts back to their source material.

        Every reported conclusion must be traceable to the original signal, so
        reporting reads the pair rather than the verdict alone.
        """
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        signal_columns = (
            "s.id, s.source, s.source_id, s.query, s.author, s.title, s.text, s.url, "
            "s.created_at, s.score, s.lang, s.community, s.raw"
        )
        classification_columns = ", ".join(
            f"c.{column.strip()}" for column in _COLUMNS.split(",")
        )
        sql = f"""
            SELECT {signal_columns}, {classification_columns}
            FROM classifications AS c
            JOIN signals AS s ON s.id = c.signal_id
            WHERE c.hypothesis = ?
        """
        args: list = [hypothesis]
        if relevant_only:
            sql += " AND c.relevant = 1"
        if since:
            sql += " AND s.created_at >= ?"
            args.append(since.isoformat())
        if until:
            sql += " AND s.created_at < ?"
            args.append(until.isoformat())
        sql += " ORDER BY s.created_at DESC, s.id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)

        with self.db.read() as cursor:
            cursor.execute(sql, args)
            return [(row_to_signal(row), _to_classification(row)) for row in cursor.fetchall()]

    def classified_ids(self, hypothesis: str) -> set[str]:
        with self.db.read() as cursor:
            cursor.execute(
                "SELECT signal_id FROM classifications WHERE hypothesis = ?", (hypothesis,)
            )
            return {row["signal_id"] for row in cursor.fetchall()}

    def delete_for(self, hypothesis: str) -> int:
        """Drop a hypothesis's verdicts. Collected signals are untouched."""
        with self.db.write() as cursor:
            cursor.execute("DELETE FROM classifications WHERE hypothesis = ?", (hypothesis,))
            return max(cursor.rowcount, 0)

    def hypotheses(self) -> list[str]:
        with self.db.read() as cursor:
            cursor.execute("SELECT DISTINCT hypothesis FROM classifications ORDER BY hypothesis")
            return [row["hypothesis"] for row in cursor.fetchall()]


def _to_row(classification: Classification) -> tuple:
    relevance = classification.relevance
    return (
        classification.signal_id,
        classification.hypothesis,
        int(relevance.relevant),
        relevance.confidence,
        relevance.reason,
        relevance.stage,
        _enum_value(classification.pain_type),
        classification.pain_summary,
        classification.role,
        classification.industry,
        _bool_value(classification.b2b_context),
        classification.native_language,
        classification.conversation_language,
        _enum_value(classification.commercial_intent),
        _enum_value(classification.urgency),
        _bool_value(classification.solution_seeking),
        classification.competitor_mentioned,
        _bool_value(classification.existing_solution_dissatisfaction),
        _bool_value(classification.distribution_opportunity),
        json.dumps(classification.quotes, ensure_ascii=False),
        classification.model,
        classification.classified_at.isoformat(),
    )


def _to_classification(row) -> Classification:
    return Classification(
        signal_id=row["signal_id"],
        hypothesis=row["hypothesis"],
        relevance=Relevance(
            relevant=bool(row["relevant"]),
            confidence=row["confidence"],
            reason=row["reason"] or "",
            stage=row["stage"] or "",
        ),
        pain_type=PainType(row["pain_type"]) if row["pain_type"] else None,
        pain_summary=row["pain_summary"],
        role=row["role"],
        industry=row["industry"],
        b2b_context=_to_bool(row["b2b_context"]),
        native_language=row["native_language"],
        conversation_language=row["conversation_language"],
        commercial_intent=(
            CommercialIntent(row["commercial_intent"]) if row["commercial_intent"] else None
        ),
        urgency=Urgency(row["urgency"]) if row["urgency"] else None,
        solution_seeking=_to_bool(row["solution_seeking"]),
        competitor_mentioned=row["competitor_mentioned"],
        existing_solution_dissatisfaction=_to_bool(row["existing_solution_dissatisfaction"]),
        distribution_opportunity=_to_bool(row["distribution_opportunity"]),
        quotes=json.loads(row["quotes"]) if row["quotes"] else [],
        model=row["model"],
        classified_at=datetime.fromisoformat(row["classified_at"]),
    )


def _enum_value(value) -> str | None:
    return value.value if value is not None else None


def _bool_value(value: bool | None) -> int | None:
    """Keep unknown as SQL NULL — never collapse it to False."""
    return None if value is None else int(value)


def _to_bool(value) -> bool | None:
    return None if value is None else bool(value)
