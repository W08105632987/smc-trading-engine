"""NewsWindow — the shape of a computed blackout window around a
NewsEvent. Amendment v1.0 §6/§14: the window's before/after durations
come from NewsFilterConfig (which has no defaults, by design); this
module only defines the resulting shape and the reason record, never a
duration."""

from __future__ import annotations

from datetime import datetime

from pydantic import field_validator, model_validator

from core.domain.common import UTCModel, ensure_utc
from news.domain.news_event import NewsImpact


class NewsWindow(UTCModel):
    news_event_id: str
    currency: str
    impact: NewsImpact
    window_start: datetime
    window_end: datetime

    @field_validator("window_start", "window_end")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _window_ordered(self) -> "NewsWindow":
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self

    def contains(self, at: datetime) -> bool:
        at = ensure_utc(at)
        return self.window_start <= at <= self.window_end


class NewsBlockReason(UTCModel):
    """Amendment v1.0 §14 observability requirement — everything needed
    to answer 'why didn't the robot take this otherwise-valid setup?'"""

    news_event_id: str
    decision_id: str
    news_impact: NewsImpact
    currency: str
    scheduled_at: datetime
    filter_mode: str
    block_window_start: datetime
    block_window_end: datetime
    reason_code: str = "NEWS_BLACKOUT"

    @field_validator("scheduled_at", "block_window_start", "block_window_end")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
