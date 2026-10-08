"""Domain models shared by both planes.

This package holds the vocabulary of the system and nothing else: no I/O, no
HTTP, no SQL, no model calls. Both the Data Plane and the Intelligence Plane
depend on it, and it depends on neither — which is what keeps the two planes
independently testable and replaceable.
"""

from __future__ import annotations

from demand_radar.domain.classification import (
    Classification,
    CommercialIntent,
    PainType,
    Relevance,
    Urgency,
)
from demand_radar.domain.hypothesis import Hypothesis, TimeWindow
from demand_radar.domain.signal import Signal, utcnow

__all__ = [
    "Classification",
    "CommercialIntent",
    "Hypothesis",
    "PainType",
    "Relevance",
    "Signal",
    "TimeWindow",
    "Urgency",
    "utcnow",
]
