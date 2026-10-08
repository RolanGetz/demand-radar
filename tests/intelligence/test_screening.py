"""The staged pipeline: cheap filter first, model only on survivors, never twice."""

import json
from datetime import timedelta

import pytest

from demand_radar.config import Config
from demand_radar.domain import Hypothesis, PainType, TimeWindow, Urgency
from demand_radar.intelligence.jev import JevDecider
from demand_radar.intelligence.providers import ScreeningProvider
from demand_radar.intelligence.screening import ScreeningService
from tests.constants import NOW

WINDOW = TimeWindow(start=NOW - timedelta(days=30), end=NOW + timedelta(days=1))

RELEVANT_TEXT = "I freeze when the buyer pushes back in German and lose my argument."
IRRELEVANT_TEXT = "nope"


class FakeProvider(ScreeningProvider):
    """A scripted provider: each call pops the next response or raises it."""

    name = "fake"
    available = True
    model = "fake-model-1"

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.prompts = []

    def complete_json(self, prompt, *, system, schema):
        self.prompts.append(prompt)
        if not self.responses:
            return json.dumps({"relevant": True, "confidence": 0.7, "reason": "default"})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response)


def _hypothesis() -> Hypothesis:
    return Hypothesis(
        name="h1",
        statement="B2B professionals who cannot negotiate in a non-native language",
        queries=["negotiating"],
        sources=["hackernews"],
        relevance_criteria="the author describes a work conversation in a second language",
    )


class OfflineJev(JevDecider):
    """A Jev decider that is explicitly unavailable, so stage 2 is skipped.

    Tests must never depend on ambient credentials: without this, a developer
    with a real TYPESAFE key in .env would silently make live API calls.
    """

    def __init__(self):
        super().__init__(api_key=None)

    @property
    def available(self) -> bool:
        return False


@pytest.fixture
def service_factory(database):
    def _build(provider=None, jev=None):
        return ScreeningService(
            Config(),
            database,
            provider=provider or FakeProvider(),
            jev=jev or OfflineJev(),
        )

    return _build


def test_only_cheap_survivors_reach_the_model(service_factory, signals, make_signal):
    """The whole point of staging: the expensive call is paid only for candidates."""
    signals.save(
        [
            make_signal(text=RELEVANT_TEXT),
            make_signal(text=IRRELEVANT_TEXT),
            make_signal(text=IRRELEVANT_TEXT),
        ]
    )
    provider = FakeProvider()
    report = service_factory(provider).screen(_hypothesis(), WINDOW)

    assert report.examined == 3
    assert report.cheap_rejected == 2
    assert report.model_calls == 1
    assert len(provider.prompts) == 1
    assert report.cost_reduction == pytest.approx(2 / 3, abs=0.001)


def test_cheap_rejections_are_still_recorded_with_their_reason(
    service_factory, signals, classifications, make_signal
):
    signal = make_signal(text=IRRELEVANT_TEXT)
    signals.save([signal])
    service_factory().screen(_hypothesis(), WINDOW)

    stored = classifications.get(signal.id, "h1")
    assert stored.relevance.relevant is False
    assert stored.relevance.stage == "cheap"
    assert stored.relevance.reason


def test_the_model_verdict_is_persisted(service_factory, signals, classifications, make_signal):
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    provider = FakeProvider(
        [
            {
                "relevant": True,
                "confidence": 0.95,
                "reason": "clear expression of difficulty",
                "pain_type": "confidence",
                "role": "sales rep",
                "native_language": "de",
                "quotes": ["I freeze when the buyer pushes back"],
            }
        ]
    )
    report = service_factory(provider).screen(_hypothesis(), WINDOW)

    stored = classifications.get(signal.id, "h1")
    assert stored.relevance.stage == "structured"
    assert stored.relevance.confidence == 0.95
    assert stored.role == "sales rep"
    assert stored.quotes == ["I freeze when the buyer pushes back"]
    assert stored.model == "fake-model-1"
    assert report.relevant == 1
    assert report.model == "fake-model-1"


def test_the_model_can_overturn_the_cheap_verdict(
    service_factory, signals, classifications, make_signal
):
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    provider = FakeProvider([{"relevant": False, "confidence": 0.9, "reason": "personal, not work"}])
    report = service_factory(provider).screen(_hypothesis(), WINDOW)

    assert classifications.get(signal.id, "h1").relevance.relevant is False
    assert report.irrelevant == 1
    assert report.relevant == 0


