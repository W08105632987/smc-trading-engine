"""Reusable data-gap detection, over raw ticks AND already-built candles.
Milestone 10 gap-policy task.

This module records FACTS about discontinuities; it does not JUDGE them.
Per the task's own instruction ("distinguish ordinary expected
market/session/weekend closures from suspicious/unexpected data gaps
where appropriate, but DO NOT invent broker-specific trading-session
rules unless those rules already exist in the frozen specifications") —
no such session-hours rule exists in any of the four frozen documents
(Discovery §1 names sessions by purpose, never by exact UTC open/close
times), so this module does NOT attempt to classify a gap as "expected
weekend closure" vs. "suspicious outage". It exposes exactly the plain,
non-invented facts a human needs to make that judgment themselves:
duration, start/end timestamps, and whether the interval includes a
Saturday (a calendar fact, not a trading-session rule).

Candle-level detection reuses the EXACT SAME condition already used by
market_data/normalization/candle_validator.py's `_flag_gap` — this is
not a new definition of "gap for candles", it is the existing one,
now also exposed as a structured, queryable record rather than only a
per-candle flag.

Tick-level detection is new (no prior tick-handling code exists in this
codebase) and requires an explicit, caller-supplied `max_allowed_interval`
— this module does not guess or default one, since "how much silence
between ticks is normal" is a data-engineering judgment call, not
something any frozen document specifies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from core.domain.market_data import Candle


@dataclass(frozen=True)
class DataGap:
    gap_start: datetime  # timestamp of the last observation before the gap
    gap_end: datetime  # timestamp of the first observation after the gap
    duration: timedelta
    previous_observation_timestamp: datetime  # == gap_start (explicit field per spec's own list)
    next_observation_timestamp: datetime  # == gap_end
    affected_timeframe: str  # e.g. "TICK", "M1", "M5", ... -- not judged, purely descriptive
    spans_weekend: bool  # calendar fact: does [gap_start, gap_end] include a Saturday?
    classification: str = "DATA_GAP"  # single, non-judgmental status -- see module docstring


def _spans_weekend(start: datetime, end: datetime) -> bool:
    # Purely a calendar fact (does the interval include a Saturday) --
    # not a claim about broker trading hours or session boundaries.
    day = start.date()
    end_day = end.date()
    while day <= end_day:
        if day.weekday() == 5:  # Saturday
            return True
        day += timedelta(days=1)
    return False


def _build_gap(previous_end: datetime, next_start: datetime, affected_timeframe: str) -> DataGap:
    return DataGap(
        gap_start=previous_end, gap_end=next_start, duration=next_start - previous_end,
        previous_observation_timestamp=previous_end, next_observation_timestamp=next_start,
        affected_timeframe=affected_timeframe, spans_weekend=_spans_weekend(previous_end, next_start),
    )


def detect_gaps_in_candles(candles: Sequence[Candle]) -> list[DataGap]:
    """Candles must already be sorted by timestamp_close (the same
    precondition market_data/normalization/candle_validator.py's
    normalize_candles() already establishes). Reuses that module's
    exact gap condition: a gap exists whenever the previous candle's
    close doesn't reach this candle's open."""
    gaps: list[DataGap] = []
    previous: Candle | None = None
    for candle in candles:
        if previous is not None and previous.timestamp_close < candle.timestamp_open:
            gaps.append(_build_gap(previous.timestamp_close, candle.timestamp_open, previous.timeframe.value))
        previous = candle
    return gaps


def detect_gaps_in_timestamps(
    timestamps: Sequence[datetime], max_allowed_interval: timedelta, label: str = "TICK",
) -> list[DataGap]:
    """`timestamps` must already be sorted ascending. A gap exists
    whenever two consecutive timestamps are further apart than
    `max_allowed_interval` — a caller-supplied threshold, never
    defaulted or invented here."""
    gaps: list[DataGap] = []
    previous: datetime | None = None
    for ts in timestamps:
        if previous is not None and (ts - previous) > max_allowed_interval:
            gaps.append(_build_gap(previous, ts, label))
        previous = ts
    return gaps
