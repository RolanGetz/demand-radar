"""Reddit via its OAuth Data API, searching either site-wide or named subreddits.

Reddit no longer reliably permits anonymous JSON search and its terms require
authorized access. Configure an app client (client id + secret) or supply an
existing bearer token; without either, this collector raises and the run
isolates the failure.

Subreddits are the main reason to prefer Reddit over a site-wide search: demand
is concentrated in communities ("r/sales", "r/consulting"), and searching inside
them is both cheaper and far less noisy than ranking the whole site. When
subreddits are configured, each is searched separately with ``restrict_sr`` so
Reddit cannot widen the query back out to unrelated communities.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from demand_radar.data_plane.collectors.base import CollectedPage, Collector
from demand_radar.domain import Signal

_SEARCH = "https://oauth.reddit.com/search"
_SUBREDDIT_SEARCH = "https://oauth.reddit.com/r/{subreddit}/search"
_TOKEN_API = "https://www.reddit.com/api/v1/access_token"
_MAX_PER_PAGE = 100

#: Reddit's own window buckets. A narrower one than the research window keeps
#: the API from paging through years of history we would only discard locally.
_TIME_BUCKETS = (
    (1, "day"),
    (7, "week"),
    (31, "month"),
    (366, "year"),
)


class RedditCollector(Collector):
    name = "reddit"
    label = "Reddit"
    needs_config = True

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        access_token: str | None = None,
        subreddits: list[str] | None = None,
        include_comments: bool = False,
        **options,
    ):
        super().__init__(**options)
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.subreddits = normalize_subreddits(subreddits or [])
        self.include_comments = include_comments

    def collect(
        self,
        query: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CollectedPage:
        targets = self.subreddits or [None]
        cursors = _decode_cursor(cursor, targets)
        # Split the page budget across targets so one busy subreddit cannot
        # consume the whole limit and starve the others.
        per_target = max(1, min(limit, _MAX_PER_PAGE) // len(targets))

        signals: list[Signal] = []
        examined = 0
        next_cursors: dict[str, str] = {}

        with self._client() as client:
            self._authorize(client)
            for subreddit in targets:
                key = subreddit or ""
                if cursor is not None and key not in cursors:
                    continue  # this target was already exhausted on an earlier page
                page = self._fetch(
                    client,
                    query,
                    subreddit=subreddit,
                    limit=per_target,
                    after=cursors.get(key),
                    since=since,
                )
                listing = page.get("data", {})
                children = listing.get("children", [])
                examined += len(children)
                signals.extend(self._to_signals(children, query))
                after = listing.get("after")
                if after:
                    next_cursors[key] = after

        return CollectedPage(signals, _encode_cursor(next_cursors), examined=examined)

    def _fetch(
        self,
        client: httpx.Client,
        query: str,
        *,
        subreddit: str | None,
        limit: int,
        after: str | None,
        since: datetime | None,
    ) -> dict:
        params: dict[str, object] = {
            "q": query,
            "limit": min(limit, _MAX_PER_PAGE),
            "sort": "new",
            "type": "comment" if self.include_comments else "link",
        }
        if after:
            params["after"] = after
        bucket = _time_bucket(since)
        if bucket:
            params["t"] = bucket
        if subreddit:
            params["restrict_sr"] = "true"
            url = _SUBREDDIT_SEARCH.format(subreddit=subreddit)
        else:
            url = _SEARCH
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _to_signals(self, children: list[dict], query: str) -> list[Signal]:
        signals: list[Signal] = []
        for child in children:
            data = child.get("data", {})
            created_utc = data.get("created_utc")
            permalink = data.get("permalink")
            subreddit = data.get("subreddit")
            # A comment carries its text in `body`; a post in `selftext`.
            text = data.get("selftext") or data.get("body") or ""
            signals.append(
                Signal(
                    source=self.name,
                    source_id=data.get("name") or data.get("id"),
                    query=query,
                    author=data.get("author"),
                    title=data.get("title") or data.get("link_title") or None,
                    text=text,
                    url=(
                        f"https://www.reddit.com{permalink}" if permalink else data.get("url")
                    ),
                    created_at=(
                        datetime.fromtimestamp(created_utc, tz=timezone.utc)
                        if created_utc
                        else datetime.now(timezone.utc)
                    ),
                    score=data.get("score"),
                    community=f"r/{subreddit}" if subreddit else None,
                    raw=data,
                )
            )
        return signals

    def _authorize(self, client: httpx.Client) -> None:
        # Cache the minted token on the instance so a multi-page backfill (one
        # collector object across pages, several subreddits per page) does not
        # POST the rate-limited token endpoint once per request.
        if not self.access_token:
            self.access_token = self._app_token(client)
        client.headers["Authorization"] = f"Bearer {self.access_token}"

    def _app_token(self, client: httpx.Client) -> str:
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "Reddit requires OAuth. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET, "
                "or REDDIT_ACCESS_TOKEN."
            )
        response = client.post(
            _TOKEN_API,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError("Reddit OAuth response did not contain an access token")
        return token


def normalize_subreddits(values: list[str]) -> list[str]:
    """Accept ``r/sales``, ``/r/sales``, or ``sales`` and return bare names.

    Researchers write subreddits the way Reddit displays them, so the prefix is
    stripped here rather than demanded of the caller. Order is preserved and
    duplicates (including case variants) collapse.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        # Strip the leading slash first, then the prefix, then any trailing
        # slash: doing it in the other order turns a bare "r/" into "r".
        name = (value or "").strip().lstrip("/")
        if name[:2].casefold() == "r/":
            name = name[2:]
        name = name.strip("/")
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        cleaned.append(name)
    return cleaned


def _time_bucket(since: datetime | None) -> str | None:
    """Pick the narrowest Reddit time bucket that still covers the window."""
    if since is None:
        return None
    span_days = (datetime.now(timezone.utc) - since).days
    for threshold, bucket in _TIME_BUCKETS:
        if span_days <= threshold:
            return bucket
    return "all"


def _encode_cursor(cursors: dict[str, str]) -> str | None:
    """Pack per-subreddit ``after`` tokens into one opaque cursor string.

    The collection run treats a cursor as opaque, so several subreddits paging
    at different depths are carried in a single value. Targets that returned no
    ``after`` are omitted, which is how an exhausted subreddit drops out of
    later pages instead of being re-fetched from the start.

    A site-wide search has a single unnamed target, so its cursor stays the bare
    Reddit token — no key, no separator — which keeps the common case readable.
    """
    if not cursors:
        return None
    if set(cursors) == {""}:
        return cursors[""]
    return "|".join(f"{key}:{value}" for key, value in cursors.items())


def _decode_cursor(cursor: str | None, targets: list[str | None]) -> dict[str, str]:
    """Unpack a cursor, tolerating the bare site-wide form.

    A token without a ``name:`` prefix belongs to the unnamed site-wide target;
    treating it as malformed would silently skip that target and stop paging.
    """
    if not cursor:
        return {}
    decoded: dict[str, str] = {}
    for part in cursor.split("|"):
        key, separator, value = part.partition(":")
        if separator and value:
            decoded[key] = value
        elif part:
            decoded[""] = part
    return decoded
