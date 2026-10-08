"""The raw signal: one public utterance, normalised, with nothing inferred.

A :class:`Signal` is pure Data Plane. It holds what a source actually said and
never carries a verdict about relevance or meaning — those live in
:mod:`demand_radar.domain.classification`, keyed back to ``Signal.id``.

That split is what lets one collected dataset serve many hypotheses: a signal is
collected once, and each hypothesis classifies it independently without
rewriting (or losing) the original Voice of Customer.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class Signal(BaseModel):
    """A single public post, comment, or feed entry in normalised form.

    ``id`` is a stable content hash, so the same item collected twice — by two
    overlapping queries, or by a re-run of the same backfill — de-duplicates on
    arrival instead of accumulating near-copies.
    """

    id: str = ""
    source: str  # adapter name, e.g. "hackernews", "reddit", "rss"
    source_id: str | None = None  # the source's own identifier, when it has one
    query: str = ""  # the expanded query term this signal was retrieved by
    author: str | None = None
    title: str | None = None
    text: str = ""
    url: str | None = None
    created_at: datetime
    score: int | None = None  # upvotes / points, source-dependent
    lang: str | None = None  # source-declared language, if any; never guessed here
    community: str | None = None  # subreddit, feed title, site — the "where"
    raw: dict = Field(default_factory=dict)  # untouched source payload

    @field_validator("created_at")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        """Treat a tz-naive source timestamp as UTC.

        Some feeds and federated servers return offset-less timestamps. Left
        naive, they raise on comparison against an aware ``datetime`` (trend
        bucketing) or get silently shifted by the host's local offset when
        normalised for cursor bounds.
        """
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def model_post_init(self, __context) -> None:  # noqa: D401
        if not self.id:
            self.id = self.compute_id()

    @property
    def content(self) -> str:
        """Title + body — the text every analysis stage actually reads."""
        parts = [part for part in (self.title, self.text) if part]
        return "\n".join(parts).strip()

    def compute_id(self) -> str:
        """Hash the most stable identity available for this signal.

        Prefers the source's own id, then the URL, then the content itself. The
        query is deliberately excluded: the same post found by two different
        expanded queries is one signal, not two.
        """
        if self.source_id:
            basis = f"{self.source}:{self.source_id}"
        elif self.url:
            basis = self.url
        else:
            basis = f"{self.source}:{self.author}:{self.content[:200]}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
