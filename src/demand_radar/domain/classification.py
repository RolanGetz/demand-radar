"""Derived intelligence about a signal — never a replacement for it.

Everything here is Intelligence Plane output. A :class:`Classification` points
back at a :class:`~demand_radar.domain.signal.Signal` by id and is scoped to one
hypothesis, so the same signal can be classified differently by two research
questions without either overwriting the other.

Every inferred dimension is optional. ``None`` means "not known", and the
roadmap is explicit that unknowns must be preserved rather than filled with a
guess — an absent role is information, a fabricated one is noise.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from demand_radar.domain.signal import utcnow


class PainType(str, Enum):
    """Coarse families of expressed difficulty. Deliberately small and additive."""

    COMPREHENSION = "comprehension"  # cannot follow or understand
    EXPRESSION = "expression"  # cannot say what they mean, or fast enough
    CONFIDENCE = "confidence"  # freezing, anxiety, pressure
    CONTEXT_LOSS = "context_loss"  # losing track across conversations
    PROCESS = "process"  # workflow, tooling, administrative friction
    COST = "cost"  # price, budget, value
    OTHER = "other"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CommercialIntent(str, Enum):
    """How close the author is to looking for something to buy or adopt."""

    NONE = "none"
    RESEARCHING = "researching"
    COMPARING = "comparing"
    READY = "ready"


class Relevance(BaseModel):
    """The verdict of a screening stage, with the reason it reached it.

    ``reason`` exists so a conclusion can be traced back to why a signal was
    kept or dropped; ``stage`` records which filter decided, since the cheap
    and expensive screens disagree in informative ways.
    """

    relevant: bool
    confidence: float = 0.0
    reason: str = ""
    stage: str = ""

    @field_validator("confidence")
    @classmethod
    def _unit_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"confidence must be within [0, 1] (got {value})")
        return round(value, 4)


class Classification(BaseModel):
    """Structured dimensions extracted from one signal under one hypothesis."""

    signal_id: str
    hypothesis: str
    relevance: Relevance

    pain_type: PainType | None = None
    pain_summary: str | None = None  # a short paraphrase, not a replacement
    role: str | None = None
    industry: str | None = None
    b2b_context: bool | None = None
    native_language: str | None = None
    conversation_language: str | None = None
    commercial_intent: CommercialIntent | None = None
    urgency: Urgency | None = None
    solution_seeking: bool | None = None
    competitor_mentioned: str | None = None
    existing_solution_dissatisfaction: bool | None = None
    distribution_opportunity: bool | None = None

    #: Exact phrases quoted from the signal. Voice of Customer is kept verbatim:
    #: this is the vocabulary that later feeds positioning and copy.
    quotes: list[str] = Field(default_factory=list)
    classified_at: datetime = Field(default_factory=utcnow)
    model: str | None = None  # which model/stage produced this, for auditability

    @property
    def language_pair(self) -> str | None:
        """``native→conversation``, or None while either side is unknown."""
        if self.native_language and self.conversation_language:
            return f"{self.native_language}→{self.conversation_language}"
        return None
