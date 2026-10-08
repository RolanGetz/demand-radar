"""The Data Plane: sources → collectors → normalisation → deduplication → storage.

Nothing in this package knows what demand looks like. It retrieves and stores
public conversations as faithfully as it can; judgement happens in
:mod:`demand_radar.intelligence`. That boundary is what lets one collected
dataset serve many hypotheses without refetching.
"""

from __future__ import annotations

from demand_radar.data_plane.collection import CollectionReport, CollectionService

__all__ = ["CollectionReport", "CollectionService"]
