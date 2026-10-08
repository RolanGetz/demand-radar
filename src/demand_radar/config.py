"""Runtime configuration.

Demand Radar runs with no credentials: Hacker News and RSS need none, and the
cheap relevance filter is local. Credentials unlock keyed sources and the
structured LLM screening stage. Only the variables actually needed today are
read — the surface stays small on purpose.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

# usecwd=True: look for .env in the directory the CLI is *run* from, not where
# this file happens to be installed.
load_dotenv(find_dotenv(usecwd=True))

DEFAULT_GEMINI_MODEL = "gemini-2.5-pro"


def _clean(name: str) -> str | None:
    """A set-but-blank var is treated as unset, so it falls back to the default."""
    raw = os.getenv(name)
    if raw is None:
        return None
    return raw.strip() or None


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return list(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))


def _positive_int(name: str, default: int) -> int:
    raw = _clean(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer (got {raw!r})") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1 (got {value})")
    return value


def _nonnegative_int(name: str, default: int) -> int:
    raw = _clean(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer (got {raw!r})") from exc
    if value < 0:
        raise ValueError(f"{name} must be at least 0 (got {value})")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = _clean(name)
    try:
        value = float(raw) if raw is not None else default
    except ValueError as exc:
        raise ValueError(f"{name} must be a number (got {raw!r})") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0 (got {value})")
    return value


@dataclass
class Config:
    db_path: str = field(
        default_factory=lambda: _clean("DEMAND_RADAR_DB") or "demand-radar.db"
    )
    per_source_limit: int = field(default_factory=lambda: _positive_int("DEMAND_RADAR_LIMIT", 50))
    max_pages: int = field(default_factory=lambda: _positive_int("DEMAND_RADAR_MAX_PAGES", 3))
    retries: int = field(default_factory=lambda: _nonnegative_int("DEMAND_RADAR_RETRIES", 2))
    retry_backoff: float = field(
        default_factory=lambda: _positive_float("DEMAND_RADAR_RETRY_BACKOFF", 1.0)
    )

    # -- structured classification ------------------------------------------
    typesafe_jev_api_key: str | None = field(
        # The SDK's own default env var is accepted as a fallback.
        default_factory=lambda: _clean("TYPESAFE_JEV_API_KEY") or _clean("TYPESAFE_API_KEY")
    )
    jev_model: str = field(default_factory=lambda: _clean("JEV_MODEL") or "jev-latest")
    gemini_api_key: str | None = field(default_factory=lambda: _clean("GEMINI_API_KEY"))
    gemini_model: str = field(
        default_factory=lambda: _clean("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    )

    # -- keyed sources ------------------------------------------------------
    reddit_client_id: str | None = field(default_factory=lambda: _clean("REDDIT_CLIENT_ID"))
    reddit_client_secret: str | None = field(default_factory=lambda: _clean("REDDIT_CLIENT_SECRET"))
    reddit_access_token: str | None = field(default_factory=lambda: _clean("REDDIT_ACCESS_TOKEN"))
    rss_feeds: list[str] = field(default_factory=lambda: _env_list("DEMAND_RADAR_RSS_FEEDS"))

    @property
    def classification_available(self) -> bool:
        """Whether any model-backed screening stage can run."""
        return bool(self.typesafe_jev_api_key or self.gemini_api_key)

    def source_options(self, name: str) -> dict:
        """Per-source constructor options, so collectors never read env vars themselves."""
        if name == "reddit":
            return {
                "client_id": self.reddit_client_id,
                "client_secret": self.reddit_client_secret,
                "access_token": self.reddit_access_token,
            }
        if name == "rss":
            return {"feeds": self.rss_feeds}
        return {}
