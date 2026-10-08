"""Hacker News via the public Algolia API — no credentials required.

This is the zero-config workhorse: it works on a clean clone with nothing set
up, which makes it the collector every other part of the system is demoed and
tested against.
"""

from __future__ import annotations

from datetime import datetime, timezone

from demand_radar.data_plane.collectors.base import (
    CollectedPage,
    Collector,
    matches_query,
    strip_html,
)
from demand_radar.domain import Signal

_API = "https://hn.algolia.com/api/v1/search_by_date"
_MAX_PER_PAGE = 100


class HackerNewsCollector(Collector):
    name = "hackernews"
    label = "Hacker News"
    needs_config = False

    def collect(
        self,
        query: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CollectedPage:
        params = {
            "query": query,
            "tags": "(story,comment)",
            "hitsPerPage": min(limit, _MAX_PER_PAGE),
            "typoTolerance": "false",
        }
        params["numericFilters"] = _numeric_filters(cursor, since, until)
        if not params["numericFilters"]:
            del params["numericFilters"]

        with self._client() as client:
            response = client.get(_API, params=params)
            response.raise_for_status()
            payload = response.json()

        hits = payload.get("hits", [])
        signals: list[Signal] = []
        timestamps: list[int] = []
        for hit in hits:
            created_at_i = hit.get("created_at_i")
            if created_at_i:
                timestamps.append(int(created_at_i))
            title = hit.get("title") or hit.get("story_title") or ""
            body = strip_html(hit.get("comment_text") or hit.get("story_text") or "")
            if not matches_query(query, title, body):
                continue
            object_id = hit.get("objectID")
            signals.append(
                Signal(
                    source=self.name,
                    source_id=str(object_id) if object_id else None,
                    query=query,
                    author=hit.get("author"),
                    title=title or None,
                    text=body,
                    url=(
                        f"https://news.ycombinator.com/item?id={object_id}"
                        if object_id
                        else hit.get("url")
                    ),
                    created_at=(
                        datetime.fromtimestamp(created_at_i, tz=timezone.utc)
                        if created_at_i
                        else datetime.now(timezone.utc)
                    ),
                    score=hit.get("points"),
                    community="Hacker News",
                    raw=hit,
                )
            )

        return CollectedPage(
            signals, _next_cursor(payload, hits, limit, timestamps), examined=len(hits)
        )


def _numeric_filters(
    cursor: str | None, since: datetime | None, until: datetime | None
) -> str:
    """Build Algolia's numeric bounds from the cursor and the research window."""
    boundaries: list[str] = []
    upper = until
    if cursor:
        # Inclusive (<=) so items sharing the previous page's oldest second are
        # not skipped when the page cap splits that group. The one-second
        # overlap de-duplicates on the stable signal id, and the bounded page
        # loop stops the pathological all-same-second case.
        boundaries.append(f"created_at_i<={int(cursor)}")
    elif upper:
        boundaries.append(f"created_at_i<={int(upper.timestamp())}")
    if since:
        boundaries.append(f"created_at_i>={int(since.timestamp())}")
    return ",".join(boundaries)


def _next_cursor(
    payload: dict, hits: list[dict], limit: int, timestamps: list[int]
) -> str | None:
    """Oldest timestamp on this page, when the API says more pages exist."""
    if "nbPages" in payload:
        has_more = payload.get("page", 0) + 1 < payload.get("nbPages", 1)
    else:
        has_more = len(hits) >= min(limit, _MAX_PER_PAGE)
    return str(min(timestamps)) if has_more and timestamps else None
