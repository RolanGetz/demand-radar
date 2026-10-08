"""Signal normalisation and identity tests."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from demand_radar.domain import Signal


def _signal(**overrides) -> Signal:
    base = {
        "source": "hackernews",
        "created_at": datetime(2024, 5, 1, tzinfo=timezone.utc),
        "title": "I freeze when the buyer pushes back",
        "text": "Happens every call in German.",
    }
    return Signal(**{**base, **overrides})


def test_content_joins_title_and_text():
    assert _signal().content == "I freeze when the buyer pushes back\nHappens every call in German."


def test_content_skips_missing_parts():
    assert _signal(title=None).content == "Happens every call in German."
    assert _signal(text="").content == "I freeze when the buyer pushes back"


def test_naive_timestamp_is_anchored_to_utc():
    signal = _signal(created_at=datetime(2024, 5, 1, 12, 0))
    assert signal.created_at == datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)


def test_aware_timestamp_is_converted_to_utc():
    offset = timezone(timedelta(hours=3))
    signal = _signal(created_at=datetime(2024, 5, 1, 12, 0, tzinfo=offset))
    assert signal.created_at == datetime(2024, 5, 1, 9, 0, tzinfo=timezone.utc)


def test_id_prefers_source_id_over_url():
    by_source_id = _signal(source_id="abc", url="https://example.com/1")
    assert by_source_id.id == Signal(
        source="hackernews",
        source_id="abc",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        title="totally different",
    ).id


def test_id_falls_back_to_url_then_content():
    by_url = _signal(url="https://example.com/post")
    same_url_different_text = _signal(url="https://example.com/post", text="other wording")
    assert by_url.id == same_url_different_text.id

    by_content = _signal()
    assert by_content.id != by_url.id
    assert by_content.id == _signal().id


def test_id_ignores_query_so_one_post_is_one_signal():
    """The same post retrieved by two expanded queries must de-duplicate."""
    first = _signal(url="https://example.com/x", query="negotiation in german")
    second = _signal(url="https://example.com/x", query="verhandlung englisch")
    assert first.id == second.id


def test_explicit_id_is_preserved():
    assert _signal(id="manual").id == "manual"


def test_raw_payload_is_preserved():
    signal = _signal(raw={"kind": "story", "nested": {"points": 3}})
    assert signal.raw["nested"]["points"] == 3


def test_created_at_is_required():
    with pytest.raises(ValidationError):
        Signal(source="rss")
