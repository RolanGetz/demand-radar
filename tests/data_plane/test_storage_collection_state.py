"""Collection state: resumable paging and non-shrinking coverage bounds."""

from datetime import timedelta

from tests.constants import NOW


def test_first_run_gets_a_blank_state(collection_state):
    state = collection_state.get("h1", "q1", "hackernews")
    assert state.cursor is None
    assert state.newest_at is None
    assert state.complete is False
    assert state.last_error is None


def test_success_persists_the_cursor_for_resumption(collection_state):
    collection_state.record_success(
        "h1", "q1", "hackernews",
        cursor="1700000000", newest_at=NOW, oldest_at=NOW - timedelta(days=5), complete=False,
    )
    state = collection_state.get("h1", "q1", "hackernews")
    assert state.cursor == "1700000000"
    assert state.newest_at == NOW
    assert state.oldest_at == NOW - timedelta(days=5)
    assert state.complete is False
    assert state.last_success_at is not None


def test_completion_is_recorded(collection_state):
    collection_state.record_success(
        "h1", "q1", "rss", cursor=None, newest_at=NOW, oldest_at=NOW, complete=True
    )
    assert collection_state.get("h1", "q1", "rss").complete is True


def test_coverage_bounds_only_widen(collection_state):
    """A later, narrower run must not shrink the range already known to be covered."""
    collection_state.record_success(
        "h1", "q1", "hackernews",
        cursor="a", newest_at=NOW, oldest_at=NOW - timedelta(days=30), complete=False,
    )
    collection_state.record_success(
        "h1", "q1", "hackernews",
        cursor="b",
        newest_at=NOW - timedelta(days=10),
        oldest_at=NOW - timedelta(days=12),
        complete=False,
    )
    state = collection_state.get("h1", "q1", "hackernews")
    assert state.newest_at == NOW
    assert state.oldest_at == NOW - timedelta(days=30)


def test_newer_signals_extend_the_upper_bound(collection_state):
    collection_state.record_success(
        "h1", "q1", "hackernews",
        cursor=None, newest_at=NOW - timedelta(days=5), oldest_at=NOW - timedelta(days=9), complete=False,
    )
    collection_state.record_success(
        "h1", "q1", "hackernews", cursor=None, newest_at=NOW, oldest_at=NOW - timedelta(days=1), complete=False
    )
    state = collection_state.get("h1", "q1", "hackernews")
    assert state.newest_at == NOW
    assert state.oldest_at == NOW - timedelta(days=9)


def test_a_run_with_no_results_keeps_existing_bounds(collection_state):
    collection_state.record_success(
        "h1", "q1", "hackernews", cursor="a", newest_at=NOW, oldest_at=NOW - timedelta(days=3), complete=False
    )
    collection_state.record_success(
        "h1", "q1", "hackernews", cursor="b", newest_at=None, oldest_at=None, complete=False
    )
    state = collection_state.get("h1", "q1", "hackernews")
    assert state.newest_at == NOW
    assert state.oldest_at == NOW - timedelta(days=3)


def test_error_is_recorded_on_a_first_run(collection_state):
    collection_state.record_error("h1", "q1", "reddit", "RuntimeError: no credentials")
    state = collection_state.get("h1", "q1", "reddit")
    assert state.last_error == "RuntimeError: no credentials"
    assert state.cursor is None


def test_error_does_not_discard_progress_already_made(collection_state):
    collection_state.record_success(
        "h1", "q1", "hackernews", cursor="deep-cursor", newest_at=NOW, oldest_at=NOW, complete=False
    )
    collection_state.record_error("h1", "q1", "hackernews", "HTTPStatusError: 503")
    state = collection_state.get("h1", "q1", "hackernews")
    assert state.cursor == "deep-cursor"
    assert state.last_error == "HTTPStatusError: 503"


def test_a_later_success_clears_a_stale_error(collection_state):
    collection_state.record_error("h1", "q1", "hackernews", "boom")
    collection_state.record_success(
        "h1", "q1", "hackernews", cursor=None, newest_at=NOW, oldest_at=NOW, complete=True
    )
    assert collection_state.get("h1", "q1", "hackernews").last_error is None


def test_long_errors_are_truncated(collection_state):
    collection_state.record_error("h1", "q1", "rss", "x" * 5000)
    assert len(collection_state.get("h1", "q1", "rss").last_error) == 500


def test_state_is_isolated_per_hypothesis_query_and_source(collection_state):
    collection_state.record_success(
        "h1", "q1", "hackernews", cursor="c1", newest_at=NOW, oldest_at=NOW, complete=False
    )
    assert collection_state.get("h2", "q1", "hackernews").cursor is None
    assert collection_state.get("h1", "q2", "hackernews").cursor is None
    assert collection_state.get("h1", "q1", "reddit").cursor is None


def test_for_hypothesis_lists_every_tracked_pair(collection_state):
    collection_state.record_success(
        "h1", "q1", "hackernews", cursor=None, newest_at=NOW, oldest_at=NOW, complete=True
    )
    collection_state.record_error("h1", "q2", "reddit", "boom")
    collection_state.record_success(
        "h2", "other", "rss", cursor=None, newest_at=NOW, oldest_at=NOW, complete=True
    )

    states = collection_state.for_hypothesis("h1")
    assert [(state.query, state.source) for state in states] == [
        ("q1", "hackernews"),
        ("q2", "reddit"),
    ]