def test_already_screened_signals_are_not_paid_for_twice(service_factory, signals, make_signal):
    signals.save([make_signal(text=RELEVANT_TEXT)])
    provider = FakeProvider()
    service = service_factory(provider)

    service.screen(_hypothesis(), WINDOW)
    second = service.screen(_hypothesis(), WINDOW)

    assert len(provider.prompts) == 1
    assert second.examined == 0
    assert second.model_calls == 0


def test_a_new_signal_is_screened_on_the_next_run(service_factory, signals, make_signal):
    signals.save([make_signal(text=RELEVANT_TEXT)])
    provider = FakeProvider()
    service = service_factory(provider)
    service.screen(_hypothesis(), WINDOW)

    signals.save([make_signal(text=RELEVANT_TEXT + " Another distinct post.")])
    report = service.screen(_hypothesis(), WINDOW)
    assert report.examined == 1
    assert report.model_calls == 1


def test_reclassify_drops_prior_verdicts_and_screens_again(
    service_factory, signals, classifications, make_signal
):
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    provider = FakeProvider(
        [
            {"relevant": True, "confidence": 0.5, "reason": "first pass", "role": "old"},
            {"relevant": True, "confidence": 0.9, "reason": "second pass", "role": "new"},
        ]
    )
    service = service_factory(provider)
    service.screen(_hypothesis(), WINDOW)
    report = service.screen(_hypothesis(), WINDOW, reclassify=True)

    assert report.examined == 1
    assert classifications.get(signal.id, "h1").role == "new"
    assert len(classifications.list("h1")) == 1


def test_without_a_model_the_cheap_verdict_is_recorded_honestly(
    database, signals, classifications, make_signal
):
    """A run with no credentials must still produce a traceable dataset."""
    from demand_radar.intelligence.providers import NullProvider

    relevant = make_signal(text=RELEVANT_TEXT)
    rejected = make_signal(text=IRRELEVANT_TEXT)
    signals.save([relevant, rejected])

    service = ScreeningService(Config(), database, provider=NullProvider(), jev=OfflineJev())
    report = service.screen(_hypothesis(), WINDOW)

    assert report.model_calls == 0
    assert report.model is None
    assert report.relevant == 1
    assert classifications.get(relevant.id, "h1").relevance.stage == "cheap"
    assert classifications.get(rejected.id, "h1").relevance.relevant is False


def test_a_model_failure_keeps_the_cheap_verdict_and_surfaces_the_error(
    service_factory, signals, classifications, make_signal
):
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    provider = FakeProvider([RuntimeError("rate limited")])
    report = service_factory(provider).screen(_hypothesis(), WINDOW)

    stored = classifications.get(signal.id, "h1")
    assert stored.relevance.stage == "cheap"  # degraded, not discarded
    assert stored.relevance.relevant is True
    assert len(report.errors) == 1
    assert "rate limited" in report.errors[0]
    assert report.model_calls == 1  # the attempt was still paid for


def test_a_malformed_model_response_degrades_gracefully(
    service_factory, signals, classifications, make_signal
):
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    report = service_factory(FakeProvider(["this is not json"])).screen(_hypothesis(), WINDOW)

    assert classifications.get(signal.id, "h1").relevance.stage == "cheap"
    assert "ValueError" in report.errors[0]


def test_one_failure_does_not_stop_the_remaining_signals(
    service_factory, signals, make_signal
):
    signals.save(
        [
            make_signal(text=RELEVANT_TEXT),
            make_signal(text=RELEVANT_TEXT + " A second distinct post here."),
        ]
    )
    provider = FakeProvider([RuntimeError("boom"), {"relevant": True, "confidence": 0.8}])
    report = service_factory(provider).screen(_hypothesis(), WINDOW)

    assert report.model_calls == 2
    assert report.classified == 2
    assert len(report.errors) == 1


def test_only_signals_inside_the_window_are_screened(service_factory, signals, make_signal):
    """Historical correctness: a past-period run must not screen later signals."""
    inside = make_signal(text=RELEVANT_TEXT, created_at=NOW - timedelta(days=2))
    outside = make_signal(text=RELEVANT_TEXT, created_at=NOW - timedelta(days=200))
    signals.save([inside, outside])

    report = service_factory().screen(_hypothesis(), TimeWindow(start=NOW - timedelta(days=7), end=NOW))
    assert report.examined == 1


