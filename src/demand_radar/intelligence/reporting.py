"""Aggregation into the research view: pain × role × language × source × community.

Reporting reads (signal, classification) pairs — never classifications alone —
so every number stays traceable to the posts behind it. Counts are plain
dictionaries rather than scores: the roadmap is explicit that the system should
not reduce everything to abstract numbers, and a researcher needs to see "11
signals, here they are", not "pain index 0.72".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from demand_radar.domain import Classification, Signal, TimeWindow

UNKNOWN = "unknown"


@dataclass
class VoiceOfCustomerEntry:
    """One verbatim quote with the provenance needed to go back to the source."""

    quote: str
    signal_id: str
    source: str
    url: str | None
    community: str | None


@dataclass
class DemandReport:
    """The research view over one hypothesis and one window."""

    hypothesis: str
    window: TimeWindow
    total_signals: int = 0
    relevant_signals: int = 0

    by_pain: dict[str, int] = field(default_factory=dict)
    by_role: dict[str, int] = field(default_factory=dict)
    by_language_pair: dict[str, int] = field(default_factory=dict)
    by_conversation_language: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)
    by_community: dict[str, int] = field(default_factory=dict)
    by_intent: dict[str, int] = field(default_factory=dict)
    by_urgency: dict[str, int] = field(default_factory=dict)
    competitors: dict[str, int] = field(default_factory=dict)

    solution_seeking: int = 0
    dissatisfied_with_existing: int = 0
    distribution_opportunities: int = 0
    voice_of_customer: list[VoiceOfCustomerEntry] = field(default_factory=list)

    @property
    def relevance_rate(self) -> float:
        if not self.total_signals:
            return 0.0
        return round(self.relevant_signals / self.total_signals, 4)

    def headline(self) -> list[str]:
        """The few lines a researcher reads first."""
        return [
            f"{self.relevant_signals} relevant signals",
            f"{len([pain for pain in self.by_pain if pain != UNKNOWN])} pain types",
            f"{len([pair for pair in self.by_language_pair if pair != UNKNOWN])} language patterns",
            f"{len([place for place in self.by_community if place != UNKNOWN])} communities",
        ]


def build_report(
    hypothesis_name: str,
    window: TimeWindow,
    pairs: list[tuple[Signal, Classification]],
    *,
    total_signals: int | None = None,
    max_quotes: int = 50,
) -> DemandReport:
    """Aggregate classified signals into a :class:`DemandReport`.

    ``pairs`` should already be filtered to the relevant set; ``total_signals``
    is the examined population, so the relevance rate reflects the real
    denominator rather than only what passed.
    """
    report = DemandReport(
        hypothesis=hypothesis_name,
        window=window,
        relevant_signals=len(pairs),
        total_signals=total_signals if total_signals is not None else len(pairs),
    )

    counters = {name: Counter() for name in (
        "pain", "role", "language_pair", "conversation_language", "source",
        "community", "intent", "urgency", "competitors",
    )}

    for signal, classification in pairs:
        counters["pain"][_label(_enum_name(classification.pain_type))] += 1
        counters["role"][_label(classification.role)] += 1
        counters["language_pair"][_label(classification.language_pair)] += 1
        counters["conversation_language"][_label(classification.conversation_language)] += 1
        counters["source"][signal.source] += 1
        counters["community"][_label(signal.community)] += 1
        counters["intent"][_label(_enum_name(classification.commercial_intent))] += 1
        counters["urgency"][_label(_enum_name(classification.urgency))] += 1
        if classification.competitor_mentioned:
            counters["competitors"][classification.competitor_mentioned] += 1

        # Only a true flag counts. An unknown is never tallied as a yes or a no.
        report.solution_seeking += classification.solution_seeking is True
        report.dissatisfied_with_existing += (
            classification.existing_solution_dissatisfaction is True
        )
        report.distribution_opportunities += classification.distribution_opportunity is True

        for quote in classification.quotes:
            if len(report.voice_of_customer) >= max_quotes:
                break
            report.voice_of_customer.append(
                VoiceOfCustomerEntry(
                    quote=quote,
                    signal_id=signal.id,
                    source=signal.source,
                    url=signal.url,
                    community=signal.community,
                )
            )

    report.by_pain = _ranked(counters["pain"])
    report.by_role = _ranked(counters["role"])
    report.by_language_pair = _ranked(counters["language_pair"])
    report.by_conversation_language = _ranked(counters["conversation_language"])
    report.by_source = _ranked(counters["source"])
    report.by_community = _ranked(counters["community"])
    report.by_intent = _ranked(counters["intent"])
    report.by_urgency = _ranked(counters["urgency"])
    report.competitors = _ranked(counters["competitors"])
    return report


def _ranked(counter: Counter) -> dict[str, int]:
    """Most frequent first, ties broken alphabetically for stable output."""
    return {key: count for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))}


def _label(value: str | None) -> str:
    """Render an unknown dimension explicitly rather than dropping the row.

    A signal whose role could not be determined still counts toward the total,
    so "unknown" appearing in a breakdown is a finding about coverage.
    """
    return value if value else UNKNOWN


def _enum_name(value) -> str | None:
    return value.value if value is not None else None
