"""Deterministic M1 -> {M5, M15, H1, H4, D1} candle aggregation.
Milestone 10 gap-policy task.

Phase 2 §4: "Aggregate lower timeframe candles into higher timeframes
deterministically." No aggregation code of any kind existed anywhere in
this codebase before this module (confirmed by audit before writing
this) — this is new, additive infrastructure, not a rewrite of
anything.

Bucketing is UTC-epoch-aligned (D1 buckets start at 00:00 UTC, H4 at
00:00/04:00/08:00.../ UTC, etc.) — a fixed, mechanical alignment rule,
not a strategy choice.

GAP SAFETY (Requirement 6/7): a target-timeframe window with ZERO
constituent M1 candles produces NO output candle — never a synthetic
one. A window with SOME but not full contiguous coverage still produces
a candle (built honestly from whatever real data exists in that
window), but is flagged with the EXISTING DataQualityFlag.GAP — reused,
not a new flag — so nothing downstream can mistake it for a candle that
represents complete, gapless coverage of its nominal period.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Iterator, Optional, Sequence

from core.domain.common import Timeframe, ensure_utc
from core.domain.market_data import Candle, DataQualityFlag, DataSource
from market_data.quality.gap_detector import detect_gaps_in_candles

_TIMEFRAME_MINUTES = {
    Timeframe.M1: 1, Timeframe.M5: 5, Timeframe.M15: 15,
    Timeframe.H1: 60, Timeframe.H4: 240, Timeframe.D1: 1440,
}


def _bucket_start(timestamp: datetime, target_minutes: int) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=timestamp.tzinfo)
    minutes_since_epoch = int((timestamp - epoch).total_seconds() // 60)
    aligned_minutes = (minutes_since_epoch // target_minutes) * target_minutes
    return epoch + timedelta(minutes=aligned_minutes)


def _build_htf_candle(
    symbol: str, target_timeframe: Timeframe, bucket_start: datetime,
    target_minutes: int, constituents: list[Candle],
) -> Candle:
    bucket_end = bucket_start + timedelta(minutes=target_minutes)
    full_coverage = (
        constituents[0].timestamp_open == bucket_start
        and constituents[-1].timestamp_close == bucket_end
        and not detect_gaps_in_candles(constituents)
    )
    flags: tuple[DataQualityFlag, ...] = () if full_coverage else (DataQualityFlag.GAP,)
    return Candle(
        candle_id=Candle.build_id(symbol, target_timeframe, bucket_end),
        symbol=symbol, timeframe=target_timeframe,
        open=constituents[0].open,
        high=max(c.high for c in constituents),
        low=min(c.low for c in constituents),
        close=constituents[-1].close,
        timestamp_open=bucket_start, timestamp_close=bucket_end,
        source=DataSource.AGGREGATED, confirmed=True,
        data_quality_flags=flags,
    )


def stream_aggregate_multi(
    source_candles: Iterable[Candle], target_timeframes: Sequence[Timeframe], symbol: str,
) -> Iterator[tuple[Timeframe, Candle]]:
    """Milestone 10 blocker-3 follow-up: fans a SINGLE source-candle
    stream out to multiple target timeframes (e.g. M1 -> {M5, H4}
    simultaneously) in ONE pass, without materializing the source series
    and without the itertools.tee() buffering trap.

    Why not itertools.tee()? tee()'s two resulting iterators only stay
    memory-bounded if consumed in lockstep — but stream_aggregate_candles
    for M5 yields roughly every 5th input candle while the H4 variant
    yields roughly every 240th, so driving two independently-tee'd
    generators (e.g. by fully consuming one before the other, or
    round-robin next() calls) would force tee's internal buffer to hold
    every M1 candle between the two consumers' positions — exactly the
    "holds everything" failure this exists to avoid. This function
    sidesteps the problem entirely by maintaining one bucket-accumulator
    PER target timeframe inside a single loop over the source, so the
    source is read exactly once and each target's buffer is bounded to
    its own bucket size (e.g. <=5 M1 candles for M5, <=240 for H4) —
    never the full history, and never each other's backlog.

    Yields (timeframe, candle) tuples in the order buckets complete,
    which — because target_timeframes' buckets close at different
    times — is chronological across the combined stream but interleaved
    between timeframes (e.g. many M5 candles, then one H4 candle, more
    M5, etc.), not grouped by timeframe. Byte-identical per-timeframe
    output to calling stream_aggregate_candles() once per timeframe on
    the same source, in the same relative order — proven in
    tests/test_streaming_aggregation.py.
    """
    for tf in target_timeframes:
        if tf not in _TIMEFRAME_MINUTES:
            raise ValueError(f"unsupported target timeframe: {tf}")

    source_timeframe: Optional[Timeframe] = None
    previous_close: Optional[datetime] = None

    # Per-target state, all initialized once the source timeframe is known.
    minutes_by_tf = {tf: _TIMEFRAME_MINUTES[tf] for tf in target_timeframes}
    bucket_start_by_tf: dict[Timeframe, Optional[datetime]] = {tf: None for tf in target_timeframes}
    buffer_by_tf: dict[Timeframe, list[Candle]] = {tf: [] for tf in target_timeframes}

    for c in source_candles:
        if source_timeframe is None:
            source_timeframe = c.timeframe
            for tf, minutes in minutes_by_tf.items():
                if _TIMEFRAME_MINUTES[source_timeframe] >= minutes:
                    raise ValueError(
                        f"source timeframe {source_timeframe.value} is not finer than target {tf.value}"
                    )
        elif c.timeframe != source_timeframe:
            raise ValueError("source_candles must all share the same timeframe")

        if previous_close is not None and c.timestamp_close < previous_close:
            raise ValueError(
                "source_candles must already be sorted ascending by timestamp_close — "
                "this function never re-sorts or shuffles input"
            )
        previous_close = c.timestamp_close

        open_ts = ensure_utc(c.timestamp_open)
        for tf, minutes in minutes_by_tf.items():
            start = _bucket_start(open_ts, minutes)
            current_start = bucket_start_by_tf[tf]
            if current_start is not None and start != current_start:
                yield tf, _build_htf_candle(symbol, tf, current_start, minutes, buffer_by_tf[tf])
                buffer_by_tf[tf] = []
            bucket_start_by_tf[tf] = start
            buffer_by_tf[tf].append(c)

    for tf, minutes in minutes_by_tf.items():
        if buffer_by_tf[tf]:
            yield tf, _build_htf_candle(symbol, tf, bucket_start_by_tf[tf], minutes, buffer_by_tf[tf])


def stream_aggregate_candles(
    source_candles: Iterable[Candle], target_timeframe: Timeframe, symbol: str,
) -> Iterator[Candle]:
    """Streaming counterpart to aggregate_candles() — Milestone 10
    blocker-3 (memory-safe processing). `source_candles` is consumed
    lazily; at most ONE target-timeframe bucket's constituent candles
    (e.g. up to 240 M1 candles for an H4 target) are ever held in memory
    at once, never the full source series. A bucket is known complete
    (and is yielded, then discarded) as soon as a candle belonging to
    the NEXT bucket is seen — the same "ascending order guarantees the
    previous bucket is closed" principle stream_ticks_to_m1() uses one
    layer down.

    Produces byte-identical output to aggregate_candles() given the same
    source sequence (see tests/test_streaming_aggregation.py). Ordering/
    timeframe-consistency validation raises with the same messages as
    the batch function, applied incrementally instead of via an upfront
    scan of the whole input.
    """
    if target_timeframe not in _TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported target timeframe: {target_timeframe}")
    target_minutes = _TIMEFRAME_MINUTES[target_timeframe]

    source_timeframe: Optional[Timeframe] = None
    source_minutes: Optional[int] = None
    current_bucket_start: Optional[datetime] = None
    buffer: list[Candle] = []
    previous_close: Optional[datetime] = None

    for c in source_candles:
        if source_timeframe is None:
            source_timeframe = c.timeframe
            source_minutes = _TIMEFRAME_MINUTES[source_timeframe]
            if source_minutes >= target_minutes:
                raise ValueError(
                    f"source timeframe {source_timeframe.value} is not finer than target {target_timeframe.value}"
                )
        elif c.timeframe != source_timeframe:
            raise ValueError("source_candles must all share the same timeframe")

        if previous_close is not None and c.timestamp_close < previous_close:
            raise ValueError(
                "source_candles must already be sorted ascending by timestamp_close — "
                "this function never re-sorts or shuffles input"
            )
        previous_close = c.timestamp_close

        start = _bucket_start(ensure_utc(c.timestamp_open), target_minutes)
        if current_bucket_start is not None and start != current_bucket_start:
            yield _build_htf_candle(symbol, target_timeframe, current_bucket_start, target_minutes, buffer)
            buffer = []
        current_bucket_start = start
        buffer.append(c)

    if buffer:
        yield _build_htf_candle(symbol, target_timeframe, current_bucket_start, target_minutes, buffer)


def aggregate_candles(source_candles: Sequence[Candle], target_timeframe: Timeframe, symbol: str) -> list[Candle]:
    """`source_candles` must be a single-timeframe, chronologically
    sorted series (M1, in the canonical pipeline, but any finer
    timeframe than `target_timeframe` works). Raises if unsorted or
    mixed-timeframe — never silently re-sorts or filters, same
    precedent as aggregate_ticks_to_m1() and walk_forward.py.
    """
    if target_timeframe not in _TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported target timeframe: {target_timeframe}")
    if not source_candles:
        return []

    source_timeframe = source_candles[0].timeframe
    for c in source_candles:
        if c.timeframe != source_timeframe:
            raise ValueError("source_candles must all share the same timeframe")
    for previous, current in zip(source_candles, source_candles[1:]):
        if current.timestamp_close < previous.timestamp_close:
            raise ValueError(
                "source_candles must already be sorted ascending by timestamp_close — "
                "this function never re-sorts or shuffles input"
            )

    target_minutes = _TIMEFRAME_MINUTES[target_timeframe]
    source_minutes = _TIMEFRAME_MINUTES[source_timeframe]
    if source_minutes >= target_minutes:
        raise ValueError(
            f"source timeframe {source_timeframe.value} is not finer than target {target_timeframe.value}"
        )

    buckets: dict[datetime, list[Candle]] = {}
    for c in source_candles:
        start = _bucket_start(c.timestamp_open, target_minutes)
        buckets.setdefault(start, []).append(c)

    result: list[Candle] = []
    for bucket_start in sorted(buckets.keys()):
        constituents = buckets[bucket_start]  # already ascending — input was sorted, groupby preserves order
        bucket_end = bucket_start + timedelta(minutes=target_minutes)

        full_coverage = (
            constituents[0].timestamp_open == bucket_start
            and constituents[-1].timestamp_close == bucket_end
            and not detect_gaps_in_candles(constituents)
        )

        flags: tuple[DataQualityFlag, ...] = () if full_coverage else (DataQualityFlag.GAP,)
        result.append(
            Candle(
                candle_id=Candle.build_id(symbol, target_timeframe, bucket_end),
                symbol=symbol, timeframe=target_timeframe,
                open=constituents[0].open,
                high=max(c.high for c in constituents),
                low=min(c.low for c in constituents),
                close=constituents[-1].close,
                timestamp_open=bucket_start, timestamp_close=bucket_end,
                source=DataSource.AGGREGATED, confirmed=True,
                data_quality_flags=flags,
            )
        )

    return result
