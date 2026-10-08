"""Shared fixtures: an in-memory database and signal/classification builders."""

from datetime import timedelta

import pytest

from demand_radar.data_plane.storage import (
    ClassificationRepository,
    CollectionStateRepository,
    Database,
    SignalRepository,
)
from demand_radar.domain import Classification, Relevance, Signal
from tests.constants import NOW


@pytest.fixture
def database():
    db = Database(":memory:")
    yield db
    db.close()


@pytest.fixture
def signals(database):
    return SignalRepository(database)


@pytest.fixture
def classifications(database):
    return ClassificationRepository(database)


@pytest.fixture
def collection_state(database):
    return CollectionStateRepository(database)


@pytest.fixture
def make_signal():
    """Build a Signal with a distinct identity per call unless overridden."""
    counter = {"n": 0}

    def _build(**overrides) -> Signal:
        counter["n"] += 1
        base = {
            "source": "hackernews",
            "source_id": f"id-{counter['n']}",
            "query": "negotiating in english",
            "author": "alice",
            "title": f"Signal {counter['n']}",
            "text": "I freeze when the buyer pushes back.",
            "created_at": NOW - timedelta(days=counter["n"]),
            "community": "Hacker News",
        }
        return Signal(**{**base, **overrides})

    return _build


@pytest.fixture
def make_classification():
    def _build(signal_id: str, hypothesis: str = "h1", **overrides) -> Classification:
        base = {
            "signal_id": signal_id,
            "hypothesis": hypothesis,
            "relevance": Relevance(relevant=True, confidence=0.8, reason="pain stated", stage="llm"),
        }
        return Classification(**{**base, **overrides})

    return _build
