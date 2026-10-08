"""The Jev stage: fast, cheap, typed decisions over every cheap-filter survivor.

This is the stage that carries the bulk of the work. One call per signal decides
relevance and every enumerable dimension in parallel, at ~70–500 ms instead of
the generative model's seconds, so the expensive stage only ever sees signals
already judged relevant.

The SDK handles retries, backoff, and Retry-After itself, so none of that is
reimplemented here.
"""

from __future__ import annotations

import logging
import time

from typesafe_sdk import RetryPolicy, TypeSafeClient, TypeSafeError

from demand_radar.domain import Classification, Hypothesis, Signal
from demand_radar.intelligence.jev_schema import (
    build_questions,
    build_state,
    parse_response,
)

logger = logging.getLogger("demand_radar.jev")

DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 20.0


class JevDecider:
    """Wraps the TypeSafe client for one hypothesis's screening run."""

    name = "jev"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        client: TypeSafeClient | None = None,
    ):
        self._api_key = api_key
        self.model = model
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        #: Input tokens consumed, for the cost line in the run summary.
        self.input_tokens = 0
        self.calls = 0

    @property
    def available(self) -> bool:
        return bool(self._api_key or self._client)

    def _get_client(self) -> TypeSafeClient:
        if self._client is None:
            self._client = TypeSafeClient(
                api_key=self._api_key,
                model=self.model,
                timeout=self._timeout,
                # Rate limits and transient overload (529) are expected on a
                # long screening run; let the SDK ride them out.
                retry=RetryPolicy(max_retries=3, respect_retry_after=True),
            )
        return self._client

    def decide(self, signal: Signal, hypothesis: Hypothesis) -> Classification:
        """Classify one signal. Raises on API or validation failure."""
        started = time.perf_counter()
        response = self._get_client().system_one(
            build_state(signal),
            build_questions(hypothesis.statement, hypothesis.relevance_criteria),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.calls += 1
        if response.usage and response.usage.input_tokens:
            self.input_tokens += response.usage.input_tokens

        classification = parse_response(
            response, signal=signal, hypothesis=hypothesis.name, model=response.model or self.model
        )
        _log_decision(signal, classification, response, elapsed_ms)
        return classification

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            close = getattr(self._client, "close", None)
            if close:
                close()
            self._client = None


def _log_decision(signal, classification, response, elapsed_ms: float) -> None:
    """One dev-readable line per decision: who decided, how sure, how fast."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    relevance = classification.relevance
    if not relevance.relevant:
        logger.debug(
            "%s relevant=%.2f → dropped  %.0fms",
            signal.id,
            response.nouls["relevant"].noul,
            elapsed_ms,
        )
        return
    parts = [f"relevant={response.nouls['relevant'].noul:.2f}"]
    pain = response.choices.get("pain_type")
    if pain:
        parts.append(f"pain={pain.choice}({pain.confidence:.2f})")
    urgency = response.scores.get("urgency")
    if urgency:
        parts.append(f"urgency={classification.urgency.value if classification.urgency else '?'}({urgency.confidence:.2f})")
    if classification.b2b_context is not None:
        parts.append(f"b2b={classification.b2b_context}")
    logger.debug("%s %s  %.0fms", signal.id, " ".join(parts), elapsed_ms)


__all__ = ["JevDecider", "TypeSafeError"]
