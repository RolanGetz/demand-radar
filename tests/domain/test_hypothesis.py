"""Hypothesis parsing, validation, and time-window resolution."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from demand_radar.domain import Hypothesis, TimeWindow

NOW = datetime(2024, 7, 1, 12, 30, tzinfo=timezone.utc)

VALID_YAML = """
name: non-native-negotiation
statement: B2B professionals who struggle to negotiate in a non-native language.
queries:
  - negotiating in english
  - verhandeln auf englisch
sources:
  - hackernews
  - reddit
relevance_criteria: The author describes difficulty in a work conversation held in a second language.
languages: [en, de]
"""


def test_from_yaml_parses_all_fields():
    hypothesis = Hypothesis.from_yaml(VALID_YAML)
    assert hypothesis.name == "non-native-negotiation"
    assert hypothesis.queries == ["negotiating in english", "verhandeln auf englisch"]
    assert hypothesis.sources == ["hackernews", "reddit"]
    assert hypothesis.languages == ["en", "de"]
    assert "second language" in hypothesis.relevance_criteria


def test_from_yaml_rejects_non_mapping():
    with pytest.raises(ValueError, match="YAML mapping"):
        Hypothesis.from_yaml("- just\n- a list\n")


def test_queries_and_sources_are_required():
    with pytest.raises(ValidationError, match="at least one query"):
        Hypothesis(name="n", statement="s", sources=["rss"])
    with pytest.raises(ValidationError, match="at least one source"):
        Hypothesis(name="n", statement="s", queries=["q"])


def test_blank_and_duplicate_terms_are_cleaned_in_order():
    hypothesis = Hypothesis(
        name="n",
        statement="s",
        queries=[" second ", "first", "", "second", "  "],
        sources=["rss", "rss"],
    )
    assert hypothesis.queries == ["second", "first"]
    assert hypothesis.sources == ["rss"]


def test_empty_name_or_statement_is_rejected():
    with pytest.raises(ValidationError):
        Hypothesis(name="   ", statement="s", queries=["q"], sources=["rss"])
    with pytest.raises(ValidationError):
        Hypothesis(name="n", statement="", queries=["q"], sources=["rss"])


def test_languages_default_to_any():
    hypothesis = Hypothesis(name="n", statement="s", queries=["q"], sources=["rss"])
    assert hypothesis.languages == []


@pytest.mark.parametrize(
    ("period", "expected_days"),
    [("7d", 7), ("2w", 14), ("6m", 180), ("12m", 360), ("1y", 365)],
)
def test_period_shorthands_resolve_to_absolute_bounds(period, expected_days):
    window = TimeWindow.of_period(period, now=NOW)
    assert window.end == NOW
    assert window.start == NOW - timedelta(days=expected_days)


def test_today_starts_at_midnight():
    window = TimeWindow.of_period("today", now=NOW)
    assert window.start == datetime(2024, 7, 1, 0, 0, tzinfo=timezone.utc)
    assert window.end == NOW


@pytest.mark.parametrize("period", ["", "6", "6x", "x6", "-1d", "0d", "lastmonth"])
def test_invalid_periods_are_rejected(period):
    with pytest.raises(ValueError):
        TimeWindow.of_period(period, now=NOW)


def test_window_requires_start_before_end():
    with pytest.raises(ValidationError, match="before end"):
        TimeWindow(start=NOW, end=NOW)


def test_window_bounds_are_half_open():
    window = TimeWindow(start=NOW - timedelta(days=1), end=NOW)
    assert window.contains(window.start)
    assert not window.contains(window.end)
    assert window.contains(NOW - timedelta(hours=1))
    assert not window.contains(NOW - timedelta(days=2))


def test_window_contains_normalises_naive_input():
    window = TimeWindow.of_period("7d", now=NOW)
    assert window.contains(datetime(2024, 6, 28, 12, 0))


def test_window_normalises_naive_bounds_to_utc():
    window = TimeWindow(start=datetime(2024, 1, 1), end=datetime(2024, 2, 1))
    assert window.start.tzinfo == timezone.utc


# -- per-source retrieval options ------------------------------------------
SUBREDDIT_YAML = """
name: sales-pushback
statement: Sales people who freeze when a buyer pushes back in a second language.
queries: [pushback]
sources: [reddit]
source_options:
  reddit:
    subreddits: [r/sales, r/consulting]
    include_comments: true
"""


def test_source_options_are_parsed_from_yaml():
    hypothesis = Hypothesis.from_yaml(SUBREDDIT_YAML)
    assert hypothesis.options_for("reddit") == {
        "subreddits": ["r/sales", "r/consulting"],
        "include_comments": True,
    }


def test_source_options_default_to_empty():
    hypothesis = Hypothesis(name="n", statement="s", queries=["q"], sources=["reddit"])
    assert hypothesis.source_options == {}
    assert hypothesis.options_for("reddit") == {}


def test_options_for_is_case_insensitive_and_unknown_sources_are_empty():
    hypothesis = Hypothesis.from_yaml(SUBREDDIT_YAML)
    assert hypothesis.options_for("  REDDIT ") == hypothesis.options_for("reddit")
    assert hypothesis.options_for("hackernews") == {}


def test_source_option_keys_are_normalised():
    hypothesis = Hypothesis(
        name="n", statement="s", queries=["q"], sources=["reddit"],
        source_options={" Reddit ": {"subreddits": ["r/sales"]}},
    )
    assert "reddit" in hypothesis.source_options


def test_a_non_mapping_source_option_is_rejected():
    with pytest.raises(ValidationError, match="valid dictionary"):
        Hypothesis(
            name="n", statement="s", queries=["q"], sources=["reddit"],
            source_options={"reddit": ["r/sales"]},
        )
