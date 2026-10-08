"""The on-disk schema. One SQLite file, no server — research data stays local.

Two tables carry the plane split: ``signals`` is append-and-refresh raw data
owned by the Data Plane, and ``classifications`` is derived output owned by the
Intelligence Plane. Deleting every classification loses no collected data, and
re-running a hypothesis never rewrites a signal — which is what makes one
dataset reusable across many research questions.
"""

from __future__ import annotations

#: Bumped whenever the statements below change shape.
SCHEMA_VERSION = 1

SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    source_id   TEXT,
    query       TEXT NOT NULL,
    author      TEXT,
    title       TEXT,
    text        TEXT NOT NULL DEFAULT '',
    url         TEXT,
    created_at  TEXT NOT NULL,
    score       INTEGER,
    lang        TEXT,
    community   TEXT,
    raw         TEXT NOT NULL DEFAULT '{}',
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_source ON signals(source);
CREATE INDEX IF NOT EXISTS idx_signals_community ON signals(community);
"""

# A signal is classified once per hypothesis, so the pair is the identity. The
# hypothesis is stored by name (not a foreign key) so collected data survives a
# hypothesis file being renamed, edited, or deleted.
CLASSIFICATIONS = """
CREATE TABLE IF NOT EXISTS classifications (
    signal_id   TEXT NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    hypothesis  TEXT NOT NULL,
    relevant    INTEGER NOT NULL,
    confidence  REAL NOT NULL DEFAULT 0,
    reason      TEXT NOT NULL DEFAULT '',
    stage       TEXT NOT NULL DEFAULT '',
    pain_type   TEXT,
    pain_summary TEXT,
    role        TEXT,
    industry    TEXT,
    b2b_context INTEGER,
    native_language      TEXT,
    conversation_language TEXT,
    commercial_intent    TEXT,
    urgency     TEXT,
    solution_seeking     INTEGER,
    competitor_mentioned TEXT,
    existing_solution_dissatisfaction INTEGER,
    distribution_opportunity INTEGER,
    quotes      TEXT NOT NULL DEFAULT '[]',
    model       TEXT,
    classified_at TEXT NOT NULL,
    PRIMARY KEY (signal_id, hypothesis)
);
CREATE INDEX IF NOT EXISTS idx_classifications_hypothesis
    ON classifications(hypothesis, relevant);
CREATE INDEX IF NOT EXISTS idx_classifications_pain ON classifications(hypothesis, pain_type);
"""

# Per (hypothesis, query, source) paging state, so a backfill resumes instead of
# refetching and a continuous radar only asks for what is new.
COLLECTION_STATE = """
CREATE TABLE IF NOT EXISTS collection_state (
    hypothesis  TEXT NOT NULL,
    query       TEXT NOT NULL,
    source      TEXT NOT NULL,
    cursor      TEXT,
    newest_at   TEXT,
    oldest_at   TEXT,
    complete    INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT,
    last_error  TEXT,
    PRIMARY KEY (hypothesis, query, source)
);
"""

ALL_STATEMENTS = SIGNALS + CLASSIFICATIONS + COLLECTION_STATE
