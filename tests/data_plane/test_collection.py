"""The collection run: paging, window correctness, retries, and failure isolation."""

from datetime import timedelta

import httpx
import pytest

from demand_radar.config import Config
from demand_radar.data_plane import CollectionService
from demand_radar.data_plane.collectors import REGISTRY, CollectedPage, Collector
from demand_radar.domain import Hypothesis, TimeWindow
from tests.constants import NOW

WINDOW = TimeWindow(start=NOW - timedelta(days=30), end=NOW)


class FakeCollector(Collector):
    """A scripted collector: each entry in `pages` is one response."""

    name = "fake"
    label = "Fake"
    pages: list = []
    calls: list = []

    def collect(self, query, *, limit=50, cursor=None, since=None, until=None):
        type(self).calls.append(
            {"query": query, "limit": limit, "cursor": cursor, "since": since, "until": until}
        )
        if not type(self).pages:
            return CollectedPage([], None)
        page = type(self).pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


@pytest.fixture
def fake_source(make_signal):
    """Register a scripted collector in the registry for the duration of a test."""
    FakeCollector.pages = []
    FakeCollector.calls = []
    REGISTRY["fake"] = FakeCollector
    yield FakeCollector
    del REGISTRY["fake"]


@pytest.fixture
def service(database):
    config = Config()
    config.retries = 2
    config.retry_backoff = 0.0
    config.per_source_limit = 25
    config.max_pages = 3
    slept = []
    service = CollectionService(config, database, sleep=slept.append)
    service.slept = slept
    return service


def _hypothesis(sources=("fake",), queries=("negotiating",)) -> Hypothesis:
    return Hypothesis(
        name="h1",
        statement="people struggling to negotiate in a second language",
        queries=list(queries),
        sources=list(sources),
    )


def test_collects_and_stores_signals(service, fake_source, make_signal):
    fake_source.pages = [CollectedPage([make_signal(), make_signal()], None)]
    report = service.collect(_hypothesis(), WINDOW)

    assert report.collected == 2
    assert report.new == 2
    assert report.by_source == {"fake": 2}
    assert report.ok
    assert service.signals.count() == 2


def test_recollecting_reports_no_new_signals(service, fake_source, make_signal):
    signal = make_signal()
    fake_source.pages = [CollectedPage([signal], None)]
    service.collect(_hypothesis(), WINDOW)
    fake_source.pages = [CollectedPage([signal], None)]
    report = service.collect(_hypothesis(), WINDOW)

    assert report.collected == 1
    assert report.new == 0
    assert service.signals.count() == 1


def test_the_window_is_passed_down_to_the_collector(service, fake_source):
    fake_source.pages = [CollectedPage([], None)]
    service.collect(_hypothesis(), WINDOW)
    call = fake_source.calls[0]
    assert call["since"] == WINDOW.start
    assert call["until"] == WINDOW.end
    assert call["limit"] == 25


def test_signals_outside_the_window_are_dropped(service, fake_source, make_signal):
    """Historical correctness: a source that ignores the bounds must not leak data in."""
    inside = make_signal(created_at=NOW - timedelta(days=2))
    future = make_signal(created_at=NOW + timedelta(days=1))
    fake_source.pages = [CollectedPage([inside, future], None)]

    report = service.collect(_hypothesis(), WINDOW)
    assert report.collected == 1
    assert report.skipped_out_of_window == 1
    assert [signal.id for signal in service.signals.list()] == [inside.id]


def test_paging_follows_the_cursor(service, fake_source, make_signal):
    fake_source.pages = [
        CollectedPage([make_signal()], "cursor-1"),
        CollectedPage([make_signal()], "cursor-2"),
        CollectedPage([make_signal()], None),
    ]
    report = service.collect(_hypothesis(), WINDOW)

    assert report.collected == 3
    assert report.pages_by_source == {"fake": 3}
    assert [call["cursor"] for call in fake_source.calls] == [None, "cursor-1", "cursor-2"]


def test_paging_stops_at_max_pages(service, fake_source, make_signal):
    fake_source.pages = [CollectedPage([make_signal()], f"c{i}") for i in range(10)]
    report = service.collect(_hypothesis(), WINDOW, max_pages=2)
    assert report.pages_by_source == {"fake": 2}
    assert report.collected == 2


