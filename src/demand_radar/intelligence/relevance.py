"""Stage 1: the cheap relevance filter — local, deterministic, free.

Its only job is to discard the obviously irrelevant before anything expensive
runs. It is deliberately *generous*: latent demand is often phrased without any
of the vocabulary we expect ("I freeze when the buyer pushes back"), so a filter
tuned to be precise here would throw away exactly the signals the project exists
to find. Precision is the next stage's job.

The filter is therefore a cost control, not a judgement: it drops empty, trivial,
and promotional items, and lets everything with plausible first-person
difficulty through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from demand_radar.domain import Relevance, Signal

STAGE = "cheap"

MIN_CONTENT_CHARS = 40

#: First-person difficulty markers across the languages Phase 1 targets. Latent
#: demand usually surfaces as someone describing their own struggle, whatever
#: the topic vocabulary.
_DIFFICULTY_MARKERS = (
    # English
    "struggle", "struggling", "hard time", "difficult", "difficulty", "cannot", "can't",
    "unable", "freeze", "froze", "stuck", "lost", "confus", "overwhelm", "anxious",
    "nervous", "afraid", "frustrat", "problem", "issue", "pain", "worst", "fail",
    "mistake", "embarrass", "awkward", "no idea", "help", "advice", "how do i",
    "how do you", "anyone else", "tips", "recommend", "looking for", "need a",
    "alternative", "wish there", "too slow", "keep losing", "losing track",
    "changed what", "went back on", "backtrack", "realized after", "realised after",
    "after the meeting", "after the call", "had to redo", "again and again",
    # Spanish / Portuguese
    "no puedo", "no consigo", "não consigo", "nao consigo", "difícil", "dificil",
    "me cuesta", "problema", "ayuda", "ajuda", "consejo", "conselho", "no logro",
    # German
    "schwierig", "schwer", "kann nicht", "problem", "hilfe", "keine ahnung",
    # French
    "difficile", "je n'arrive pas", "je narrive pas", "problème", "probleme", "aide",
    # Polish
    "trudno", "nie mogę", "nie moge", "problem", "pomoc",
)

#: Items that are almost never someone describing their own need.
_PROMOTIONAL_MARKERS = (
    "buy now", "discount code", "limited offer", "sign up today", "click here",
    "sponsored", "affiliate", "coupon", "% off", "free trial today",
    "we are hiring", "we're hiring", "job opening", "apply now",
)

_URL_PATTERN = re.compile(r"https?://\S+")
_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class FilterStats:
    """How much the cheap stage saved — the point of having it."""

    examined: int = 0
    kept: int = 0

    @property
    def dropped(self) -> int:
        return self.examined - self.kept

    @property
    def reduction(self) -> float:
        """Fraction discarded before any model was called."""
        return round(self.dropped / self.examined, 4) if self.examined else 0.0


class CheapRelevanceFilter:
    """Deterministic pre-screen. No network, no model, no cost.

    ``defer_unmatched`` decides what happens to substantial prose that shows no
    explicit marker of need. With a model downstream it should pass through, so
    latent demand gets a real reading. When this filter is the *only* stage,
    passing it through would mean recording "relevant" for text nothing has
    actually judged — so it is rejected instead, with a reason that says why.
    """

    def __init__(self, *, defer_unmatched: bool = True):
        self.defer_unmatched = defer_unmatched

    def screen(self, signal: Signal) -> Relevance:
        content = signal.content.strip()
        if not content:
            return Relevance(relevant=False, confidence=1.0, reason="empty content", stage=STAGE)

        # A link with no commentary carries no voice of customer.
        without_urls = _URL_PATTERN.sub(" ", content).strip()
        if len(without_urls) < MIN_CONTENT_CHARS:
            return Relevance(
                relevant=False,
                confidence=0.9,
                reason=f"under {MIN_CONTENT_CHARS} characters of text",
                stage=STAGE,
            )

        lowered = without_urls.casefold()
        promotional = next((marker for marker in _PROMOTIONAL_MARKERS if marker in lowered), None)
        if promotional:
            return Relevance(
                relevant=False,
                confidence=0.8,
                reason=f"promotional or recruiting language ({promotional!r})",
                stage=STAGE,
            )

        matched = [marker for marker in _DIFFICULTY_MARKERS if marker in lowered]
        if matched:
            return Relevance(
                relevant=True,
                confidence=0.6,
                reason=f"expresses difficulty or need ({', '.join(matched[:3])})",
                stage=STAGE,
            )

        # No marker found. Substantial first-person prose is passed to the next
        # stage rather than dropped — the cheap filter must not be the thing
        # that loses latent demand phrased in unfamiliar words.
        if len(_WORD_PATTERN.findall(without_urls)) >= 25:
            if self.defer_unmatched:
                return Relevance(
                    relevant=True,
                    confidence=0.3,
                    reason="substantial prose, deferred to semantic screening",
                    stage=STAGE,
                )
            return Relevance(
                relevant=False,
                confidence=0.3,
                reason="no explicit expression of need and no model available to judge it",
                stage=STAGE,
            )

        return Relevance(
            relevant=False,
            confidence=0.5,
            reason="short text with no expression of need",
            stage=STAGE,
        )

    def partition(self, signals: list[Signal]) -> tuple[list[tuple[Signal, Relevance]], list[tuple[Signal, Relevance]], FilterStats]:
        """Split signals into (candidates, rejected, stats).

        Candidates carry their cheap verdict forward so the expensive stage can
        see — and record — what the filter thought before overriding it.
        """
        candidates: list[tuple[Signal, Relevance]] = []
        rejected: list[tuple[Signal, Relevance]] = []
        for signal in signals:
            verdict = self.screen(signal)
            (candidates if verdict.relevant else rejected).append((signal, verdict))
        return candidates, rejected, FilterStats(examined=len(signals), kept=len(candidates))
