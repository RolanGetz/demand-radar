"""Research output: CSV and JSON, both traceable back to the source material.

Every exported row carries its signal id, url, and the author's own wording, so
an interesting number in a spreadsheet can always be followed back to the post
it came from.
"""

from __future__ import annotations

import csv
import io
import json

from demand_radar.domain import Classification, Signal
from demand_radar.intelligence.reporting import DemandReport

SIGNAL_COLUMNS = [
    "signal_id",
    "created_at",
    "source",
    "community",
    "url",
    "author",
    "title",
    "text",
    "relevant",
    "confidence",
    "stage",
    "reason",
    "pain_type",
    "pain_summary",
    "role",
    "industry",
    "b2b_context",
    "native_language",
    "conversation_language",
    "language_pair",
    "commercial_intent",
    "urgency",
    "solution_seeking",
    "competitor_mentioned",
    "existing_solution_dissatisfaction",
    "distribution_opportunity",
    "quotes",
    "model",
]


def signals_to_csv(pairs: list[tuple[Signal, Classification]]) -> str:
    """One row per classified signal, with the original text preserved."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=SIGNAL_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for signal, classification in pairs:
        writer.writerow(_row(signal, classification))
    return buffer.getvalue()


def signals_to_json(pairs: list[tuple[Signal, Classification]]) -> str:
    return json.dumps(
        [_row(signal, classification) for signal, classification in pairs],
        ensure_ascii=False,
        indent=2,
    )


def report_to_json(report: DemandReport) -> str:
    """Serialise the aggregate research view."""
    return json.dumps(
        {
            "hypothesis": report.hypothesis,
            "window": {
                "start": report.window.start.isoformat(),
                "end": report.window.end.isoformat(),
            },
            "total_signals": report.total_signals,
            "relevant_signals": report.relevant_signals,
            "relevance_rate": report.relevance_rate,
            "by_pain": report.by_pain,
            "by_role": report.by_role,
            "by_language_pair": report.by_language_pair,
            "by_conversation_language": report.by_conversation_language,
            "by_source": report.by_source,
            "by_community": report.by_community,
            "by_intent": report.by_intent,
            "by_urgency": report.by_urgency,
            "competitors": report.competitors,
            "solution_seeking": report.solution_seeking,
            "dissatisfied_with_existing": report.dissatisfied_with_existing,
            "distribution_opportunities": report.distribution_opportunities,
            "voice_of_customer": [
                {
                    "quote": entry.quote,
                    "signal_id": entry.signal_id,
                    "source": entry.source,
                    "url": entry.url,
                    "community": entry.community,
                }
                for entry in report.voice_of_customer
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _row(signal: Signal, classification: Classification) -> dict:
    relevance = classification.relevance
    return {
        "signal_id": signal.id,
        "created_at": signal.created_at.isoformat(),
        "source": signal.source,
        "community": signal.community,
        "url": signal.url,
        "author": signal.author,
        "title": signal.title,
        "text": signal.text,
        "relevant": relevance.relevant,
        "confidence": relevance.confidence,
        "stage": relevance.stage,
        "reason": relevance.reason,
        "pain_type": _value(classification.pain_type),
        "pain_summary": classification.pain_summary,
        "role": classification.role,
        "industry": classification.industry,
        "b2b_context": classification.b2b_context,
        "native_language": classification.native_language,
        "conversation_language": classification.conversation_language,
        "language_pair": classification.language_pair,
        "commercial_intent": _value(classification.commercial_intent),
        "urgency": _value(classification.urgency),
        "solution_seeking": classification.solution_seeking,
        "competitor_mentioned": classification.competitor_mentioned,
        "existing_solution_dissatisfaction": classification.existing_solution_dissatisfaction,
        "distribution_opportunity": classification.distribution_opportunity,
        "quotes": " | ".join(classification.quotes),
        "model": classification.model,
    }


def _value(enum_value) -> str | None:
    return enum_value.value if enum_value is not None else None
