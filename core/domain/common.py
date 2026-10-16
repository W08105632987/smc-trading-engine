"""Shared enums and base types used across every domain contract.

Time Model (Technical Architecture v1.0): every timestamp field in this
codebase MUST be a timezone-aware UTC datetime. Naive datetimes are a bug,
not a style violation — they silently break the no-look-ahead guarantees
this engine depends on. `utc_now()` and `ensure_utc()` below are the only
sanctioned ways to produce/validate timestamps in this package.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict


def utc_now() -> datetime:
    """The single sanctioned source of 'now' for this codebase."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes; normalize aware ones to UTC.

    Raises ValueError on a naive datetime rather than silently assuming a
    timezone — a silent assumption here is exactly the kind of invented
    behavior the frozen specs forbid.
    """
    if value.tzinfo is None:
        raise ValueError(
            "naive datetime is not allowed anywhere in this engine; "
            "all timestamps must be timezone-aware UTC (Technical "
            "Architecture v1.0, Time Model)"
        )
    return value.astimezone(timezone.utc)


class UTCModel(BaseModel):
    """Base model: immutable by default, forbids unknown fields.

    Domain objects are events/facts once created — mutation is done by
    constructing a new instance (e.g. Setup.state transitions build a new
    Setup with a new state, never `setup.state = ...` in place), which
    keeps the event-sourcing/audit contract (Implementation Spec §13)
    honest — every past instance stays exactly what it was when recorded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Timeframe(str, Enum):
    D1 = "D1"
    H4 = "H4"
    H1 = "H1"
    M15 = "M15"
    M5 = "M5"
    # M1 is not one of the five strategy-analysis timeframes in Phase 2
    # §27's config contract (D1/H4/H1/M15/M5) — it is the canonical,
    # internal-only intermediate layer locked at the data-acquisition
    # gate (Decision 2: "M1 candles are constructed from BID ticks. All
    # higher timeframes are deterministically aggregated from our
    # canonical M1 series."). No strategy engine reads Timeframe.M1
    # candles directly; only market_data ingestion/aggregation code
    # produces and consumes them before handing M5/M15/H1/H4/D1 to
    # CandleStore.
    M1 = "M1"


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class Environment(str, Enum):
    """Strategy FROZEN v1.0 invariant: one canonical pipeline, three
    environments. This tag is what lets backtest/paper/live share every
    event schema without ambiguity about where an event came from."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"
