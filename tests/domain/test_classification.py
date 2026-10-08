"""Classification: optional dimensions, preserved uncertainty, traceable verdicts."""

import pytest
from pydantic import ValidationError

from demand_radar.domain import Classification, CommercialIntent, PainType, Relevance, Urgency


def _classification(**overrides) -> Classification:
    base = {
        "signal_id": "abc123",
        "hypothesis": "non-native-negotiation",
        "relevance": Relevance(relevant=True, confidence=0.9, reason="describes freezing", stage="llm"),
    }
    return Classification(**{**base, **overrides})


def test_unknown_dimensions_stay_none_rather_than_guessed():
    classification = _classification()
    for field in (
        "pain_type",
        "role",
        "industry",
        "b2b_context",
        "native_language",
        "conversation_language",
        "commercial_intent",
        "urgency",
        "solution_seeking",
        "competitor_mentioned",
        "existing_solution_dissatisfaction",
        "distribution_opportunity",
    ):
        assert getattr(classification, field) is None, field


def test_language_pair_needs_both_sides():
    assert _classification().language_pair is None
    assert _classification(native_language="pt").language_pair is None
    assert _classification(conversation_language="en").language_pair is None
    assert (
        _classification(native_language="pt", conversation_language="en").language_pair == "pt→en"
    )


def test_dimensions_accept_their_enums():
    classification = _classification(
        pain_type=PainType.EXPRESSION,
        urgency=Urgency.HIGH,
        commercial_intent=CommercialIntent.COMPARING,
    )
    assert classification.pain_type is PainType.EXPRESSION
    assert classification.urgency is Urgency.HIGH
    assert classification.commercial_intent is CommercialIntent.COMPARING


def test_invalid_enum_value_is_rejected():
    with pytest.raises(ValidationError):
        _classification(pain_type="vibes")


def test_quotes_preserve_verbatim_voice_of_customer():
    quotes = ["I freeze when the buyer pushes back", "cannot answer quickly enough"]
    assert _classification(quotes=quotes).quotes == quotes


def test_quotes_default_empty_and_classified_at_is_stamped():
    classification = _classification()
    assert classification.quotes == []
    assert classification.classified_at.tzinfo is not None


def test_relevance_records_reason_and_stage_for_traceability():
    relevance = Relevance(relevant=False, confidence=0.2, reason="no work context", stage="cheap")
    assert not relevance.relevant
    assert relevance.reason == "no work context"
    assert relevance.stage == "cheap"


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_relevance_confidence_must_be_a_unit_fraction(confidence):
    with pytest.raises(ValidationError, match=r"\[0, 1\]"):
        Relevance(relevant=True, confidence=confidence)


def test_relevance_confidence_is_rounded():
    assert Relevance(relevant=True, confidence=0.123456789).confidence == 0.1235


def test_model_attribution_is_retained():
    assert _classification(model="gemini-2.5-pro").model == "gemini-2.5-pro"
