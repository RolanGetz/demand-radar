"""The Intelligence Plane: hypotheses → relevance → classification → reporting.

Staged by cost on purpose. The cheap local filter runs over everything; the
expensive structured model runs only over what survives, and only once per
(signal, hypothesis) pair. Nothing here collects data — it reads what the Data
Plane already stored, so a new hypothesis costs model calls, not refetching.
"""

from __future__ import annotations

from demand_radar.intelligence.relevance import CheapRelevanceFilter, FilterStats
from demand_radar.intelligence.screening import ScreeningReport, ScreeningService

__all__ = [
    "CheapRelevanceFilter",
    "FilterStats",
    "ScreeningReport",
    "ScreeningService",
]
