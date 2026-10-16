"""Displacement / MSS engine. Phase 2 §11 — qualifying displacement
requires ALL of:
  - body size >= displacement_body_median_multiple x median body size of
    the previous 20 candles
  - total range >= displacement_range_atr x ATR(14)
  - breaks a meaningful (major) opposing swing
  - closes strongly in the direction of the break

MSS = qualifying liquidity event (sweep) + displacement + break of a
meaningful opposing swing (Strategy FROZEN v1.0 §6). confirmed_at is the
LATEST of the sweep's, displacement's, and break's own confirmation
times — never earlier (v4 §F availability table)."""

from __future__ import annotations

import bisect as _bisect
from datetime import timedelta
from decimal import Decimal
from statistics import median
from typing import Optional, Sequence

from core.domain.common import Direction
from core.domain.liquidity import DisplacementEvent, MSSEvent, SweepEvent
from core.domain.market_data import Candle
from core.domain.structure import SwingPoint, SwingType


class DisplacementEngineImpl:
    """Concrete implementation of core.contracts.detectors.DisplacementEngine."""

    def __init__(self, body_median_multiple: Decimal, range_atr_multiple: Decimal) -> None:
        self._body_median_multiple = body_median_multiple
        self._range_atr_multiple = range_atr_multiple

    def detect(
        self,
        candles: Sequence[Candle],
        swings: Sequence[SwingPoint],
        atr_by_candle_id: dict[str, Decimal],
        median_lookback: int = 20,
        _major_highs_sorted: Optional[list] = None,
        _major_highs_prices: Optional[list] = None,
        _major_lows_sorted: Optional[list] = None,
        _major_lows_prices: Optional[list] = None,
        _invalidated_ids: Optional[set] = None,
    ) -> Sequence[DisplacementEvent]:
        """PERF-05: Accepts optional pre-sorted swing lists from
        IncrementalDisplacementCache to avoid rebuilding O(all_swings)
        filter and sort on every step. When the pre-sorted lists are
        absent (direct calls / tests), builds them here once per call
        using the same logic as before — semantically identical."""

        events: list[DisplacementEvent] = []

        # Build or reuse pre-sorted HIGH/LOW major swing lists.
        # Sorting is done ONCE per detect() call, not per candle, cutting
        # the per-candle scan from O(M) to O(log M + k) via bisect.
        if _major_highs_sorted is None:
            major_swings = [s for s in swings if s.major_flag and s.invalidated_at is None]
            major_highs_sorted = sorted(
                [s for s in major_swings if s.type == SwingType.HIGH],
                key=lambda s: s.price,
            )
            major_lows_sorted = sorted(
                [s for s in major_swings if s.type == SwingType.LOW],
                key=lambda s: s.price,
            )
            major_highs_prices = [s.price for s in major_highs_sorted]
            major_lows_prices = [s.price for s in major_lows_sorted]
            invalidated_ids: set = set()  # already filtered above
        else:
            major_highs_sorted = _major_highs_sorted
            major_highs_prices = _major_highs_prices  # type: ignore[assignment]
            major_lows_sorted = _major_lows_sorted
            major_lows_prices = _major_lows_prices    # type: ignore[assignment]
            invalidated_ids = _invalidated_ids or set()

        for i, candle in enumerate(candles):
            if i < median_lookback:
                continue  # not enough history to compute the median body size

            body = abs(candle.close - candle.open)
            window = candles[i - median_lookback : i]
            median_body = median(abs(c.close - c.open) for c in window)
            if median_body == 0:
                continue

            atr = atr_by_candle_id.get(candle.candle_id)
            if atr is None:
                continue

            body_ratio = body / median_body
            candle_range = candle.high - candle.low
            range_atr_ratio = candle_range / atr

            if body_ratio < self._body_median_multiple:
                continue
            if range_atr_ratio < self._range_atr_multiple:
                continue

            direction = Direction.BULLISH if candle.close > candle.open else Direction.BEARISH
            broken_swing = self._find_broken_opposing_swing_fast(
                candle, direction,
                major_highs_sorted, major_highs_prices,
                major_lows_sorted, major_lows_prices,
                invalidated_ids,
            )
            if broken_swing is None:
                continue  # no meaningful opposing swing broken — not qualifying displacement

            events.append(
                DisplacementEvent(
                    displacement_id=f"disp:{candle.candle_id}",
                    direction=direction,
                    candle_id=candle.candle_id,
                    body_ratio=body_ratio,
                    range_atr_ratio=range_atr_ratio,
                    broken_swing_id=broken_swing.swing_id,
                    valid=True,
                    confirmed_at=candle.timestamp_close,  # self-contained, zero extra lag (v4 §F)
                )
            )

        return events

    @staticmethod
    def _find_broken_opposing_swing(
        candle: Candle, direction: Direction, major_swings: Sequence[SwingPoint]
    ) -> SwingPoint | None:
        """Legacy O(M) path kept for tests that call detect() without
        pre-sorted lists. Production paths use _find_broken_opposing_swing_fast."""
        candidates = [
            s for s in major_swings
            if s.confirmation_timestamp < candle.timestamp_close  # must already be confirmed
            and (
                (direction == Direction.BULLISH and s.type == SwingType.HIGH and candle.close > s.price)
                or (direction == Direction.BEARISH and s.type == SwingType.LOW and candle.close < s.price)
            )
        ]
        if not candidates:
            return None
        # Closest qualifying swing to the candle's close — the most
        # immediately relevant break, not an arbitrarily distant one.
        return min(
            candidates,
            key=lambda s: abs(s.price - candle.close),
        )

    @staticmethod
    def _find_broken_opposing_swing_fast(
        candle: Candle,
        direction: Direction,
        major_highs_sorted: list,   # SwingPoint list, sorted by price ASC
        major_highs_prices: list,   # parallel Decimal list for bisect
        major_lows_sorted: list,    # SwingPoint list, sorted by price ASC
        major_lows_prices: list,    # parallel Decimal list for bisect
        invalidated_ids: set,       # swing_ids to skip (invalidated_at is not None)
    ) -> SwingPoint | None:
        """PERF-05: O(log M + k) replacement for the O(M) listcomp above.

        For BULLISH displacement: needs the HIGH swing with the highest price
        still below candle.close (abs distance is minimized by maximising
        price). Uses bisect_left to find all such highs in O(log M), then
        scans right-to-left from the cutoff (highest price first) so the
        first valid entry is the answer — typically 1-3 iterations.

        For BEARISH displacement: symmetric — needs the LOW swing with the
        lowest price still above candle.close; bisect_right finds the cutoff,
        then we scan left-to-right.

        Confirmation-timestamp and invalidation checks are preserved exactly.
        """
        ts = candle.timestamp_close
        close = candle.close

        if direction == Direction.BULLISH:
            # HIGH swings with price < close (strict) — bisect_left on close
            # gives the first index with price >= close; everything left
            # of that has price < close.
            cut = _bisect.bisect_left(major_highs_prices, close)
            # Scan right-to-left: highest price first = closest to close.
            for j in range(cut - 1, -1, -1):
                s = major_highs_sorted[j]
                if s.swing_id in invalidated_ids:
                    continue
                if s.confirmation_timestamp < ts:
                    return s
            return None
        else:  # BEARISH
            # LOW swings with price > close (strict) — bisect_right on close
            # gives the first index with price > close.
            cut = _bisect.bisect_right(major_lows_prices, close)
            # Scan left-to-right: lowest price first = closest to close.
            for j in range(cut, len(major_lows_sorted)):
                s = major_lows_sorted[j]
                if s.swing_id in invalidated_ids:
                    continue
                if s.confirmation_timestamp < ts:
                    return s
            return None

    def detect_mss(
        self, sweeps: Sequence[SweepEvent], displacements: Sequence[DisplacementEvent],
        _pre_partitioned: Optional[tuple] = None,
        max_sweep_to_displacement_age: Optional[timedelta] = None,
    ) -> Sequence[MSSEvent]:
        """Pairs each valid sweep with the next qualifying displacement
        in the same direction (bearish sweep -> expects bullish reversal
        displacement, and vice versa — a sweep of highs implies a
        bearish liquidity grab, so the FOLLOWING displacement should be
        bearish; a sweep of lows implies bullish, so displacement should
        be bullish). confirmed_at = the LATER of the two.

        PERF-03: displacements arrive in chronological order of
        confirmed_at. By partitioning into bullish and bearish lists,
        bisect_left finds the earliest displacement with
        confirmed_at >= sweep.reversal_confirmed_at in O(log N) rather
        than an O(N) scan per sweep, eliminating the primary bottleneck
        in deep replay steps. Output is mathematically identical."""

        if not sweeps or not displacements:
            return []

        import bisect

        if _pre_partitioned is not None:
            bullish_disps, bullish_times, bearish_disps, bearish_times = _pre_partitioned
        else:
            # Partition by direction; sort defensively in case caller supplied unsorted input
            bullish_disps: list[DisplacementEvent] = []
            bearish_disps: list[DisplacementEvent] = []
            for d in displacements:
                if d.direction == Direction.BULLISH:
                    bullish_disps.append(d)
                elif d.direction == Direction.BEARISH:
                    bearish_disps.append(d)

            bullish_disps.sort(key=lambda d: d.confirmed_at)
            bearish_disps.sort(key=lambda d: d.confirmed_at)

            bullish_times = [d.confirmed_at for d in bullish_disps]
            bearish_times = [d.confirmed_at for d in bearish_disps]

        by_dir = {
            Direction.BULLISH: (bullish_disps, bullish_times),
            Direction.BEARISH: (bearish_disps, bearish_times),
        }

        events: list[MSSEvent] = []
        for sweep in sweeps:
            if not sweep.valid or sweep.reversal_confirmed_at is None:
                continue

            dir_disps, dir_times = by_dir.get(sweep.direction, ([], []))
            if not dir_times:
                continue

            idx = bisect.bisect_left(dir_times, sweep.reversal_confirmed_at)
            if idx >= len(dir_disps):
                continue

            displacement = dir_disps[idx]
            # PHASE A (audit 5.2): optional maximum sweep->displacement age.
            # None (default, Phase A baseline) = disabled = original behaviour.
            # Later displacements are even older, so skipping is exact.
            if (
                max_sweep_to_displacement_age is not None
                and displacement.confirmed_at - sweep.reversal_confirmed_at > max_sweep_to_displacement_age
            ):
                continue
            confirmed_at = max(sweep.reversal_confirmed_at, displacement.confirmed_at)

            events.append(
                MSSEvent(
                    mss_id=f"mss:{sweep.sweep_id}:{displacement.displacement_id}",
                    direction=sweep.direction,
                    sweep_id=sweep.sweep_id,
                    displacement_id=displacement.displacement_id,
                    broken_swing_id=displacement.broken_swing_id,
                    confirmed_at=confirmed_at,
                )
            )
        return events