def test_paging_stops_once_a_page_falls_entirely_before_the_window(
    service, fake_source, make_signal
):
    """No point asking for older pages when the current one already predates the window."""
    fake_source.pages = [
        CollectedPage([make_signal(created_at=NOW - timedelta(days=1))], "c1"),
        CollectedPage([make_signal(created_at=NOW - timedelta(days=400))], "c2"),
        CollectedPage([make_signal()], None),
    ]
    report = service.collect(_hypothesis(), WINDOW, max_pages=5)
    assert report.pages_by_source == {"fake": 2}
    assert report.collected == 1


def test_max_pages_is_validated(service, fake_source):
    with pytest.raises(ValueError, match="between 1 and 20"):
        service.collect(_hypothesis(), WINDOW, max_pages=0)
    with pytest.raises(ValueError, match="between 1 and 20"):
        service.collect(_hypothesis(), WINDOW, max_pages=21)


def test_duplicate_signals_across_pages_collapse(service, fake_source, make_signal):
    """The one-item cursor overlap some APIs produce must not double-count."""
    shared = make_signal()
    fake_source.pages = [
        CollectedPage([shared], "c1"),
        CollectedPage([shared, make_signal()], None),
    ]
    report = service.collect(_hypothesis(), WINDOW)
    assert report.collected == 2


def test_a_failing_source_does_not_sink_the_run(service, fake_source, make_signal):
    fake_source.pages = [RuntimeError("no credentials")]
    report = service.collect(_hypothesis(sources=("fake", "hackernews")), WINDOW)

    assert "fake" in report.errors
    assert "no credentials" in report.errors["fake"]
    assert not report.ok


def test_a_failure_is_recorded_against_the_source_state(service, fake_source):
    fake_source.pages = [RuntimeError("boom")]
    service.collect(_hypothesis(), WINDOW)
    state = service.state.get("h1", "negotiating", "fake")
    assert "boom" in state.last_error


def test_every_query_is_collected(service, fake_source, make_signal):
    fake_source.pages = [
        CollectedPage([make_signal()], None),
        CollectedPage([make_signal()], None),
    ]
    report = service.collect(_hypothesis(queries=("english", "englisch")), WINDOW)

    assert report.collected == 2
    assert [call["query"] for call in fake_source.calls] == ["english", "englisch"]


def test_one_failing_query_leaves_the_others_collected(service, fake_source, make_signal):
    fake_source.pages = [RuntimeError("boom"), CollectedPage([make_signal()], None)]
    report = service.collect(_hypothesis(queries=("bad", "good")), WINDOW)
    assert report.collected == 1
    assert "fake" in report.errors


def test_transient_errors_are_retried_then_succeed(service, fake_source, make_signal):
    fake_source.pages = [
        httpx.ConnectError("flaky"),
        CollectedPage([make_signal()], None),
    ]
    report = service.collect(_hypothesis(), WINDOW)

    assert report.collected == 1
    assert report.retries_by_source == {"fake": 1}
    assert report.ok


@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limits_and_server_errors_are_retried(service, fake_source, make_signal, status):
    error = httpx.HTTPStatusError(
        "server", request=httpx.Request("GET", "https://x.test"), response=httpx.Response(status)
    )
    fake_source.pages = [error, CollectedPage([make_signal()], None)]
    report = service.collect(_hypothesis(), WINDOW)
    assert report.collected == 1
    assert report.retries_by_source == {"fake": 1}


def test_client_errors_are_not_retried(service, fake_source):
    error = httpx.HTTPStatusError(
        "bad request",
        request=httpx.Request("GET", "https://x.test"),
        response=httpx.Response(400),
    )
    fake_source.pages = [error, CollectedPage([], None)]
    report = service.collect(_hypothesis(), WINDOW)

    assert "fake" in report.errors
    assert report.retries_by_source == {}


def test_retries_are_bounded_and_the_error_surfaces(service, fake_source):
    fake_source.pages = [httpx.ConnectError("down") for _ in range(10)]
    report = service.collect(_hypothesis(), WINDOW)

    assert "ConnectError" in report.errors["fake"]
    assert len(service.slept) == 2  # config.retries


