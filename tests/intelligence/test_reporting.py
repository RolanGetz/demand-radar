"""Aggregation into the research view, and traceable export."""

import csv
import io
import json
from datetime import timedelta

from demand_radar.domain import (
    Classification,
    CommercialIntent,
    PainType,
    Relevance,
    Signal,
    TimeWindow,
    Urgency,
)
from demand_radar.intelligence.export import report_to_json, signals_to_csv, signals_to_json
from demand_radar.intelligence.reporting import UNKNOWN, build_report
from tests.constants import NOW

WINDOW = TimeWindow(start=NOW - timedelta(days=30), end=NOW)


def _pair(**overrides):
    signal_fields = {"source", "community", "url", "author", "title", "text", "created_at"}
    signal_kwargs = {key: value for key, value in overrides.items() if key in signal_fields}
    classification_kwargs = {
        key: value for key, value in overrides.items() if key not in signal_fields
    }
    signal = Signal(
        **{
            "source": "reddit",
            "source_id": str(id(overrides)) + str(len(overrides)),
            "created_at": NOW - timedelta(days=2),
            "text": "I freeze when the buyer pushes back",
            "community": "r/sales",
            "url": "https://example.test/p",
            **signal_kwargs,
        }
    )
    classification = Classification(
        **{
            "signal_id": signal.id,
            "hypothesis": "h1",
            "relevance": Relevance(relevant=True, confidence=0.9, stage="structured"),
            **classification_kwargs,
        }
    )
    return signal, classification


def test_an_empty_report_is_well_formed():
    report = build_report("h1", WINDOW, [])
    assert report.relevant_signals == 0
    assert report.relevance_rate == 0.0
    assert report.by_pain == {}
    assert report.voice_of_customer == []


def test_relevance_rate_uses_the_examined_population():
    report = build_report("h1", WINDOW, [_pair(), _pair()], total_signals=10)
    assert report.relevant_signals == 2
    assert report.total_signals == 10
    assert report.relevance_rate == 0.2


def test_dimensions_are_counted():
    pairs = [
        _pair(pain_type=PainType.CONFIDENCE, role="AE", source="reddit"),
        _pair(pain_type=PainType.CONFIDENCE, role="AE", source="hackernews"),
        _pair(pain_type=PainType.CONTEXT_LOSS, role="founder", source="reddit"),
    ]
    report = build_report("h1", WINDOW, pairs)
    assert report.by_pain == {"confidence": 2, "context_loss": 1}
    assert report.by_role == {"AE": 2, "founder": 1}
    assert report.by_source == {"reddit": 2, "hackernews": 1}


def test_breakdowns_are_ranked_most_frequent_first():
    pairs = [_pair(role="rare")] + [_pair(role="common") for _ in range(3)]
    assert list(build_report("h1", WINDOW, pairs).by_role) == ["common", "rare"]


def test_language_pairs_are_counted_as_a_first_class_dimension():
    pairs = [
        _pair(native_language="pt", conversation_language="en"),
        _pair(native_language="pt", conversation_language="en"),
        _pair(native_language="es", conversation_language="de"),
    ]
    report = build_report("h1", WINDOW, pairs)
    assert report.by_language_pair == {"pt→en": 2, "es→de": 1}
    assert report.by_conversation_language == {"en": 2, "de": 1}


def test_unknown_dimensions_are_reported_as_unknown_not_dropped():
    """Missing coverage is itself a finding, so the signal still counts."""
    report = build_report("h1", WINDOW, [_pair(), _pair(role="AE")])
    assert report.by_role == {"AE": 1, UNKNOWN: 1}
    assert report.by_pain == {UNKNOWN: 2}
    assert report.by_language_pair == {UNKNOWN: 2}


def test_only_true_flags_are_tallied():
    """An unknown flag must count as neither a yes nor a no."""
    pairs = [
        _pair(solution_seeking=True, distribution_opportunity=True),
        _pair(solution_seeking=False),
        _pair(),  # unknown
    ]
    report = build_report("h1", WINDOW, pairs)
    assert report.solution_seeking == 1
    assert report.distribution_opportunities == 1
    assert report.dissatisfied_with_existing == 0


def test_competitors_are_counted_when_named():
    pairs = [
        _pair(competitor_mentioned="ToolA"),
        _pair(competitor_mentioned="ToolA"),
        _pair(competitor_mentioned="ToolB"),
        _pair(),
    ]
    assert build_report("h1", WINDOW, pairs).competitors == {"ToolA": 2, "ToolB": 1}