def test_two_hypotheses_screen_the_same_signal_independently(
    service_factory, signals, classifications, make_signal
):
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    provider = FakeProvider(
        [{"relevant": True, "confidence": 0.9, "role": "AE"}, {"relevant": False, "confidence": 0.8}]
    )
    service = service_factory(provider)

    service.screen(_hypothesis(), WINDOW)
    other = Hypothesis(
        name="h2", statement="something else entirely", queries=["q"], sources=["hackernews"]
    )
    service.screen(other, WINDOW)

    assert classifications.get(signal.id, "h1").role == "AE"
    assert classifications.get(signal.id, "h2").relevance.relevant is False


def test_limit_bounds_how_much_is_screened(service_factory, signals, make_signal):
    signals.save([make_signal(text=f"{RELEVANT_TEXT} post {i}") for i in range(5)])
    report = service_factory().screen(_hypothesis(), WINDOW, limit=2)
    assert report.examined == 2


def test_an_empty_dataset_reports_nothing(service_factory):
    report = service_factory().screen(_hypothesis(), WINDOW)
    assert report.examined == 0
    assert report.classified == 0
    assert report.cost_reduction == 0.0


def test_the_prompt_carries_the_hypothesis_statement(service_factory, signals, make_signal):
    signals.save([make_signal(text=RELEVANT_TEXT)])
    provider = FakeProvider()
    service_factory(provider).screen(_hypothesis(), WINDOW)
    assert "non-native language" in provider.prompts[0]


def test_service_is_a_context_manager(tmp_path, make_signal):
    config = Config()
    config.db_path = str(tmp_path / "radar.db")
    with ScreeningService(config, provider=FakeProvider(), jev=OfflineJev()) as service:
        assert service.screen(_hypothesis(), WINDOW).examined == 0


def test_without_a_model_unmarked_prose_is_not_called_relevant(
    database, signals, classifications, make_signal
):
    """A verdict of 'relevant' must never rest on text length alone."""
    from demand_radar.intelligence.providers import NullProvider

    prose = make_signal(
        text=(
            "Yesterday the procurement lead went through the quarterly figures with us "
            "and we discussed the roadmap for the coming period in some detail, covering "
            "several topics raised in earlier conversations with their team."
        )
    )
    signals.save([prose])
    service = ScreeningService(Config(), database, provider=NullProvider(), jev=OfflineJev())
    service.screen(_hypothesis(), WINDOW)

    stored = classifications.get(prose.id, "h1")
    assert stored.relevance.relevant is False
    assert "no model available" in stored.relevance.reason


def test_with_a_model_the_same_prose_is_sent_for_judgement(
    service_factory, signals, make_signal
):
    prose = make_signal(
        text=(
            "Yesterday the procurement lead went through the quarterly figures with us "
            "and we discussed the roadmap for the coming period in some detail, covering "
            "several topics raised in earlier conversations with their team."
        )
    )
    signals.save([prose])
    provider = FakeProvider()
    service_factory(provider).screen(_hypothesis(), WINDOW)
    assert len(provider.prompts) == 1


# -- durability of paid-for work -------------------------------------------
def test_verdicts_are_flushed_in_batches_not_held_to_the_end(
    database, signals, classifications, make_signal
):
    """A model call is money spent; it must be on disk before the next one starts."""
    signals.save([make_signal(text=f"{RELEVANT_TEXT} post {i}") for i in range(5)])
    service = ScreeningService(Config(), database, provider=FakeProvider(), jev=OfflineJev(), batch_size=2)

    seen_counts = []
    original_save = service.classifications.save

    def counting_save(batch):
        result = original_save(batch)
        seen_counts.append(len(batch))
        return result

    service.classifications.save = counting_save
    service.screen(_hypothesis(), WINDOW)

    # 5 candidates at batch_size=2 → flushes of 2, 2, 1 (no single write of 5).
    assert [count for count in seen_counts if count] == [2, 2, 1]
    assert len(classifications.list("h1")) == 5


