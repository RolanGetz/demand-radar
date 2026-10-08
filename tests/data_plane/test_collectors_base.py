"""Shared collector helpers."""

import pytest

from demand_radar.data_plane.collectors import REGISTRY, get_collector
from demand_radar.data_plane.collectors.base import (
    CollectedPage,
    Collector,
    matches_query,
    strip_html,
)


def test_strip_html_removes_tags_and_decodes_entities():
    assert strip_html("I tried <b>it</b> and it&#x27;s fast") == "I tried it and it's fast"


def test_strip_html_collapses_whitespace_and_handles_empty():
    assert strip_html("<p>a</p>\n\n  <p>b</p>") == "a b"
    assert strip_html("") == ""
    assert strip_html(None) == ""


def test_matches_query_is_case_insensitive_across_parts():
    assert matches_query("Acme", "about ACME corp", None)
    assert matches_query("acme", None, "we use Acme")
    assert not matches_query("acme", "something else", None)


def test_matches_query_ignores_missing_parts():
    assert not matches_query("acme", None, None)


def test_collected_page_defaults_to_empty_and_no_cursor():
    page = CollectedPage()
    assert page.signals == []
    assert page.next_cursor is None


def test_base_collector_requires_an_implementation():
    with pytest.raises(NotImplementedError):
        Collector().collect("acme")


def test_every_registered_collector_declares_its_identity():
    for name, collector_cls in REGISTRY.items():
        assert collector_cls.name == name
        assert collector_cls.label
        assert isinstance(collector_cls.needs_config, bool)


def test_get_collector_rejects_unknown_source():
    with pytest.raises(ValueError, match="unknown source"):
        get_collector("mastodon")


def test_get_collector_normalises_the_name():
    assert get_collector("  HackerNews  ").name == "hackernews"
