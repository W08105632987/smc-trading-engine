"""Chronological development/walk-forward/holdout partitioning.
Milestone 9, Section E.

This is the 3-way split already locked during the pre-build audit
(Phase 3's dev/walk-forward/untouched-holdout split supersedes Phase
1's 70/30 split) — this module does not choose a split ratio or
introduce a new validation scheme, it only implements the boundary
mechanics of a split whose SHAPE was already decided.

Partitioning key: `entry_timestamp`, not `exit_timestamp`. A trade
belongs to whichever period the DECISION to take it was made in — using
only information available up to that point — even if the trade's
outcome (exit) isn't known until later. Partitioning by exit_timestamp
instead would let a trade decided using in-sample information leak into
an out-of-sample bucket just because it happened to close late; that
would defeat the entire purpose of a walk-forward split.

NEVER SHUFFLES, NEVER RE-SORTS: `trades` must already be in
non-decreasing `entry_timestamp` order. This module raises rather than
silently re-ordering the input, for the same reason
market_data/normalization/normalization.py raises on a genuine conflict
instead of silently picking one candle — an out-of-order input here is
a caller bug worth surfacing, not something to quietly fix by guessing
the "right" order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from backtest.trade_log import TradeRecord


@dataclass(frozen=True)
class ChronologicalPartitions:
    development: tuple
    walk_forward: tuple
    holdout: tuple


def partition_chronologically(
    trades: Sequence[TradeRecord], development_end: datetime, walk_forward_end: datetime,
) -> ChronologicalPartitions:
    if walk_forward_end <= development_end:
        raise ValueError("walk_forward_end must be strictly after development_end")

    for previous, current in zip(trades, trades[1:]):
        if current.entry_timestamp < previous.entry_timestamp:
            raise ValueError(
                "trades must already be sorted by entry_timestamp (non-decreasing) — "
                "this function never re-sorts or shuffles input; sort the caller's "
                "trade list by entry_timestamp before calling partition_chronologically()"
            )

    development: list = []
    walk_forward: list = []
    holdout: list = []
    for t in trades:
        if t.entry_timestamp < development_end:
            development.append(t)
        elif t.entry_timestamp < walk_forward_end:
            walk_forward.append(t)
        else:
            holdout.append(t)

    return ChronologicalPartitions(tuple(development), tuple(walk_forward), tuple(holdout))