def test_an_interruption_keeps_the_verdicts_already_written(
    database, signals, classifications, make_signal
):
    """Simulates the real failure: the run dies mid-way through a long screening."""
    signals.save([make_signal(text=f"{RELEVANT_TEXT} post {i}") for i in range(6)])

    class DyingProvider(FakeProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def complete_json(self, prompt, *, system, schema):
            self.calls += 1
            if self.calls > 4:
                raise KeyboardInterrupt("user stopped the run")
            return super().complete_json(prompt, system=system, schema=schema)

    service = ScreeningService(Config(), database, provider=DyingProvider(), jev=OfflineJev(), batch_size=2)
    with pytest.raises(KeyboardInterrupt):
        service.screen(_hypothesis(), WINDOW)

    # The first two flushed batches survived; nothing was lost to the crash.
    assert len(classifications.list("h1")) == 4


def test_a_resumed_run_only_screens_what_is_still_pending(
    database, signals, classifications, make_signal
):
    signals.save([make_signal(text=f"{RELEVANT_TEXT} post {i}") for i in range(4)])

    class DyingProvider(FakeProvider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def complete_json(self, prompt, *, system, schema):
            self.calls += 1
            if self.calls > 2:
                raise KeyboardInterrupt("stopped")
            return super().complete_json(prompt, system=system, schema=schema)

    with pytest.raises(KeyboardInterrupt):
        ScreeningService(
            Config(), database, provider=DyingProvider(), jev=OfflineJev(), batch_size=2
        ).screen(_hypothesis(), WINDOW)
    assert len(classifications.list("h1")) == 2

    resumed = FakeProvider()
    report = ScreeningService(
        Config(), database, provider=resumed, jev=OfflineJev()
    ).screen(_hypothesis(), WINDOW)
    assert report.examined == 2  # only the unfinished remainder
    assert len(resumed.prompts) == 2
    assert len(classifications.list("h1")) == 4


def test_batch_size_is_validated(database):
    with pytest.raises(ValueError, match="at least 1"):
        ScreeningService(Config(), database, provider=FakeProvider(), jev=OfflineJev(), batch_size=0)


# -- three-stage orchestration ---------------------------------------------
class FakeJev(JevDecider):
    """A scripted Jev decider recording what it was asked about."""

    def __init__(self, verdicts=None, error=None):
        super().__init__(api_key="apikey_test")
        self.verdicts = list(verdicts or [])
        self.error = error
        self.seen = []

    @property
    def available(self) -> bool:
        return True

    def decide(self, signal, hypothesis):
        from demand_radar.domain import Classification, Relevance

        self.seen.append(signal.id)
        if self.error:
            raise self.error
        relevant = self.verdicts.pop(0) if self.verdicts else True
        self.calls += 1
        self.input_tokens += 100
        return Classification(
            signal_id=signal.id,
            hypothesis=hypothesis.name,
            relevance=Relevance(
                relevant=relevant, confidence=0.9, reason="jev verdict", stage="jev"
            ),
            pain_type=PainType.CONFIDENCE if relevant else None,
            urgency=Urgency.HIGH if relevant else None,
            b2b_context=True if relevant else None,
            model="jev-1.13.0",
        )


def test_the_generative_model_only_sees_what_jev_kept(service_factory, signals, make_signal):
    """This is the saving the whole design exists for."""
    signals.save([make_signal(text=f"{RELEVANT_TEXT} post {i}") for i in range(4)])
    jev = FakeJev(verdicts=[True, False, False, False])
    provider = FakeProvider()

    report = service_factory(provider, jev=jev).screen(_hypothesis(), WINDOW)

    assert report.jev_calls == 4
    assert report.jev_rejected == 3
    assert report.model_calls == 1          # only the one Jev kept
    assert len(provider.prompts) == 1
    assert report.cost_reduction == 0.75


def test_jev_verdicts_are_persisted_with_their_stage(
    service_factory, signals, classifications, make_signal
):
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    service_factory(FakeProvider(), jev=FakeJev()).screen(_hypothesis(), WINDOW)

    stored = classifications.get(signal.id, "h1")
    assert stored.pain_type is PainType.CONFIDENCE
    assert stored.urgency is Urgency.HIGH
    assert stored.relevance.stage == "jev+generative"


def test_jev_rejections_are_recorded_without_the_generative_stage(
    service_factory, signals, classifications, make_signal
):
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    service_factory(FakeProvider(), jev=FakeJev(verdicts=[False])).screen(_hypothesis(), WINDOW)

    stored = classifications.get(signal.id, "h1")
    assert stored.relevance.relevant is False
    assert stored.relevance.stage == "jev"
    assert stored.quotes == []


def test_jev_decides_the_typed_fields_and_the_model_fills_the_text(
    service_factory, signals, classifications, make_signal
):
    """Jev's calibrated answers win; the model contributes only what Jev cannot."""
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    provider = FakeProvider(
        [
            {
                "relevant": True,
                "confidence": 0.4,
                "pain_type": "cost",            # contradicts Jev — must lose
                "urgency": "low",               # contradicts Jev — must lose
                "role": "account executive",    # Jev cannot produce this — must win
                "native_language": "pt",
                "quotes": ["I freeze when the buyer pushes back"],
            }
        ]
    )
    service_factory(provider, jev=FakeJev()).screen(_hypothesis(), WINDOW)

    stored = classifications.get(signal.id, "h1")
    assert stored.pain_type is PainType.CONFIDENCE   # from Jev
    assert stored.urgency is Urgency.HIGH            # from Jev
    assert stored.role == "account executive"        # from the model
    assert stored.native_language == "pt"
    assert stored.quotes == ["I freeze when the buyer pushes back"]
    assert "jev-1.13.0" in stored.model


def test_a_jev_failure_degrades_to_the_cheap_verdict_and_surfaces_the_error(
    service_factory, signals, classifications, make_signal
):
    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    report = service_factory(
        FakeProvider(), jev=FakeJev(error=RuntimeError("jev down"))
    ).screen(_hypothesis(), WINDOW)

    assert any("jev down" in error for error in report.errors)
    # The signal is not lost: the generative stage still ran.
    assert classifications.get(signal.id, "h1") is not None
    assert report.model_calls == 1


def test_jev_alone_produces_a_complete_typed_classification(
    database, signals, classifications, make_signal
):
    """With no generative model, everything except the text fields is still filled."""
    from demand_radar.intelligence.providers import NullProvider

    signal = make_signal(text=RELEVANT_TEXT)
    signals.save([signal])
    service = ScreeningService(Config(), database, provider=NullProvider(), jev=FakeJev())
    report = service.screen(_hypothesis(), WINDOW)

    stored = classifications.get(signal.id, "h1")
    assert stored.pain_type is PainType.CONFIDENCE
    assert stored.b2b_context is True
    assert stored.role is None       # nothing could produce it
    assert stored.quotes == []
    assert report.model_calls == 0
    assert report.relevant == 1


def test_the_report_tracks_jev_usage_and_cost(service_factory, signals, make_signal):
    signals.save([make_signal(text=f"{RELEVANT_TEXT} post {i}") for i in range(3)])
    report = service_factory(FakeProvider(), jev=FakeJev()).screen(_hypothesis(), WINDOW)

    assert report.jev_model == "jev-latest"
    assert report.jev_input_tokens == 300
    assert report.jev_cost_usd == pytest.approx(300 / 1_000_000 * 0.042, abs=1e-9)


def test_cheap_rejections_never_reach_jev(service_factory, signals, make_signal):
    signals.save([make_signal(text=IRRELEVANT_TEXT), make_signal(text=RELEVANT_TEXT)])
    jev = FakeJev()
    report = service_factory(FakeProvider(), jev=jev).screen(_hypothesis(), WINDOW)

    assert report.cheap_rejected == 1
    assert len(jev.seen) == 1


def test_unmarked_prose_is_deferred_when_only_jev_is_available(
    database, signals, make_signal
):
    """Jev counts as a later stage, so deferral is justified without a generative model."""
    from demand_radar.intelligence.providers import NullProvider

    prose = make_signal(
        text=(
            "Yesterday the procurement lead went through the quarterly figures with us "
            "and we discussed the roadmap for the coming period in some detail, covering "
            "several topics raised in earlier conversations with their team."
        )
    )
    signals.save([prose])
    jev = FakeJev()
    ScreeningService(Config(), database, provider=NullProvider(), jev=jev).screen(
        _hypothesis(), WINDOW
    )
    assert len(jev.seen) == 1
