"""Signal persistence: deduplication, provenance, and window reads."""

from datetime import timedelta

import pytest

from tests.constants import NOW


def test_save_returns_the_number_of_new_signals(signals, make_signal):
    assert signals.save([make_signal(), make_signal()]) == 2
    assert signals.count() == 2


def test_saving_nothing_is_a_no_op(signals):
    assert signals.save([]) == 0
    assert signals.count() == 0


def test_resaving_the_same_signal_does_not_duplicate_it(signals, make_signal):
    signal = make_signal()
    assert signals.save([signal]) == 1
    assert signals.save([signal]) == 0
    assert signals.count() == 1


def test_duplicates_inside_one_batch_collapse(signals, make_signal):
    signal = make_signal()
    assert signals.save([signal, signal, signal]) == 1
    assert signals.count() == 1


def test_the_same_post_found_by_two_queries_is_one_signal(signals, make_signal):
    """Deduplication is by content identity, so overlapping queries converge."""
    first = make_signal(source_id="shared", query="negotiating in english")
    second = make_signal(source_id="shared", query="verhandeln auf englisch")
    signals.save([first])
    assert signals.save([second]) == 0
    assert signals.count() == 1


def test_recollection_refreshes_volatile_fields(signals, make_signal):
    signals.save([make_signal(source_id="s1", score=5, title="before")])
    signals.save([make_signal(source_id="s1", score=99, title="after")])
    stored = signals.list()[0]
    assert stored.score == 99
    assert stored.title == "after"


def test_recollection_preserves_the_original_query_provenance(signals, make_signal):
    signals.save([make_signal(source_id="s1", query="first query")])
    signals.save([make_signal(source_id="s1", query="second query")])
    assert signals.list()[0].query == "first query"


def test_recollection_does_not_erase_known_language_or_community(signals, make_signal):
    signals.save([make_signal(source_id="s1", lang="de", community="r/sales")])
    signals.save([make_signal(source_id="s1", lang=None, community=None)])
    stored = signals.list()[0]
    assert stored.lang == "de"
    assert stored.community == "r/sales"


def test_round_trip_preserves_every_field_including_raw(signals, make_signal):
    original = make_signal(
        source="reddit",
        author="bob",
        url="https://example.test/p",
        score=7,
        lang="pt",
        community="r/vendas",
        raw={"nested": {"flag": True}},
    )
    signals.save([original])
    stored = signals.get(original.id)
    assert stored is not None
    assert stored.source == "reddit"
    assert stored.author == "bob"
    assert stored.url == "https://example.test/p"
    assert stored.score == 7
    assert stored.lang == "pt"
    assert stored.community == "r/vendas"
    assert stored.raw == {"nested": {"flag": True}}
    assert stored.created_at == original.created_at


def test_get_returns_none_for_an_unknown_id(signals):
    assert signals.get("nope") is None


def test_list_is_newest_first(signals, make_signal):
    older = make_signal(created_at=NOW - timedelta(days=10))
    newer = make_signal(created_at=NOW - timedelta(days=1))
    signals.save([older, newer])
    assert [signal.id for signal in signals.list()] == [newer.id, older.id]


def test_list_filters_by_source(signals, make_signal):
    signals.save([make_signal(source="hackernews"), make_signal(source="reddit")])
    assert {signal.source for signal in signals.list(source="reddit")} == {"reddit"}


def test_list_window_is_half_open(signals, make_signal):
    at_start = make_signal(created_at=NOW - timedelta(days=5))
    at_end = make_signal(created_at=NOW)
    signals.save([at_start, at_end])
    found = signals.list(since=NOW - timedelta(days=5), until=NOW)
    assert [signal.id for signal in found] == [at_start.id]


def test_list_respects_limit_and_rejects_a_bad_one(signals, make_signal):
    signals.save([make_signal() for _ in range(3)])
    assert len(signals.list(limit=2)) == 2
    with pytest.raises(ValueError, match="at least 1"):
        signals.list(limit=0)


def test_sources_reports_counts_per_source(signals, make_signal):
    signals.save(
        [make_signal(source="hackernews"), make_signal(source="hackernews"), make_signal(source="rss")]
    )
    assert signals.sources() == {"hackernews": 2, "rss": 1}


def test_unclassified_for_excludes_already_screened_signals(
    signals, classifications, make_signal, make_classification
):
    screened = make_signal()
    pending = make_signal()
    signals.save([screened, pending])
    classifications.save([make_classification(screened.id, hypothesis="h1")])

    remaining = signals.list(unclassified_for="h1")
    assert [signal.id for signal in remaining] == [pending.id]


def test_unclassified_for_is_scoped_per_hypothesis(
    signals, classifications, make_signal, make_classification
):
    """A signal screened by one hypothesis is still pending for another."""
    signal = make_signal()
    signals.save([signal])
    classifications.save([make_classification(signal.id, hypothesis="h1")])

    assert signals.list(unclassified_for="h1") == []
    assert [found.id for found in signals.list(unclassified_for="h2")] == [signal.id]


def test_large_batches_chunk_under_the_sqlite_variable_limit(signals, make_signal):
    batch = [make_signal() for _ in range(1000)]
    assert signals.save(batch) == 1000
    assert signals.save(batch) == 0
