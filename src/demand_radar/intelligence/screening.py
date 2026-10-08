"""Screening: three stages, each only paying for what the previous one kept.

::

    1. cheap local filter   free, instant      drops empty/short/promotional
    2. Jev                  ~70-500ms, ~$0     relevance + every enumerable field
    3. generative model     seconds, costly    open-ended text + verbatim quotes

Jev carries the bulk of the work: it decides relevance and all the enumerable
dimensions in one parallel call, so the generative model is only ever asked
about signals already judged relevant — and only for what Jev structurally
cannot produce (free-form roles, language codes, quotes).

Every stage is optional. With no credentials at all the cheap verdict is
recorded as-is; with Jev but no generative model the classification is complete
except for its text fields. Each verdict records which stage produced it, so a
dataset is always honest about how it was judged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from demand_radar.config import Config
from demand_radar.data_plane.storage import (
    ClassificationRepository,
    Database,
    SignalRepository,
)
from demand_radar.domain import Classification, Hypothesis, Signal, TimeWindow
from demand_radar.intelligence.jev import JevDecider
from demand_radar.intelligence.providers import ScreeningProvider, get_provider
from demand_radar.intelligence.relevance import CheapRelevanceFilter
from demand_radar.intelligence.screening_schema import (
    SCREENING_SCHEMA,
    SYSTEM_PROMPT,
    build_prompt,
    parse_screening,
)

logger = logging.getLogger(__name__)


@dataclass
class ScreeningReport:
    """What screening cost and what it found."""

    hypothesis: str
    examined: int = 0
    cheap_rejected: int = 0
    jev_calls: int = 0
    jev_rejected: int = 0
    jev_input_tokens: int = 0
    model_calls: int = 0
    relevant: int = 0
    irrelevant: int = 0
    errors: list[str] = field(default_factory=list)
    model: str | None = None
    jev_model: str | None = None

    @property
    def classified(self) -> int:
        return self.relevant + self.irrelevant

    @property
    def cost_reduction(self) -> float:
        """Fraction of examined signals that never reached the generative model."""
        if not self.examined:
            return 0.0
        return round((self.examined - self.model_calls) / self.examined, 4)

    #: Jev input price per million tokens; output is free.
    JEV_USD_PER_MTOK = 0.042

    @property
    def jev_cost_usd(self) -> float:
        """Jev input cost. Kept at full precision; rounded only for display."""
        return self.jev_input_tokens / 1_000_000 * self.JEV_USD_PER_MTOK


class ScreeningService:
    def __init__(
        self,
        config: Config | None = None,
        database: Database | None = None,
        *,
        provider: ScreeningProvider | None = None,
        jev: JevDecider | None = None,
        batch_size: int = 25,
    ):
        self.config = config or Config()
        self.db = database or Database(self.config.db_path)
        self.signals = SignalRepository(self.db)
        self.classifications = ClassificationRepository(self.db)
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self.batch_size = batch_size
        self.provider = provider if provider is not None else get_provider(self.config)
        self.jev = (
            jev
            if jev is not None
            else JevDecider(
                self.config.typesafe_jev_api_key, model=self.config.jev_model
            )
        )
        # Unmarked prose is only worth deferring if a later stage will actually
        # read it; otherwise "relevant" would rest on text length alone.
        self.cheap_filter = CheapRelevanceFilter(
            defer_unmatched=self.jev.available or self.provider.available
        )

    def screen(
        self,
        hypothesis: Hypothesis,
        window: TimeWindow,
        *,
        limit: int | None = None,
        reclassify: bool = False,
    ) -> ScreeningReport:
        """Screen stored signals for a hypothesis over one window.

        By default only signals this hypothesis has not seen are screened, so
        re-running a research project is cheap. ``reclassify=True`` drops the
        hypothesis's verdicts first and screens the window from scratch.
        """
        if reclassify:
            removed = self.classifications.delete_for(hypothesis.name)
            logger.info("dropped %d prior verdicts for %s", removed, hypothesis.name)

        pending = self.signals.list(
            since=window.start,
            until=window.end,
            unclassified_for=hypothesis.name,
            limit=limit,
        )
        report = ScreeningReport(
            hypothesis=hypothesis.name,
            examined=len(pending),
            model=self.provider.model if self.provider.available else None,
            jev_model=self.jev.model if self.jev.available else None,
        )
        logger.info(
            "screening start hypothesis=%s pending=%d jev=%s generative=%s",
            hypothesis.name,
            len(pending),
            report.jev_model or "off",
            report.model or "off",
        )
        if not pending:
            return report

        candidates, rejected, stats = self.cheap_filter.partition(pending)
        report.cheap_rejected = stats.dropped

        # Cheap rejections cost nothing and are known up front, so they land in
        # one write before any model call is made.
        self._persist(
            [
                Classification(
                    signal_id=signal.id,
                    hypothesis=hypothesis.name,
                    relevance=verdict,
                )
                for signal, verdict in rejected
            ],
            report,
        )

        # Model verdicts are flushed in batches rather than accumulated to the
        # end. A long screening run is the normal case (one call per signal), and
        # an interruption part-way through must not discard the verdicts already
        # paid for — on the next run those signals are simply no longer pending.
        batch: list[Classification] = []
        for signal, cheap_verdict in candidates:
            classification = self._classify(signal, cheap_verdict, hypothesis, report)
            batch.append(classification)
            if len(batch) >= self.batch_size:
                self._persist(batch, report)
                batch = []
        self._persist(batch, report)
        report.jev_input_tokens = self.jev.input_tokens
        logger.info(
            "screening done hypothesis=%s examined=%d jev_calls=%d jev_rejected=%d "
            "generative_calls=%d relevant=%d cost_usd=%.6f",
            hypothesis.name,
            report.examined,
            report.jev_calls,
            report.jev_rejected,
            report.model_calls,
            report.relevant,
            report.jev_cost_usd,
        )
        return report

    def _persist(self, classifications: list[Classification], report: ScreeningReport) -> None:
        """Write a batch of verdicts and fold them into the running tally."""
        if not classifications:
            return
        self.classifications.save(classifications)
        for classification in classifications:
            if classification.relevance.relevant:
                report.relevant += 1
            else:
                report.irrelevant += 1

    def _classify(
        self,
        signal: Signal,
        cheap_verdict,
        hypothesis: Hypothesis,
        report: ScreeningReport,
    ) -> Classification:
        """Run the stages for one signal, degrading rather than losing it."""
        classification = Classification(
            signal_id=signal.id,
            hypothesis=hypothesis.name,
            relevance=cheap_verdict,
        )

        # -- stage 2: Jev decides relevance and every enumerable dimension ---
        if self.jev.available:
            try:
                classification = self.jev.decide(signal, hypothesis)
                report.jev_calls += 1
            except Exception as exc:
                report.jev_calls += 1
                report.errors.append(f"{signal.id}: jev: {type(exc).__name__}: {exc}")
                logger.warning("jev failed for %s: %s", signal.id, exc)
            else:
                if not classification.relevance.relevant:
                    # Nothing downstream to learn about an irrelevant signal,
                    # and this is the saving the whole design exists for.
                    report.jev_rejected += 1
                    return classification

        # -- stage 3: the generative model fills in what Jev cannot produce ---
        if not self.provider.available:
            return classification
        try:
            raw = self.provider.complete_json(
                build_prompt(signal, hypothesis.statement, hypothesis.relevance_criteria),
                system=SYSTEM_PROMPT,
                schema=SCREENING_SCHEMA,
            )
            enriched = parse_screening(
                raw, signal=signal, hypothesis=hypothesis.name, model=self.provider.model
            )
        except Exception as exc:
            report.model_calls += 1
            report.errors.append(f"{signal.id}: {type(exc).__name__}: {exc}")
            return classification
        report.model_calls += 1
        return _merge(classification, enriched, jev_ran=self.jev.available)

    def close(self) -> None:
        self.jev.close()
        self.db.close()

    def __enter__(self) -> ScreeningService:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _merge(decided: Classification, enriched: Classification, *, jev_ran: bool) -> Classification:
    """Combine a Jev verdict with the generative model's text fields.

    Jev's typed answers win on everything it can decide — they are calibrated
    and reproducible. The generative model contributes only what Jev cannot
    produce: open-ended strings and verbatim quotes.
    """
    if not jev_ran:
        return enriched
    merged = decided.model_copy(
        update={
            "role": enriched.role,
            "industry": enriched.industry,
            "native_language": enriched.native_language,
            "conversation_language": enriched.conversation_language,
            "pain_summary": enriched.pain_summary,
            "competitor_mentioned": enriched.competitor_mentioned,
            "quotes": enriched.quotes,
            "model": f"{decided.model}+{enriched.model}",
        }
    )
    merged.relevance.stage = "jev+generative"
    return merged
