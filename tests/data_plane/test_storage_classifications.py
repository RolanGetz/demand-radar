"""Classification persistence: per-hypothesis scoping, preserved unknowns, traceability."""

from datetime import timedelta

import pytest

from demand_radar.domain import CommercialIntent, PainType, Relevance, Urgency
from tests.constants import NOW


def test_save_and_read_back_a_full_classification(
    signals, classifications, make_signal, make_classification
):
    signal = make_signal()
    signals.save([signal])
    classifications.save(
        [
            make_classification(
                signal.id,
                pain_type=PainType.EXPRESSION,
                pain_summary="cannot respond fast enough",
                role="account executive",
                industry="saas",
                b2b_context=True,
                native_language="pt",
                conversation_language="en",
                commercial_intent=CommercialIntent.COMPARING,
                urgency=Urgency.HIGH,
                solution_seeking=True,
                competitor_mentioned="SomeTool",
                existing_solution_dissatisfaction=True,
                distribution_opportunity=False,
                quotes=["I freeze when the buyer pushes back"],
                model="gemini-2.5-pro",
            )
        ]
    )

    stored = classifications.get(signal.id, "h1")
    assert stored is not None
    assert stored.pain_type is PainType.EXPRESSION
    assert stored.commercial_intent is CommercialIntent.COMPARING
    assert stored.urgency is Urgency.HIGH
    assert stored.b2b_context is True
    assert stored.distribution_opportunity is False
    assert stored.language_pair == "pt→en"
    assert stored.quotes == ["I freeze when the buyer pushes back"]
    assert stored.model == "gemini-2.5-pro"
    assert stored.relevance.reason == "pain stated"


def test_unknown_dimensions_round_trip_as_none_not_false(
    signals, classifications, make_signal, make_classification
):
    """A NULL boolean must come back as None — unknown is not the same as 'no'."""
    signal = make_signal()
    signals.save([signal])
    classifications.save([make_classification(signal.id)])

    stored = classifications.get(signal.id, "h1")
    assert stored.b2b_context is None
    assert stored.solution_seeking is None
    assert stored.existing_solution_dissatisfaction is None
    assert stored.distribution_opportunity is None
    assert stored.pain_type is None
    assert stored.urgency is None


def test_false_is_distinguishable_from_unknown(
    signals, classifications, make_signal, make_classification
):
    signal = make_signal()
    signals.save([signal])
    classifications.save([make_classification(signal.id, b2b_context=False)])
    assert classifications.get(signal.id, "h1").b2b_context is False


def test_saving_nothing_is_a_no_op(classifications):
    assert classifications.save([]) == 0


def test_reclassifying_replaces_the_previous_verdict(
    signals, classifications, make_signal, make_classification
):
    signal = make_signal()
    signals.save([signal])
    classifications.save([make_classification(signal.id, role="unknown role")])
    classifications.save([make_classification(signal.id, role="procurement lead")])

    assert len(classifications.list("h1")) == 1
    assert classifications.get(signal.id, "h1").role == "procurement lead"


def test_two_hypotheses_classify_one_signal_independently(
    signals, classifications, make_signal, make_classification
):
    signal = make_signal()
    signals.save([signal])
    classifications.save(
        [
            make_classification(signal.id, hypothesis="h1", role="AE"),
            make_classification(
                signal.id,
                hypothesis="h2",
                relevance=Relevance(relevant=False, confidence=0.1, reason="off topic", stage="cheap"),
            ),
        ]
    )

    assert classifications.get(signal.id, "h1").role == "AE"
    assert classifications.get(signal.id, "h2").relevance.relevant is False
    assert classifications.hypotheses() == ["h1", "h2"]


def test_get_returns_none_when_not_classified(signals, classifications, make_signal):
    signal = make_signal()
    signals.save([signal])
    assert classifications.get(signal.id, "h1") is None


def test_list_can_filter_to_relevant_only(
    signals, classifications, make_signal, make_classification
):
    relevant, irrelevant = make_signal(), make_signal()
    signals.save([relevant, irrelevant])
    classifications.save(
        [
            make_classification(relevant.id),
            make_classification(
                irrelevant.id,
                relevance=Relevance(relevant=False, confidence=0.2, reason="no pain", stage="cheap"),
            ),
        ]
    )

    assert len(classifications.list("h1")) == 2
    assert [item.signal_id for item in classifications.list("h1", relevant_only=True)] == [relevant.id]


def test_list_limit_is_validated(classifications):
    with pytest.raises(ValueError, match="at least 1"):
        classifications.list("h1", limit=0)
    with pytest.raises(ValueError, match="at least 1"):
        classifications.list_with_signals("h1", limit=0)


def test_list_with_signals_joins_verdicts_to_source_material(
    signals, classifications, make_signal, make_classification
):
    """Every conclusion must be traceable back to the original wording."""
    signal = make_signal(text="I keep losing context between enterprise calls.")
    signals.save([signal])
    classifications.save([make_classification(signal.id, pain_type=PainType.CONTEXT_LOSS)])

    pairs = classifications.list_with_signals("h1")
    assert len(pairs) == 1
    stored_signal, stored_classification = pairs[0]
    assert stored_signal.text == "I keep losing context between enterprise calls."
    assert stored_signal.community == "Hacker News"
    assert stored_classification.pain_type is PainType.CONTEXT_LOSS


def test_list_with_signals_filters_by_the_research_window(
    signals, classifications, make_signal, make_classification
):
    inside = make_signal(created_at=NOW - timedelta(days=2))
    outside = make_signal(created_at=NOW - timedelta(days=90))
    signals.save([inside, outside])
    classifications.save([make_classification(inside.id), make_classification(outside.id)])

    pairs = classifications.list_with_signals("h1", since=NOW - timedelta(days=7), until=NOW)
    assert [signal.id for signal, _ in pairs] == [inside.id]


def test_list_with_signals_can_include_irrelevant_verdicts(
    signals, classifications, make_signal, make_classification
):
    signal = make_signal()
    signals.save([signal])
    classifications.save(
        [
            make_classification(
                signal.id,
                relevance=Relevance(relevant=False, confidence=0.1, reason="off topic", stage="cheap"),
            )
        ]
    )
    assert classifications.list_with_signals("h1") == []
    assert len(classifications.list_with_signals("h1", relevant_only=False)) == 1


def test_classified_ids_reports_what_has_been_screened(
    signals, classifications, make_signal, make_classification
):
    first, second = make_signal(), make_signal()
    signals.save([first, second])
    classifications.save([make_classification(first.id)])
    assert classifications.classified_ids("h1") == {first.id}


def test_deleting_a_hypothesis_leaves_collected_signals_intact(
    signals, classifications, make_signal, make_classification
):
    """Derived data is disposable; raw collected data is not."""
    signal = make_signal()
    signals.save([signal])
    classifications.save([make_classification(signal.id, hypothesis="h1")])

    assert classifications.delete_for("h1") == 1
    assert classifications.list("h1") == []
    assert signals.count() == 1
    assert signals.get(signal.id) is not None


def test_deleting_one_hypothesis_spares_another(
    signals, classifications, make_signal, make_classification
):
    signal = make_signal()
    signals.save([signal])
    classifications.save(
        [make_classification(signal.id, hypothesis="h1"), make_classification(signal.id, hypothesis="h2")]
    )
    classifications.delete_for("h1")
    assert classifications.hypotheses() == ["h2"]


def test_a_classification_requires_an_existing_signal(classifications, make_classification):
    """The foreign key keeps derived rows from outliving their source material."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        classifications.save([make_classification("ghost-id")])
