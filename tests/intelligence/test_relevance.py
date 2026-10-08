"""The cheap filter: generous by design, so latent demand survives to stage 2."""


import pytest

from demand_radar.domain import Signal
from demand_radar.intelligence.relevance import MIN_CONTENT_CHARS, CheapRelevanceFilter
from tests.constants import NOW


@pytest.fixture
def cheap_filter():
    return CheapRelevanceFilter()


def _signal(text: str, **overrides) -> Signal:
    base = {"source": "hackernews", "created_at": NOW, "text": text}
    return Signal(**{**base, **overrides})


# The roadmap's own examples of demand expressed without category vocabulary.
LATENT_DEMAND_EXAMPLES = [
    "I freeze when the buyer pushes back in German and lose my whole argument.",
    "I understand the client, but I cannot answer quickly enough on the call.",
    "I realized after the meeting that procurement changed what they promised.",
    "I keep losing context between enterprise calls and it costs me deals.",
]


@pytest.mark.parametrize("text", LATENT_DEMAND_EXAMPLES)
def test_latent_demand_survives_the_cheap_filter(cheap_filter, text):
    """These name no product category, so a keyword-precise filter would lose them."""
    verdict = cheap_filter.screen(_signal(text))
    assert verdict.relevant, text


@pytest.mark.parametrize(
    "text",
    [
        "No consigo responder rápido cuando el cliente presiona en inglés durante la reunión.",
        "Ich kann nicht schnell genug antworten, wenn der Kunde auf Englisch verhandelt.",
        "Je n'arrive pas à répondre assez vite quand le client négocie en anglais.",
        "Não consigo responder rápido quando o cliente pressiona em inglês na reunião.",
    ],
)
def test_non_english_difficulty_is_recognised(cheap_filter, text):
    """Language is a first-class dimension: English must not be the only path through."""
    assert cheap_filter.screen(_signal(text)).relevant, text


def test_empty_content_is_rejected_with_full_confidence(cheap_filter):
    verdict = cheap_filter.screen(_signal(""))
    assert not verdict.relevant
    assert verdict.confidence == 1.0
    assert "empty" in verdict.reason


def test_whitespace_only_content_is_rejected(cheap_filter):
    assert not cheap_filter.screen(_signal("   \n  ")).relevant


def test_a_bare_link_carries_no_voice_of_customer(cheap_filter):
    verdict = cheap_filter.screen(_signal("https://example.com/a-very-long-url-path-indeed"))
    assert not verdict.relevant
    assert str(MIN_CONTENT_CHARS) in verdict.reason


def test_short_text_is_rejected(cheap_filter):
    assert not cheap_filter.screen(_signal("too short")).relevant


@pytest.mark.parametrize(
    "text",
    [
        "Buy now with discount code SAVE20 on our negotiation training course today!",
        "We are hiring a senior account executive, apply now for this great role.",
        "Sponsored: the best negotiation tool money can buy, click here to learn more.",
    ],
)
def test_promotional_and_recruiting_text_is_dropped(cheap_filter, text):
    verdict = cheap_filter.screen(_signal(text))
    assert not verdict.relevant
    assert "promotional" in verdict.reason


def test_substantial_prose_without_markers_is_deferred_not_dropped(cheap_filter):
    """Precision is stage 2's job; the cheap stage must not lose borderline demand."""
    text = (
        "Yesterday the procurement lead changed the terms again during our quarterly "
        "review, and the whole conversation shifted to pricing in a language that was "
        "not my first. We spent forty minutes going back over details already agreed."
    )
    verdict = cheap_filter.screen(_signal(text))
    assert verdict.relevant
    assert verdict.confidence < 0.5
    assert "deferred" in verdict.reason


def test_short_neutral_text_is_rejected(cheap_filter):
    verdict = cheap_filter.screen(_signal("The weather in Berlin was quite pleasant this week."))
    assert not verdict.relevant
    assert "no expression of need" in verdict.reason


def test_the_title_counts_toward_relevance(cheap_filter):
    signal = _signal("", title="I am struggling to negotiate contracts in my second language")
    assert cheap_filter.screen(signal).relevant


def test_every_verdict_records_its_stage_and_reason(cheap_filter):
    for text in ("", "short", LATENT_DEMAND_EXAMPLES[0]):
        verdict = cheap_filter.screen(_signal(text))
        assert verdict.stage == "cheap"
        assert verdict.reason


def test_partition_splits_candidates_from_rejects(cheap_filter):
    signals = [
        _signal(LATENT_DEMAND_EXAMPLES[0]),
        _signal(""),
        _signal(LATENT_DEMAND_EXAMPLES[1]),
        _signal("nope"),
    ]
    candidates, rejected, stats = cheap_filter.partition(signals)

    assert len(candidates) == 2
    assert len(rejected) == 2
    assert stats.examined == 4
    assert stats.kept == 2
    assert stats.dropped == 2
    assert stats.reduction == 0.5


def test_partition_carries_the_cheap_verdict_forward(cheap_filter):
    candidates, _, _ = cheap_filter.partition([_signal(LATENT_DEMAND_EXAMPLES[0])])
    _, verdict = candidates[0]
    assert verdict.stage == "cheap"
    assert verdict.relevant


def test_partition_of_nothing_reports_no_reduction(cheap_filter):
    candidates, rejected, stats = cheap_filter.partition([])
    assert candidates == [] and rejected == []
    assert stats.reduction == 0.0


# -- deferral depends on a second stage existing ---------------------------
UNMARKED_PROSE = (
    "Yesterday the procurement lead went through the quarterly figures with us "
    "and we discussed the roadmap for the coming period in some detail, covering "
    "several topics that had come up in earlier conversations with their team."
)


def test_unmarked_prose_is_deferred_when_a_model_will_judge_it():
    verdict = CheapRelevanceFilter(defer_unmatched=True).screen(_signal(UNMARKED_PROSE))
    assert verdict.relevant
    assert "deferred" in verdict.reason


def test_unmarked_prose_is_rejected_when_nothing_follows():
    """Without a model, 'relevant' would mean nothing actually judged the text."""
    verdict = CheapRelevanceFilter(defer_unmatched=False).screen(_signal(UNMARKED_PROSE))
    assert not verdict.relevant
    assert "no model available" in verdict.reason


def test_explicit_need_is_kept_regardless_of_the_deferral_mode():
    for defer in (True, False):
        verdict = CheapRelevanceFilter(defer_unmatched=defer).screen(
            _signal(LATENT_DEMAND_EXAMPLES[0])
        )
        assert verdict.relevant, defer


def test_the_filter_defers_by_default():
    assert CheapRelevanceFilter().defer_unmatched is True
