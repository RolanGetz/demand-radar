"""The collector contract and shared HTTP/text helpers.

A collector's entire job is retrieval and normalisation: take a query and a
window, return :class:`~demand_radar.domain.signal.Signal` objects. Per the
roadmap, a source adapter must contain no business logic about demand
classification — no relevance verdicts, no pain types, no scoring. That keeps
adapters replaceable and makes collected data reusable across hypotheses.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from demand_radar.domain import Signal

USER_AGENT = "demand-radar/0.1 (+https://github.com/demand-radar/demand-radar)"
DEFAULT_TIMEOUT = 15.0

_TERM_PATTERN = re.compile(r"\w+", re.UNICODE)
#: Function words carry no retrieval signal and would otherwise make a natural
#: multi-word query impossible to satisfy.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is", "it",
        "my", "not", "of", "on", "or", "the", "to", "with",
        "de", "en", "la", "el", "los", "las", "um", "uma", "no", "na", "em",
        "der", "die", "das", "auf", "und", "mit", "le", "les", "du", "des", "au",
    }
)


@dataclass
class CollectedPage:
    """One page of signals plus an opaque cursor for the next, older page.

    The cursor is the source's own paging token (a timestamp, an ``after`` key,
    an offset) and is never interpreted by the caller — only stored and handed
    back, so a backfill can resume where it stopped.
    """

    signals: list[Signal] = field(default_factory=list)
    next_cursor: str | None = None
    #: How many items the source actually returned, before the adapter dropped
    #: any as off-query. Defaults to len(signals) when an adapter does not filter.
    examined: int | None = None

    def __post_init__(self) -> None:
        if self.examined is None:
            self.examined = len(self.signals)


class Collector:
    """Base class for a source adapter.

    Subclasses set :attr:`name` and implement :meth:`collect`. Raising is fine:
    the collection run isolates per-source failures so one rate-limited source
    never sinks the others.
    """

    name: str = "base"
    #: Human-readable, shown in CLI output and reports.
    label: str = "Base"
    #: Whether the source needs credentials or config to return anything.
    needs_config: bool = False

    def __init__(self, **options):
        self.options = options

    def collect(
        self,
        query: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> CollectedPage:
        """Retrieve one page of signals matching ``query``.

        ``since``/``until`` are best-effort window bounds: a source applies them
        server-side when its API allows, and the collection run filters the
        result regardless, so a source that ignores them stays correct.
        """
        raise NotImplementedError

    # -- helpers -------------------------------------------------------------
    def _client(self, **kwargs) -> httpx.Client:
        headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
        return httpx.Client(headers=headers, timeout=DEFAULT_TIMEOUT, **kwargs)


def strip_html(value: str) -> str:
    """Turn the small HTML fragments sources return into readable plain text.

    Voice of Customer is kept verbatim elsewhere; this only removes markup the
    source added around the author's own words.
    """
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def matches_query(query: str, *parts: str | None) -> bool:
    """Whether every term in the query appears somewhere in the given text.

    Several sources match loosely — Algolia ranks by relevance and will return
    items containing none of the query words, and a feed has no search at all —
    so adapters use this to drop results that are not about the query.

    Terms are required individually rather than as an exact phrase: a
    multi-word research query ("negotiating in english") is a description, and
    demanding the literal phrase would reject almost every real post that
    discusses it. This is retrieval hygiene, not relevance judgement — deciding
    what the text *means* is the Intelligence Plane's job.
    """
    haystack = " ".join(part for part in parts if part).casefold()
    if not haystack:
        return False
    terms = [term for term in _TERM_PATTERN.findall(query.casefold()) if term not in _STOPWORDS]
    if not terms:
        # A query of only stopwords or punctuation: fall back to substring.
        return query.casefold() in haystack
    return all(term in haystack for term in terms)
