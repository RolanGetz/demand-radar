"""Pluggable source collectors.

Each collector implements :class:`~demand_radar.data_plane.collectors.base.Collector`
and is registered in :data:`REGISTRY`. Adding a source is one small class plus a
registry entry — no change to the pipeline, the store, or the Intelligence Plane.
"""

from __future__ import annotations

from demand_radar.data_plane.collectors.base import CollectedPage, Collector
from demand_radar.data_plane.collectors.hackernews import HackerNewsCollector
from demand_radar.data_plane.collectors.reddit import RedditCollector
from demand_radar.data_plane.collectors.rss import RSSCollector

REGISTRY: dict[str, type[Collector]] = {
    HackerNewsCollector.name: HackerNewsCollector,
    RedditCollector.name: RedditCollector,
    RSSCollector.name: RSSCollector,
}

#: Sources that work with zero configuration — no key, no account.
DEFAULT_SOURCES = [HackerNewsCollector.name]


def get_collector(name: str, **options) -> Collector:
    """Instantiate a registered collector by name."""
    collector_cls = REGISTRY.get(name.strip().lower())
    if collector_cls is None:
        available = ", ".join(sorted(REGISTRY))
        raise ValueError(f"unknown source {name!r}. Available: {available}")
    return collector_cls(**options)


__all__ = [
    "CollectedPage",
    "Collector",
    "DEFAULT_SOURCES",
    "HackerNewsCollector",
    "REGISTRY",
    "RSSCollector",
    "RedditCollector",
    "get_collector",
]