def test_retry_after_header_sets_the_delay(fake_source, database):
    config = Config()
    config.retries = 1
    config.retry_backoff = 99.0
    slept = []
    service = CollectionService(config, database, sleep=slept.append)

    error = httpx.HTTPStatusError(
        "slow down",
        request=httpx.Request("GET", "https://x.test"),
        response=httpx.Response(429, headers={"retry-after": "7"}),
    )
    fake_source.pages = [error, CollectedPage([], None)]
    service.collect(_hypothesis(), WINDOW)
    assert slept == [7.0]


def test_backoff_grows_exponentially(fake_source, database):
    config = Config()
    config.retries = 3
    config.retry_backoff = 1.0
    slept = []
    service = CollectionService(config, database, sleep=slept.append)

    fake_source.pages = [httpx.ConnectError("down") for _ in range(5)]
    service.collect(_hypothesis(), WINDOW)
    assert slept == [1.0, 2.0, 4.0]


def test_collection_state_records_progress_for_resumption(service, fake_source, make_signal):
    fake_source.pages = [CollectedPage([make_signal()], "deep-cursor")]
    service.collect(_hypothesis(), WINDOW, max_pages=1)

    state = service.state.get("h1", "negotiating", "fake")
    assert state.cursor == "deep-cursor"
    assert state.complete is False
    assert state.last_error is None


def test_exhausting_a_source_marks_it_complete(service, fake_source, make_signal):
    fake_source.pages = [CollectedPage([make_signal()], None)]
    service.collect(_hypothesis(), WINDOW)
    assert service.state.get("h1", "negotiating", "fake").complete is True


def test_a_second_run_resumes_from_the_stored_cursor(service, fake_source, make_signal):
    fake_source.pages = [CollectedPage([make_signal()], "cursor-x")]
    service.collect(_hypothesis(), WINDOW, max_pages=1)

    fake_source.calls.clear()
    fake_source.pages = [CollectedPage([make_signal()], None)]
    service.collect(_hypothesis(), WINDOW, max_pages=1)
    assert fake_source.calls[0]["cursor"] == "cursor-x"


def test_an_unknown_source_is_reported_not_raised(service):
    report = service.collect(_hypothesis(sources=("nope",)), WINDOW)
    assert "unknown source" in report.errors["nope"]


def test_service_is_a_context_manager(fake_source, make_signal, tmp_path):
    config = Config()
    config.db_path = str(tmp_path / "radar.db")
    fake_source.pages = [CollectedPage([make_signal()], None)]
    with CollectionService(config, sleep=lambda _: None) as service:
        assert service.collect(_hypothesis(), WINDOW).new == 1


def test_hypothesis_options_reach_the_collector(service, fake_source, make_signal):
    """Subreddits and feeds are part of the research question, so they come from it."""
    captured = {}

    class OptionCapturingCollector(FakeCollector):
        def __init__(self, **options):
            super().__init__(**options)
            captured.update(options)

    REGISTRY["fake"] = OptionCapturingCollector
    fake_source.pages = [CollectedPage([make_signal()], None)]
    hypothesis = Hypothesis(
        name="h1",
        statement="s",
        queries=["negotiating"],
        sources=["fake"],
        source_options={"fake": {"subreddits": ["r/sales"], "include_comments": True}},
    )
    service.collect(hypothesis, WINDOW)

    assert captured["subreddits"] == ["r/sales"]
    assert captured["include_comments"] is True


def test_hypothesis_options_win_over_configured_ones(service, fake_source, make_signal):
    """Credentials come from the environment; what to search comes from the hypothesis."""
    captured = {}

    class OptionCapturingCollector(FakeCollector):
        def __init__(self, **options):
            super().__init__(**options)
            captured.update(options)

    REGISTRY["fake"] = OptionCapturingCollector
    service.config.source_options = lambda name: {"subreddits": ["r/fromconfig"], "token": "secret"}
    fake_source.pages = [CollectedPage([make_signal()], None)]
    hypothesis = Hypothesis(
        name="h1", statement="s", queries=["q"], sources=["fake"],
        source_options={"fake": {"subreddits": ["r/fromhypothesis"]}},
    )
    service.collect(hypothesis, WINDOW)

    assert captured["subreddits"] == ["r/fromhypothesis"]
    assert captured["token"] == "secret"  # credential preserved
