"""NewsEvent domain contract. News Awareness Architecture Amendment v1.0
§4: provider-independent — external classifications get normalized into
this representation by the provider adapter, never assumed to match it.

This module intentionally does NOT decide impact thresholds, blackout
durations, or which currencies matter — those are NewsFilterConfig values
(see filtering/config.py) and stay unset/DEFERRED until tested, per the
explicit instruction not to guess them."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import field_validator

from core.domain.common import UTCModel, ensure_utc


class NewsImpact(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class NewsEventStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


class NewsEvent(UTCModel):
    event_id: str
    provider: str
    title: str
    currency: str
    country: str
    scheduled_at: datetime
    impact: NewsImpact
    source: str
    status: NewsEventStatus
    created_at: datetime

    @field_validator("scheduled_at", "created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    def is_knowable_at(self, at: datetime) -> bool:
        """LOCKED (approved decision, News Awareness Amendment v1.0 §11
        follow-up): availability is governed by `created_at` — the
        timestamp at which the scheduled event became available to the
        engine/calendar — never by `scheduled_at`, which remains only
        the release timestamp and the anchor for blackout window
        calculations (see NewsWindow in news_window.py).

        This is not an arbitrary choice: a "before the event" blackout
        window is only possible at all if the event's existence is
        knowable in advance of `scheduled_at`. If availability were
        gated on `scheduled_at` instead, a "before" window would require
        look-ahead by construction.

        CRITICAL CONSTRAINT: `created_at` must come from the news
        provider's actual historical publication/calendar metadata.
        Nothing in this engine may derive, infer, or substitute it from
        `scheduled_at` (e.g. `created_at = scheduled_at - 7 days`) —
        that would silently reintroduce a fixed advance-notice
        assumption this decision explicitly avoided making. A provider
        adapter that cannot supply a genuine `created_at` cannot be used
        for backtesting until it can.
        """
        return ensure_utc(at) >= self.created_at