def test_intent_and_urgency_are_counted():
    pairs = [
        _pair(commercial_intent=CommercialIntent.READY, urgency=Urgency.HIGH),
        _pair(commercial_intent=CommercialIntent.NONE, urgency=Urgency.LOW),
    ]
    report = build_report("h1", WINDOW, pairs)
    assert report.by_intent == {"none": 1, "ready": 1}
    assert report.by_urgency == {"high": 1, "low": 1}


def test_communities_are_counted_for_distribution_research():
    pairs = [_pair(community="r/sales"), _pair(community="r/sales"), _pair(community="Hacker News")]
    assert build_report("h1", WINDOW, pairs).by_community == {"r/sales": 2, "Hacker News": 1}


def test_voice_of_customer_keeps_quotes_with_their_provenance():
    pairs = [
        _pair(
            text="I freeze when the buyer pushes back",
            url="https://example.test/post-1",
            quotes=["I freeze when the buyer pushes back"],
        )
    ]
    entry = build_report("h1", WINDOW, pairs).voice_of_customer[0]
    assert entry.quote == "I freeze when the buyer pushes back"
    assert entry.url == "https://example.test/post-1"
    assert entry.source == "reddit"
    assert entry.community == "r/sales"
    assert entry.signal_id


def test_voice_of_customer_is_capped():
    pairs = [_pair(quotes=["I freeze"]) for _ in range(10)]
    assert len(build_report("h1", WINDOW, pairs, max_quotes=3).voice_of_customer) == 3


def test_headline_counts_only_known_values():
    pairs = [_pair(pain_type=PainType.COST, community="r/sales"), _pair()]
    headline = build_report("h1", WINDOW, pairs).headline()
    assert headline[0] == "2 relevant signals"
    assert headline[1] == "1 pain types"  # the unknown is not a pain type
    assert headline[3] == "1 communities"


# -- export ----------------------------------------------------------------
def test_csv_has_one_row_per_signal_with_the_original_text():
    pairs = [_pair(text="I freeze when the buyer pushes back", role="AE")]
    rows = list(csv.DictReader(io.StringIO(signals_to_csv(pairs))))
    assert len(rows) == 1
    assert rows[0]["text"] == "I freeze when the buyer pushes back"
    assert rows[0]["role"] == "AE"
    assert rows[0]["signal_id"]
    assert rows[0]["url"] == "https://example.test/p"


def test_csv_header_is_written_even_with_no_rows():
    output = signals_to_csv([])
    assert output.splitlines()[0].startswith("signal_id,created_at,source")


def test_csv_renders_enums_as_their_values():
    pairs = [_pair(pain_type=PainType.EXPRESSION, urgency=Urgency.MEDIUM)]
    row = next(csv.DictReader(io.StringIO(signals_to_csv(pairs))))
    assert row["pain_type"] == "expression"
    assert row["urgency"] == "medium"


def test_csv_joins_quotes_into_one_cell():
    pairs = [_pair(quotes=["first quote", "second quote"])]
    row = next(csv.DictReader(io.StringIO(signals_to_csv(pairs))))
    assert row["quotes"] == "first quote | second quote"


def test_csv_leaves_unknowns_empty_rather_than_writing_false():
    row = next(csv.DictReader(io.StringIO(signals_to_csv([_pair()]))))
    assert row["b2b_context"] == ""
    assert row["role"] == ""


def test_json_export_preserves_types():
    pairs = [_pair(role="AE", b2b_context=True, pain_type=PainType.PROCESS)]
    rows = json.loads(signals_to_json(pairs))
    assert rows[0]["role"] == "AE"
    assert rows[0]["b2b_context"] is True
    assert rows[0]["pain_type"] == "process"


def test_json_export_keeps_unknowns_as_null():
    rows = json.loads(signals_to_json([_pair()]))
    assert rows[0]["b2b_context"] is None
    assert rows[0]["pain_type"] is None


def test_report_json_round_trips_the_research_view():
    pairs = [
        _pair(pain_type=PainType.CONFIDENCE, native_language="pt", conversation_language="en",
              quotes=["I freeze when the buyer pushes back"])
    ]
    payload = json.loads(report_to_json(build_report("h1", WINDOW, pairs, total_signals=4)))

    assert payload["hypothesis"] == "h1"
    assert payload["window"]["start"] == WINDOW.start.isoformat()
    assert payload["relevant_signals"] == 1
    assert payload["total_signals"] == 4
    assert payload["relevance_rate"] == 0.25
    assert payload["by_pain"] == {"confidence": 1}
    assert payload["by_language_pair"] == {"pt→en": 1}
    assert payload["voice_of_customer"][0]["quote"] == "I freeze when the buyer pushes back"


def test_report_json_is_valid_for_an_empty_report():
    payload = json.loads(report_to_json(build_report("h1", WINDOW, [])))
    assert payload["relevant_signals"] == 0
    assert payload["voice_of_customer"] == []
