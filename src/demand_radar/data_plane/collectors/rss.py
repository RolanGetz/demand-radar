"""RSS/Atom collector — point it at any feed: blogs, forums, news, search alerts.

Feeds carry no search API, so the whole feed is fetched and entries are matched
locally against the query.
"""

from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone

import feedparser
import httpx

from demand_radar.data_plane.collectors.base import (
    CollectedPage,
    Collector,
    matches_query,
    strip_html,
)
from demand_radar.domain import Signal


class RSSCollector(Collector):
    name = "rss"
    label = "RSS / Atom"
    needs_config = True  # needs at least one feed URL

    def __init__(self, feeds: list[str] | None = None, **options):
        super().__init__(**options)
        self.feeds = list(feeds or [])

    def collect(
        self,
        query: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CollectedPage:
        if not self.feeds:
            raise RuntimeError("RSS requires at least one feed URL (DEMAND_RADAR_RSS_FEEDS)")

        signals: list[Signal] = []
        examined = 0
        with self._client() as client:
            for feed_url in self.feeds:
                try:
                    response = client.get(feed_url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue  # one bad or slow feed shouldn't cost the others their fetch
                matched, entry_count = self._parse_feed(response.content, feed_url, query)
                signals.extend(matched)
                examined += entry_count

        # Feeds are finite and have no paging token, so a feed collector is
        # always complete after one call: no cursor to continue from.
        return CollectedPage(signals[:limit], None, examined=examined)

    def _parse_feed(
        self, content: bytes, feed_url: str, query: str
    ) -> tuple[list[Signal], int]:
        parsed = feedparser.parse(content)
        community = (parsed.feed.get("title") or feed_url) if parsed.feed else feed_url
        signals: list[Signal] = []
        for entry in parsed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            if not matches_query(query, title, summary):
                continue
            signals.append(
                Signal(
                    source=self.name,
                    source_id=entry.get("id") or entry.get("link"),
                    query=query,
                    author=entry.get("author"),
                    title=title or None,
                    text=strip_html(summary),
                    url=entry.get("link"),
                    created_at=_entry_time(entry),
                    lang=_entry_language(parsed, entry),
                    community=community,
                )
            )
        return signals, len(parsed.entries)


def _entry_time(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        # `in` rather than .get(): feedparser synthesises a deprecated
        # updated->published fallback on attribute access, and warns.
        parsed_time = entry[key] if key in entry else None
        if parsed_time:
            # feedparser's struct_time is UTC; time.mktime() would read it as
            # local time and shift every timestamp on a non-UTC host.
            return datetime.fromtimestamp(timegm(parsed_time), tz=timezone.utc)
    return datetime.now(timezone.utc)


def _entry_language(parsed, entry) -> str | None:
    """Prefer the entry's declared language, else the feed's. Never guessed."""
    for candidate in (entry.get("language"), parsed.feed.get("language") if parsed.feed else None):
        if candidate:
            return str(candidate)
    return None
