"""The hypothesis: the research question, and the queries it expands into.

A hypothesis is the unit of research. It is declarative and reusable — it names
what we are looking for and over what window, but never how to fetch it. The
Data Plane reads only ``queries``/``sources``/``window``; the Intelligence Plane
reads ``statement`` and ``relevance_criteria``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# Suffix-based periods from the roadmap's historical-research modes.
_PERIOD_UNITS = {"d": "days", "w": "weeks", "m": "months", "y": "years"}


class TimeWindow(BaseModel):
    """The period under research, as an explicit closed interval.

    Historical correctness matters: a window is resolved to absolute bounds
    once, up front, so every stage of a run analyses the same interval and
    cannot leak later data into an earlier period's conclusions.
    """

    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _ensure_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.start >= self.end:
            raise ValueError("time window start must be before end")
        return self

    def contains(self, moment: datetime) -> bool:
        """Whether a timestamp falls inside the window (start inclusive, end exclusive)."""
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return self.start <= moment < self.end

    @classmethod
    def of_period(cls, period: str, *, now: datetime | None = None) -> TimeWindow:
        """Build a window from a period shorthand: ``today``, ``7d``, ``6m``, ``12m``, ``1y``.

        Months and years are approximated in days (30/365). Demand research
        asks "roughly the last half year", not "to the calendar day", and the
        approximation keeps the bound reproducible without a date library.
        """
        end = now or datetime.now(timezone.utc)
        text = period.strip().lower()
        if text == "today":
            start = end.replace(hour=0, minute=0, second=0, microsecond=0)
            return cls(start=start, end=end)
        unit = text[-1:]
        if unit not in _PERIOD_UNITS or not text[:-1].isdigit():
            expected = ", ".join(sorted(_PERIOD_UNITS))
            raise ValueError(f"period must be 'today' or <number><unit> with unit in {expected} (got {period!r})")
        count = int(text[:-1])
        if count < 1:
            raise ValueError(f"period length must be at least 1 (got {period!r})")
        days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit] * count
        return cls(start=end - timedelta(days=days), end=end)


class Hypothesis(BaseModel):
    """A reusable research question plus the retrieval surface it implies."""

    name: str
    statement: str
    #: Expanded, multilingual search terms. Language is a first-class dimension,
    #: so these are not assumed to be English and are not derived from `name`.
    queries: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    #: Natural-language test applied by the relevance stage. Kept as prose so a
    #: hypothesis stays readable and reviewable by a human researcher.
    relevance_criteria: str = ""
    #: Optional allow-list of languages; empty means "any", per the roadmap.
    languages: list[str] = Field(default_factory=list)
    #: Per-source retrieval targeting, keyed by source name — e.g.
    #: ``{"reddit": {"subreddits": ["r/sales"]}}``. Which communities to search
    #: is part of the research question, not of the credentials, so it belongs
    #: to the hypothesis. Credentials stay in configuration; these options are
    #: merged over them at collection time.
    source_options: dict[str, dict] = Field(default_factory=dict)

    @field_validator("name", "statement")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("queries", "sources", "languages")
    @classmethod
    def _clean_terms(cls, values: list[str]) -> list[str]:
        """Drop blanks and duplicates while preserving the author's ordering."""
        cleaned = [value.strip() for value in values if value and value.strip()]
        return list(dict.fromkeys(cleaned))

    @field_validator("source_options")
    @classmethod
    def _clean_source_options(cls, values: dict[str, dict]) -> dict[str, dict]:
        """Re-key entries by normalised source name.

        The values are already guaranteed to be mappings by the field's type, so
        this only normalises the keys so ``Reddit`` and ``reddit`` are one entry.
        """
        cleaned: dict[str, dict] = {}
        for name, options in (values or {}).items():
            key = str(name).strip().lower()
            if key:
                cleaned[key] = options
        return cleaned

    def options_for(self, source: str) -> dict:
        """This hypothesis's retrieval options for one source, if any."""
        return self.source_options.get(source.strip().lower(), {})

    @model_validator(mode="after")
    def _needs_retrieval_surface(self) -> Hypothesis:
        if not self.queries:
            raise ValueError("a hypothesis needs at least one query")
        if not self.sources:
            raise ValueError("a hypothesis needs at least one source")
        return self

    @classmethod
    def from_yaml(cls, text: str) -> Hypothesis:
        """Parse a hypothesis file — the unit the CLI takes as its argument."""
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("a hypothesis file must contain a YAML mapping")
        return cls.model_validate(data)
