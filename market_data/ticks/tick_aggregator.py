"""Raw tick ingestion and gap-aware tick -> M1 candle construction.
Milestone 10 gap-policy task.

No tick-handling code existed anywhere in this codebase before this
module (confirmed by audit before writing this). M1 is the canonical
intermediate layer locked at the data-acquisition gate (Decision 2):
M1 candles are built from BID ticks only; ASK is retained on the Tick
record for future spread analysis but never used to construct a candle
price in this validation phase.

GAP SAFETY (Requirement 6/7): a UTC-minute bucket with zero ticks in it
produces NO candle. There is no code path in this module that can ever
synthesize a flat/carried-forward candle for a silent period — the
output candle list is built ONLY from buckets that actually contain at
least one real tick, so a multi-day data outage simply leaves a
multi-day hole in the output list, never a fabricated one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Iterator, Optional, Sequence

from core.domain.common import Timeframe, ensure_utc
from core.domain.market_data import Candle, DataSource
from market_data.quality.gap_detector import DataGap, detect_gaps_in_candles, _build_gap


@dataclass(frozen=True)
class Tick:
    symbol: str
    timestamp: datetime  # UTC
    bid: Decimal
    ask: Decimal
    # Milestone 10 blocker-1 addition: the real Tickstory/Dukascopy feed
    # carries bid/ask volume per tick. These are ADDITIVE, defaulted
    # fields — every existing call site that constructs a Tick with only
    # symbol/timestamp/bid/ask (every test in this repository, as of this
    # change) is unaffected. Volume is not consumed by any strategy
    # engine (confirmed by repository search); it is carried through
    # purely so the ingestion layer doesn't silently discard real data
    # the dataset provides, matching this module's own precedent of
    # retaining ASK "for future spread analysis" even though nothing
    # uses it yet.
    bid_volume: Optional[Decimal] = None
    ask_volume: Optional[Decimal] = None


def _build_m1_candle(symbol: str, bucket_start: datetime, bucket_ticks: list[Tick]) -> Candle:
    bids = [t.bid for t in bucket_ticks]
    bucket_end = bucket_start + timedelta(minutes=1)
    return Candle(
        candle_id=Candle.build_id(symbol, Timeframe.M1, bucket_end),
        symbol=symbol, timeframe=Timeframe.M1,
        open=bids[0], high=max(bids), low=min(bids), close=bids[-1],
        timestamp_open=bucket_start, timestamp_close=bucket_end,
        source=DataSource.AGGREGATED, confirmed=True,
    )


def stream_ticks_to_m1(ticks: Iterable[Tick], symbol: str) -> Iterator[Candle]:
    """Streaming counterpart to aggregate_ticks_to_m1() — Milestone 10
    blocker-3 (memory-safe 84M-tick processing).

    `ticks` is consumed lazily, one at a time, from any iterable
    (typically market_data/ingestion/tickstory_reader.py's streaming CSV
    reader). At most ONE UTC-minute bucket's worth of ticks is ever held
    in memory at once — as soon as a tick belonging to a NEW minute is
    seen, the previous minute is known complete (ticks must arrive in
    non-decreasing timestamp order, enforced below) and its M1 candle is
    yielded immediately; the buffer for that minute is then discarded.
    The full tick history is never resident in memory, and neither is
    the full M1 candle history — this function only ever holds the
    current bucket.

    Produces byte-identical output to aggregate_ticks_to_m1() given the
    same tick sequence (see tests/test_tick_streaming.py's equivalence
    test) — this is a memory/architecture change, not a behavior change.
    Ordering/symbol validation raises with the exact same messages as
    the batch function, applied per-tick instead of via an upfront scan.
    """
    current_bucket_start: Optional[datetime] = None
    current_bucket_ticks: list[Tick] = []
    previous_timestamp: Optional[datetime] = None

    for t in ticks:
        if t.symbol != symbol:
            raise ValueError(f"tick for symbol {t.symbol!r} does not match requested symbol {symbol!r}")
        ts = ensure_utc(t.timestamp)
        if previous_timestamp is not None and ts < previous_timestamp:
            raise ValueError(
                "ticks must already be sorted ascending by timestamp — this function never "
                "re-sorts or shuffles input; sort/validate the caller's tick stream before "
                "calling stream_ticks_to_m1()"
            )
        previous_timestamp = ts

        bucket_start = ts.replace(second=0, microsecond=0)
        if current_bucket_start is not None and bucket_start != current_bucket_start:
            yield _build_m1_candle(symbol, current_bucket_start, current_bucket_ticks)
            current_bucket_ticks = []
        current_bucket_start = bucket_start
        current_bucket_ticks.append(t)

    if current_bucket_ticks:
        yield _build_m1_candle(symbol, current_bucket_start, current_bucket_ticks)


def stream_gaps_from_candles(candles: Iterable[Candle]) -> Iterator[DataGap]:
    """Streaming counterpart to detect_gaps_in_candles() — never holds
    more than the previous candle in memory. Reuses gap_detector.py's
    EXACT gap condition and DataGap construction (_build_gap), not a
    reimplementation of the rule."""
    previous: Optional[Candle] = None
    for candle in candles:
        if previous is not None and previous.timestamp_close < candle.timestamp_open:
            yield _build_gap(previous.timestamp_close, candle.timestamp_open, previous.timeframe.value)
        previous = candle


def aggregate_ticks_to_m1(ticks: Sequence[Tick], symbol: str) -> tuple[list[Candle], list[DataGap]]:
    """`ticks` must already be sorted ascending by timestamp and all be
    for `symbol` — this function raises rather than silently re-sorting
    or filtering, consistent with this project's established
    "never silently re-order input" stance
    (backtest/validation/walk_forward.py).

    Returns (m1_candles, gaps) — `gaps` are the discontinuities between
    consecutive PRODUCED M1 candles (i.e. minutes with zero ticks),
    using the exact same gap definition as candle_validator.py's
    normalize_candles(), not a new one.
    """
    if not ticks:
        return [], []

    for t in ticks:
        if t.symbol != symbol:
            raise ValueError(f"tick for symbol {t.symbol!r} does not match requested symbol {symbol!r}")

    for previous, current in zip(ticks, ticks[1:]):
        if current.timestamp < previous.timestamp:
            raise ValueError(
                "ticks must already be sorted ascending by timestamp — this function never "
                "re-sorts or shuffles input; sort the caller's tick list before calling "
                "aggregate_ticks_to_m1()"
            )

    buckets: dict[datetime, list[Tick]] = {}
    for t in ticks:
        bucket_start = ensure_utc(t.timestamp).replace(second=0, microsecond=0)
        buckets.setdefault(bucket_start, []).append(t)

    candles: list[Candle] = []
    for bucket_start in sorted(buckets.keys()):
        bucket_ticks = buckets[bucket_start]  # already ascending — input was sorted, groupby preserves order
        bids = [t.bid for t in bucket_ticks]
        bucket_end = bucket_start + timedelta(minutes=1)
        candles.append(
            Candle(
                candle_id=Candle.build_id(symbol, Timeframe.M1, bucket_end),
                symbol=symbol, timeframe=Timeframe.M1,
                open=bids[0], high=max(bids), low=min(bids), close=bids[-1],
                timestamp_open=bucket_start, timestamp_close=bucket_end,
                source=DataSource.AGGREGATED, confirmed=True,
            )
        )

    gaps = detect_gaps_in_candles(candles)
    return candles, gaps
