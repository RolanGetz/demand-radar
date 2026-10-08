"""Hacker News collector — HTTP mocked, so these run offline and deterministically."""

from datetime import datetime, timezone

import httpx
import respx

from demand_radar.data_plane.collectors import HackerNewsCollector

_API = "https://hn.algolia.com/api/v1/search_by_date"


@respx.mock
def test_parses_stories_and_comments():
    respx.get(_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "objectID": "111",
                        "title": "Negotiating in a second language is hard",
                        "author": "alice",
                        "points": 42,
                        "created_at_i": 1_700_000_000,
                    },
                    {
                        "objectID": "222",
                        "comment_text": "I <b>freeze</b> when they push back &amp; negotiating stalls",
                        "story_title": "Ask HN",
                        "author": "bob",
                        "created_at_i": 1_700_000_500,
                    },
                ]
            },
        )
    )
    page = HackerNewsCollector().collect("negotiating", limit=10)

    assert len(page.signals) == 2
    story, comment = page.signals
    assert story.source == "hackernews"
    assert story.source_id == "111"
    assert story.author == "alice"
    assert story.score == 42
    assert story.url == "https://news.ycombinator.com/item?id=111"
    assert story.created_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    assert story.community == "Hacker News"
    # markup stripped, entities decoded, author's wording otherwise intact
    assert comment.text == "I freeze when they push back & negotiating stalls"


@respx.mock
def test_skips_hits_that_do_not_contain_the_query():
    respx.get(_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": [
                    {"objectID": "1", "title": "about negotiating", "created_at_i": 1},
                    {"objectID": "2", "title": "unrelated gardening post", "created_at_i": 2},
                ]
            },
        )
    )
    page = HackerNewsCollector().collect("negotiating", limit=10)
    assert [signal.source_id for signal in page.signals] == ["1"]


@respx.mock
def test_raw_payload_is_preserved_for_later_analysis():
    hit = {"objectID": "7", "title": "negotiating", "created_at_i": 5, "_tags": ["story"]}
    respx.get(_API).mock(return_value=httpx.Response(200, json={"hits": [hit]}))
    page = HackerNewsCollector().collect("negotiating")
    assert page.signals[0].raw["_tags"] == ["story"]


@respx.mock
def test_window_bounds_become_numeric_filters():
    route = respx.get(_API).mock(return_value=httpx.Response(200, json={"hits": []}))
    HackerNewsCollector().collect(
        "negotiating",
        since=datetime(2024, 1, 1, tzinfo=timezone.utc),
        until=datetime(2024, 2, 1, tzinfo=timezone.utc),
    )
    numeric_filters = route.calls.last.request.url.params["numericFilters"]
    assert f"created_at_i<={int(datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp())}" in numeric_filters
    assert f"created_at_i>={int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())}" in numeric_filters


@respx.mock
def test_cursor_takes_precedence_over_the_window_upper_bound():
    """Paging must walk backwards from the last page, not restart at `until`."""
    route = respx.get(_API).mock(return_value=httpx.Response(200, json={"hits": []}))
    HackerNewsCollector().collect(
        "negotiating",
        cursor="1700000000",
        until=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    numeric_filters = route.calls.last.request.url.params["numericFilters"]
    assert "created_at_i<=1700000000" in numeric_filters
    assert str(int(datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp())) not in numeric_filters


@respx.mock
def test_no_numeric_filter_is_sent_without_bounds():
    route = respx.get(_API).mock(return_value=httpx.Response(200, json={"hits": []}))
    HackerNewsCollector().collect("negotiating")
    assert "numericFilters" not in route.calls.last.request.url.params


@respx.mock
def test_next_cursor_is_the_oldest_timestamp_when_more_pages_exist():
    respx.get(_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": [
                    {"objectID": "1", "title": "negotiating a", "created_at_i": 1_700_000_900},
                    {"objectID": "2", "title": "negotiating b", "created_at_i": 1_700_000_100},
                ],
                "page": 0,
                "nbPages": 3,
            },
        )
    )
    page = HackerNewsCollector().collect("negotiating")
    assert page.next_cursor == "1700000100"


@respx.mock
def test_no_cursor_on_the_last_page():
    respx.get(_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": [{"objectID": "1", "title": "negotiating", "created_at_i": 5}],
                "page": 2,
                "nbPages": 3,
            },
        )
    )
    assert HackerNewsCollector().collect("negotiating").next_cursor is None


@respx.mock
def test_cursor_is_derived_from_a_full_page_when_the_api_omits_page_counts():
    respx.get(_API).mock(
        return_value=httpx.Response(
            200,
            json={"hits": [{"objectID": str(i), "title": "negotiating", "created_at_i": 100 + i} for i in range(3)]},
        )
    )
    page = HackerNewsCollector().collect("negotiating", limit=3)
    assert page.next_cursor == "100"


@respx.mock
def test_timestamps_from_filtered_out_hits_still_bound_the_next_page():
    """Otherwise a page whose oldest hits all fail the match would skip them forever."""
    respx.get(_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": [
                    {"objectID": "1", "title": "negotiating", "created_at_i": 900},
                    {"objectID": "2", "title": "unrelated", "created_at_i": 100},
                ],
                "page": 0,
                "nbPages": 2,
            },
        )
    )
    page = HackerNewsCollector().collect("negotiating")
    assert len(page.signals) == 1
    assert page.next_cursor == "100"


@respx.mock
def test_http_error_propagates_for_the_run_to_isolate():
    respx.get(_API).mock(return_value=httpx.Response(503))
    try:
        HackerNewsCollector().collect("negotiating")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 503
    else:  # pragma: no cover
        raise AssertionError("expected HTTPStatusError")
