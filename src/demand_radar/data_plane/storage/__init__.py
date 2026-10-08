"""Local-first persistence: one SQLite file, three focused repositories.

Raw signals and derived classifications live in separate tables with separate
repositories, so the Data Plane and the Intelligence Plane can be reasoned about
— and tested — independently.
"""

from __future__ import annotations

from demand_radar.data_plane.storage.classifications import ClassificationRepository
from demand_radar.data_plane.storage.collection_state import (
    CollectionState,
    CollectionStateRepository,
)
from demand_radar.data_plane.storage.database import Database
from demand_radar.data_plane.storage.signals import SignalRepository

__all__ = [
    "ClassificationRepository",
    "CollectionState",
    "CollectionStateRepository",
    "Database",
    "SignalRepository",
]
