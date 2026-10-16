"""News policy configuration. Amendment v1.0 §6: values must be
explicitly configurable and NOT silently chosen (no 15/30/60 minute
defaults invented here). Every numeric field below is Optional with no
default — a config missing these values fails loudly if something tries
to use it, rather than quietly running on a guessed number."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator

from news.domain.news_event import NewsImpact


class NewsMode(str, Enum):
    DISABLED = "DISABLED"
    FILTER_ONLY = "FILTER_ONLY"
    BLACKOUT = "BLACKOUT"


class NewsFilterConfig(BaseModel):
    """No defaults for minimum_impact/minutes_before/minutes_after —
    these are exactly the values the amendment says must be tested, not
    guessed (§6, final architectural note). A BLACKOUT/FILTER_ONLY mode
    with any of these left unset is invalid by construction (see
    validator) rather than silently falling back to an invented number."""

    model_config = ConfigDict(extra="forbid")

    mode: NewsMode = NewsMode.DISABLED
    minimum_impact: Optional[NewsImpact] = None
    blocked_currencies: tuple[str, ...] = ()
    minutes_before: Optional[int] = None
    minutes_after: Optional[int] = None

    @model_validator(mode="after")
    def _active_modes_require_explicit_values(self) -> "NewsFilterConfig":
        if self.mode in (NewsMode.FILTER_ONLY, NewsMode.BLACKOUT):
            missing = [
                name
                for name, value in (
                    ("minimum_impact", self.minimum_impact),
                    ("minutes_before", self.minutes_before),
                    ("minutes_after", self.minutes_after),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"NewsMode.{self.mode.value} requires explicit values for "
                    f"{missing} — these must be configured, never defaulted "
                    f"(News Awareness Amendment v1.0 §6)"
                )
        return self
