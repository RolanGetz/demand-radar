"""The collection run: queries × sources → normalised, deduplicated signals.

This is the Data Plane's only entry point, and it is deliberately ignorant of
demand: it knows how to retrieve, page, retry, and store, and nothing about
relevance or pain. Anything it collects is available to every hypothesis.

A single source failing (rate limit, missing credential, network) never sinks
the run — the error is recorded against that source and the others still land.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from demand_radar.config import Config
from demand_radar.data_plane.collectors import CollectedPage, get_collector
from demand_radar.data_plane.storage import (
    CollectionStateRepository,
    Database,
    SignalRepository,
)
from demand_radar.domain import Hypothesis, Signal, TimeWindow

logger = logging.getLogger(__name__)

MAX_RETRY_DELAY = 60.0


@dataclass
class CollectionReport:
    """What a run actually did — per source, so gaps are visible, not silent."""

    hypothesis: str
    collected: int = 0
    new: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    pages_by_source: dict[str, int] = field(default_factory=dict)
    retries_by_source: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    skipped_out_of_window: int = 0
    #: Items a source returned that its adapter rejected as not about the query.
    #: Surfaced because loose source search is the usual reason a run finds nothing.
    skipped_off_query: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


class CollectionService:
    def __init__(
        self,
        config: Config | None = None,
        database: Database | None = None,
        *,
        sleep=time.sleep,
    ):
        self.config = config or Config()
        self.db = database or Database(self.config.db_path)
        self.signals = SignalRepository(self.db)
        self.state = CollectionStateRepository(self.db)
        # Injected so retry behaviour is testable without real delays.
        self._sleep = sleep

    def collect(
        self,
        hypothesis: Hypothesis,
        window: TimeWindow,
        *,
        max_pages: int | None = None,
    ) -> CollectionReport:
        """Run every (query, source) pair for a hypothesis over one window."""
        pages = max_pages if max_pages is not None else self.config.max_pages
        if not 1 <= pages <= 20:
            raise ValueError("max_pages must be between 1 and 20")

        report = CollectionReport(hypothesis=hypothesis.name)
        collected: list[Signal] = []

        for source_name in hypothesis.sources:
            for query in hypothesis.queries:
                try:
                    signals, pages_used, retries = self._collect_one(
                        hypothesis.name,
                        query,
                        source_name,
                        window,
                        pages,
                        report,
                        options=hypothesis.options_for(source_name),
                    )
                except Exception as exc:  # isolate per-source failure
                    message = f"{type(exc).__name__}: {exc}"
                    report.errors[source_name] = message
                    self.state.record_error(hypothesis.name, query, source_name, message)
                    logger.warning(
                        "collection failed for source=%s query=%r: %s", source_name, query, message
                    )
                    continue

                collected.extend(signals)
                report.by_source[source_name] = report.by_source.get(source_name, 0) + len(signals)
                report.pages_by_source[source_name] = (
                    report.pages_by_source.get(source_name, 0) + pages_used
                )
                if retries:
                    report.retries_by_source[source_name] = (
                        report.retries_by_source.get(source_name, 0) + retries
                    )

        report.collected = len(collected)
        report.new = self.signals.save(collected)
        logger.info(
            "collection complete hypothesis=%s collected=%d new=%d errors=%d",
            hypothesis.name,
            report.collected,
            report.new,
            len(report.errors),
        )
        return report

    def _collect_one(
        self,
        hypothesis_name: str,
        query: str,
        source_name: str,
        window: TimeWindow,
        max_pages: int,
        report: CollectionReport,
        options: dict | None = None,
    ) -> tuple[list[Signal], int, int]:
        """Page one (query, source) pair, resuming from stored state.

        Hypothesis options are merged *over* the configured ones: credentials
        come from the environment, while what to search (subreddits, feeds) comes
        from the research question.
        """
        collector_options = {
            **self.config.source_options(source_name),
            **(options or {}),
        }
        collector = get_collector(source_name, **collector_options)
        state = self.state.get(hypothesis_name, query, source_name)
        cursor = state.cursor
        signals: list[Signal] = []
        retries = 0
        pages_used = 0
        reached_window_start = False

        for _ in range(max_pages):
            page, page_retries = self._fetch_with_retries(
                collector, query, cursor=cursor, window=window
            )
            retries += page_retries
            pages_used += 1

            report.skipped_off_query += max(page.examined - len(page.signals), 0)
            in_window, out_of_window = _partition_by_window(page.signals, window)
            signals.extend(in_window)
            report.skipped_out_of_window += out_of_window

            # Sources page newest-first, so once a page falls entirely before
            # the window there is nothing older worth asking for.
            if out_of_window and not in_window:
                reached_window_start = True
                break
            if not page.next_cursor:
                break
            cursor = page.next_cursor

        unique = list({signal.id: signal for signal in signals}.values())
        timestamps = [signal.created_at for signal in unique]
        self.state.record_success(
            hypothesis_name,
            query,
            source_name,
            cursor=cursor,
            newest_at=max(timestamps) if timestamps else None,
            oldest_at=min(timestamps) if timestamps else None,
            complete=reached_window_start or cursor is None,
        )
        return unique, pages_used, retries

    def _fetch_with_retries(
        self, collector, query: str, *, cursor: str | None, window: TimeWindow
    ) -> tuple[CollectedPage, int]:
        """Fetch one page, retrying transient failures with exponential backoff."""
        limit = self.config.per_source_limit
        attempt = 0
        while True:
            try:
                return (
                    collector.collect(
                        query,
                        limit=limit,
                        cursor=cursor,
                        since=window.start,
                        until=window.end,
                    ),
                    attempt,
                )
            except Exception as exc:
                if attempt >= self.config.retries or not _retryable(exc):
                    raise
                delay = _retry_delay(exc, self.config.retry_backoff, attempt)
                attempt += 1
                logger.warning(
                    "retrying source=%s attempt=%d after %.1fs: %s",
                    collector.name,
                    attempt,
                    delay,
                    exc,
                )
                self._sleep(delay)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> CollectionService:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _partition_by_window(signals: list[Signal], window: TimeWindow) -> tuple[list[Signal], int]:
    """Split a page into in-window signals and a count of those outside it.

    Filtering here (not only in the source query) is what keeps historical
    research correct for sources whose API cannot bound a window server-side.
    """
    inside = [signal for signal in signals if window.contains(signal.created_at)]
    return inside, len(signals) - len(inside)


def _retryable(exc: Exception) -> bool:
    """Network blips, rate limits, and server errors are worth another attempt."""
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


def _retry_delay(exc: Exception, backoff: float, attempt: int) -> float:
    """Honour Retry-After when the server sends it, else exponential backoff."""
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("retry-after")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), MAX_RETRY_DELAY)
            except ValueError:
                pass
    return min(backoff * (2**attempt), MAX_RETRY_DELAY)


