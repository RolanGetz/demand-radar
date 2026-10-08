"""RSS/Atom collector — feed parsing, local matching, and per-feed failure isolation."""

import httpx
import pytest
import respx

from demand_radar.data_plane.collectors import RSSCollector

FEED_A = "https://example.test/a.xml"
FEED_B = "https://example.test/b.xml"


def _feed(*items, title="Example Feed", language=None):
    language_tag = f"<language>{language}</language>" if language else ""
    entries = "".join(
        f"""
        <item>
          <title>{item['title']}</title>
          <link>{item.get('link', 'https://example.test/post')}</link>
          <description>{item.get('description', '')}</description>
          <author>{item.get('author', '')}</author>
          <pubDate>{item.get('date', 'Tue, 14 Nov 2023 22:13:20 GMT')}</pubDate>
        </item>
        """
        for item in items
    )
    return f"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <title>{title}</title>{language_tag}{entries}
    </channel></rss>"""


@respx.mock
def test_parses_matching_entries_only():
    respx.get(FEED_A).mock(
        return_value=httpx.Response(
            200,
            content=_feed(
                {"title": "Negotiating in German", "description": "A <b>hard</b> call"},
                {"title": "Gardening tips", "description": "unrelated"},
            ),
        )
    )
    page = RSSCollector(feeds=[FEED_A]).collect("negotiating")

    assert len(page.signals) == 1
    signal = page.signals[0]
    assert signal.source == "rss"
    assert signal.title == "Negotiating in German"
    assert signal.text == "A hard call"  # markup stripped
    assert signal.community == "Example Feed"
    assert signal.created_at.year == 2023


def test_no_feeds_configured_is_an_actionable_error():
    with pytest.raises(RuntimeError, match="at least one feed URL"):
        RSSCollector().collect("negotiating")


@respx.mock
def test_a_failing_feed_does_not_cost_the_others_their_fetch():
    respx.get(FEED_A).mock(return_value=httpx.Response(500))
    respx.get(FEED_B).mock(
        return_value=httpx.Response(200, content=_feed({"title": "negotiating later"}))
    )
    page = RSSCollector(feeds=[FEED_A, FEED_B]).collect("negotiating")
    assert [signal.title for signal in page.signals] == ["negotiating later"]


@respx.mock
def test_a_network_error_is_skipped_too():
    respx.get(FEED_A).mock(side_effect=httpx.ConnectError("boom"))
    respx.get(FEED_B).mock(
        return_value=httpx.Response(200, content=_feed({"title": "negotiating"}))
    )
    page = RSSCollector(feeds=[FEED_A, FEED_B]).collect("negotiating")
    assert len(page.signals) == 1


@respx.mock
def test_declared_feed_language_is_recorded_but_never_guessed():
    respx.get(FEED_A).mock(
        return_value=httpx.Response(200, content=_feed({"title": "negotiating"}, language="de"))
    )
    with_language = RSSCollector(feeds=[FEED_A]).collect("negotiating")
    assert with_language.signals[0].lang == "de"

    respx.get(FEED_B).mock(
        return_value=httpx.Response(200, content=_feed({"title": "negotiating"}))
    )
    without_language = RSSCollector(feeds=[FEED_B]).collect("negotiating")
    assert without_language.signals[0].lang is None


@respx.mock
def test_entry_without_a_date_still_yields_a_signal():
    respx.get(FEED_A).mock(
        return_value=httpx.Response(
            200,
            content=_feed({"title": "negotiating", "date": ""}),
        )
    )
    page = RSSCollector(feeds=[FEED_A]).collect("negotiating")
    assert page.signals[0].created_at is not None


@respx.mock
def test_limit_truncates_across_feeds():
    respx.get(FEED_A).mock(
        return_value=httpx.Response(
            200,
            content=_feed(
                {"title": "negotiating one", "link": "https://example.test/1"},
                {"title": "negotiating two", "link": "https://example.test/2"},
            ),
        )
    )
    respx.get(FEED_B).mock(
        return_value=httpx.Response(
            200, content=_feed({"title": "negotiating three", "link": "https://example.test/3"})
        )
    )
    page = RSSCollector(feeds=[FEED_A, FEED_B]).collect("negotiating", limit=2)
    assert len(page.signals) == 2


@respx.mock
def test_feeds_are_complete_in_one_pass_so_there_is_no_cursor():
    respx.get(FEED_A).mock(
        return_value=httpx.Response(200, content=_feed({"title": "negotiating"}))
    )
    assert RSSCollector(feeds=[FEED_A]).collect("negotiating").next_cursor is None
