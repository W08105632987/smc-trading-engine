"""Liquidity engine. Phase 2 §7 / Strategy FROZEN v1.0 §3.

IMPLEMENTATION DECISION FLAGGED, NOT GUESSED: this module deliberately
does NOT implement session-based liquidity pools (Asian/London/New York
highs-lows) or the 'session_relevance' scoring component. The original
Master Discovery Prompt explicitly required exact session boundaries to
be defined ('Do not assume London/New York times... define sessions as
machine-readable intervals') — and that was never actually resolved with
concrete UTC boundary times anywhere across the whole discovery/audit
process. Inventing e.g. 'London = 08:00-16:00 UTC' here would be exactly
the kind of silent invention the standing rule forbids. PDH/PDL/PWH/PWL,
swing-based pools, and equal-level pools are all date-boundary or
swing-based (not session-clock-based) and are fully implemented below.
Session pools are a stub returning an empty list with this same
explanation, ready to fill in once session boundaries are explicitly
decided.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from core.domain.common import Direction, Timeframe, ensure_utc
from core.domain.liquidity import (
    LiquidityPool,
    LiquidityPoolType,
    LiquidityScoreComponents,
    SweepEvent,
)
from core.domain.market_data import Candle
from core.domain.structure import SwingPoint, SwingType


def _zero_scored_components(**overrides: Decimal) -> LiquidityScoreComponents:
    """Phase 2 §8 weights as defaults; individual engines populate what
    they can determine and leave the rest at 0 rather than guessing —
    e.g. session_relevance stays 0 everywhere until session boundaries
    are decided (see module docstring)."""
    base = dict(
        htf_significance=Decimal("0"), directional_alignment=Decimal("0"),
        session_relevance=Decimal("0"), equal_touch_strength=Decimal("0"),
        recency=Decimal("0"), distance_reachability=Decimal("0"),
        unconsumed_status=Decimal("5"),  # unconsumed by default at creation
    )
    base.update(overrides)
    return LiquidityScoreComponents(**base)


class LiquidityEngineImpl:
    """Concrete implementation of core.contracts.detectors.LiquidityEngine."""

    def map_daily_weekly_pools(self, candles: Sequence[Candle]) -> Sequence[LiquidityPool]:
        """PDH/PDL from the previous COMPLETED trading day; PWH/PWL from
        the previous COMPLETED trading week. 'Completed' matters for
        no-look-ahead: a day/week's high/low is only usable once that
        day/week has fully closed — Phase 2 §7: 'do not create future
        liquidity objects before the relevant candles close.'"""

        if not candles:
            return []

        by_day: dict[date, list[Candle]] = {}
        for c in candles:
            by_day.setdefault(c.timestamp_close.date(), []).append(c)

        sorted_days = sorted(by_day.keys())
        pools: list[LiquidityPool] = []

        # PDH/PDL: for every day boundary AFTER the first, the prior
        # day's candles (now fully in the past relative to any candle in
        # the current day) produce a pool, available from the first
        # candle close of the NEW day.
        for i in range(1, len(sorted_days)):
            prev_day_candles = by_day[sorted_days[i - 1]]
            new_day_first_candle = min(by_day[sorted_days[i]], key=lambda c: c.timestamp_close)
            pdh = max(c.high for c in prev_day_candles)
            pdl = min(c.low for c in prev_day_candles)
            created_at = new_day_first_candle.timestamp_close

            pools.append(self._build_pool(LiquidityPoolType.PDH, pdh, created_at))
            pools.append(self._build_pool(LiquidityPoolType.PDL, pdl, created_at))

        pools.extend(self._map_weekly_pools(by_day, sorted_days))
        return pools

    def _map_weekly_pools(
        self, by_day: dict[date, list[Candle]], sorted_days: list[date]
    ) -> list[LiquidityPool]:
        by_week: dict[tuple[int, int], list[date]] = {}
        for d in sorted_days:
            iso_year, iso_week, _ = d.isocalendar()
            by_week.setdefault((iso_year, iso_week), []).append(d)

        sorted_weeks = sorted(by_week.keys())
        pools: list[LiquidityPool] = []
        for i in range(1, len(sorted_weeks)):
            prev_week_days = by_week[sorted_weeks[i - 1]]
            prev_week_candles = [c for d in prev_week_days for c in by_day[d]]
            next_week_first_day = min(by_week[sorted_weeks[i]])
            new_week_first_candle = min(by_day[next_week_first_day], key=lambda c: c.timestamp_close)

            pwh = max(c.high for c in prev_week_candles)
            pwl = min(c.low for c in prev_week_candles)
            created_at = new_week_first_candle.timestamp_close

            pools.append(self._build_pool(LiquidityPoolType.PWH, pwh, created_at))
            pools.append(self._build_pool(LiquidityPoolType.PWL, pwl, created_at))
        return pools

    def map_swing_pools(self, swings: Sequence[SwingPoint]) -> Sequence[LiquidityPool]:
        """Confirmed major swing levels as liquidity pools — Phase 2 §7:
        'Swing pools = confirmed major/minor swing levels.' Only
        CONFIRMED swings are used (never before confirmation_timestamp),
        and the pool's created_at is the swing's own confirmation time,
        never its formation time."""

        pools = []
        for swing in swings:
            pool_type = (
                LiquidityPoolType.SWING_HIGH if swing.type == SwingType.HIGH
                else LiquidityPoolType.SWING_LOW
            )
            pools.append(
                self._build_pool(
                    pool_type, swing.price, swing.confirmation_timestamp,
                    is_major_swing=swing.major_flag,
                    htf_significance=Decimal("25") if swing.major_flag else Decimal("10"),
                )
            )
        return pools

    def map_equal_level_pools(
        self, candles: Sequence[Candle], tolerance: Decimal
    ) -> Sequence[LiquidityPool]:
        """Equal highs/lows: Phase 2 §7's tolerance = max(2 ticks,
        0.05 x ATR) is a caller concern (tick size and ATR aren't known
        to this method) — `tolerance` is the already-resolved absolute
        price tolerance, computed by the caller.

        PERF-02 (docs/DECISIONS.md): pre-build a candle_id-to-index
        lookup so that the group-membership recording step (previously
        `candles.index(c)` — an O(N) scan per element, making the
        entire function O(N^3)) is O(1) per element, reducing the
        whole function to the unavoidable O(N^2) lower bound for
        pairwise comparison. No change to logic or output."""

        pools: list[LiquidityPool] = []
        used: set[int] = set()
        n_candles = len(candles)
        if not n_candles:
            return pools
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        for i in range(n_candles):
            if i in used:
                continue
            a = candles[i]
            a_high = highs[i]
            a_low = lows[i]
            a_high_min = a_high - tolerance
            a_high_max = a_high + tolerance
            a_low_min = a_low - tolerance
            a_low_max = a_low + tolerance
            equal_high_group = [a]
            equal_high_indices = [i]
            equal_low_group = [a]
            equal_low_indices = [i]
            for j in range(i + 1, n_candles):
                h_j = highs[j]
                if a_high_min <= h_j <= a_high_max:
                    equal_high_group.append(candles[j])
                    equal_high_indices.append(j)
                l_j = lows[j]
                if a_low_min <= l_j <= a_low_max:
                    equal_low_group.append(candles[j])
                    equal_low_indices.append(j)

            if len(equal_high_group) > 1:
                avg_high = sum(c.high for c in equal_high_group) / len(equal_high_group)
                latest = max(equal_high_group, key=lambda c: c.timestamp_close)
                pools.append(
                    self._build_pool(
                        LiquidityPoolType.EQUAL_HIGH, avg_high, latest.timestamp_close,
                        equal_touch_strength=Decimal(min(15, len(equal_high_group) * 5)),
                    )
                )
                used.update(equal_high_indices)

            if len(equal_low_group) > 1:
                avg_low = sum(c.low for c in equal_low_group) / len(equal_low_group)
                latest = max(equal_low_group, key=lambda c: c.timestamp_close)
                pools.append(
                    self._build_pool(
                        LiquidityPoolType.EQUAL_LOW, avg_low, latest.timestamp_close,
                        equal_touch_strength=Decimal(min(15, len(equal_low_group) * 5)),
                    )
                )
                used.update(equal_low_indices)

        return pools

    def map_session_pools(self, candles: Sequence[Candle]) -> Sequence[LiquidityPool]:
        """DEFERRED — see module docstring. Returns [] rather than
        guessing session boundaries."""
        return []

    def detect_sweeps(
        self,
        candles: Sequence[Candle],
        pools: Sequence[LiquidityPool],
        atr_by_candle_id: dict[str, Decimal],
        min_penetration_atr_mult: Decimal,
        max_penetration_atr_mult: Decimal,
        reversal_window: int,
    ) -> Sequence[SweepEvent]:
        """Phase 2 §10 / Strategy FROZEN v1.0 §5, exactly:
          - penetration in [min_penetration_atr_mult, max_penetration_atr_mult] x ATR
          - the sweep candle must close back inside the prior range
          - reversal confirmation within `reversal_window` execution candles,
            else the sweep expires
        A sweep candle is the FIRST candle (per pool) whose high/low
        trades beyond the pool's level.

        PERF-07: pre-compute suffix max_high and suffix_min_low over candles
        to prune out pools whose level cannot possibly be reached by any candle
        in the search slice in O(1) without iterating over candles. Suffix array
        takes O(N_candles) once; bisect uses pre-extracted close timestamps
        without per-comparison lambda overhead. Output is mathematically identical."""

        if not candles or not pools:
            return []

        import bisect

        n_c = len(candles)
        candle_close_times = [c.timestamp_close for c in candles]
        suffix_max_high = [candles[-1].high] * n_c
        suffix_min_low = [candles[-1].low] * n_c
        for k in range(n_c - 2, -1, -1):
            h = candles[k].high
            l = candles[k].low
            suffix_max_high[k] = h if h > suffix_max_high[k + 1] else suffix_max_high[k + 1]
            suffix_min_low[k] = l if l < suffix_min_low[k + 1] else suffix_min_low[k + 1]

        high_pool_types = {
            LiquidityPoolType.PDH, LiquidityPoolType.PWH, LiquidityPoolType.SWING_HIGH,
            LiquidityPoolType.EQUAL_HIGH,
        }

        sweeps: list[SweepEvent] = []
        for pool in pools:
            start_i = bisect.bisect_right(candle_close_times, pool.created_at)
            if start_i >= n_c:
                continue

            # O(1) envelope pruning: if suffix max/min cannot cross pool price,
            # no candle in the remaining slice can penetrate the pool.
            if pool.type in high_pool_types:
                if suffix_max_high[start_i] <= pool.price:
                    continue
            else:
                if suffix_min_low[start_i] >= pool.price:
                    continue

            for i in range(start_i, n_c):
                candle = candles[i]
                atr = atr_by_candle_id.get(candle.candle_id)
                if atr is None:
                    continue

                direction, penetration = self._penetration(pool, candle)
                if penetration is None:
                    continue

                min_pen = atr * min_penetration_atr_mult
                max_pen = atr * max_penetration_atr_mult
                if not (min_pen <= penetration <= max_pen):
                    continue

                closed_back_inside = self._closed_back_inside(pool, candle)
                candle_duration = candle.timestamp_close - candle.timestamp_open
                expiry_at = candle.timestamp_close + (reversal_window * candle_duration)

                reversal_confirmed_at = None
                for future in candles[i + 1 : i + 1 + reversal_window]:
                    if self._reversal_confirmed(pool, direction, future):
                        reversal_confirmed_at = future.timestamp_close
                        break

                valid = closed_back_inside and reversal_confirmed_at is not None
                sweeps.append(
                    SweepEvent(
                        sweep_id=f"sweep:{pool.pool_id}:{candle.candle_id}",
                        liquidity_pool_id=pool.pool_id,
                        direction=direction,
                        penetration_atr=penetration / atr,
                        sweep_candle_id=candle.candle_id,
                        close_back_candle_id=candle.candle_id,
                        reversal_confirmed_at=reversal_confirmed_at,
                        expiry_at=expiry_at,
                        valid=valid,
                    )
                )
                break  # one sweep per pool — first qualifying penetration only

        return sweeps

    @staticmethod
    def _penetration(pool: LiquidityPool, candle: Candle):
        if pool.type in (
            LiquidityPoolType.PDH, LiquidityPoolType.PWH, LiquidityPoolType.SWING_HIGH,
            LiquidityPoolType.EQUAL_HIGH,
        ):
            if candle.high > pool.price:
                return Direction.BEARISH, candle.high - pool.price  # sweep of highs -> bearish reversal expected
            return None, None
        if pool.type in (
            LiquidityPoolType.PDL, LiquidityPoolType.PWL, LiquidityPoolType.SWING_LOW,
            LiquidityPoolType.EQUAL_LOW,
        ):
            if candle.low < pool.price:
                return Direction.BULLISH, pool.price - candle.low
            return None, None
        return None, None

    @staticmethod
    def _closed_back_inside(pool: LiquidityPool, candle: Candle) -> bool:
        if pool.type in (
            LiquidityPoolType.PDH, LiquidityPoolType.PWH, LiquidityPoolType.SWING_HIGH,
            LiquidityPoolType.EQUAL_HIGH,
        ):
            return candle.close <= pool.price
        return candle.close >= pool.price

    @staticmethod
    def _reversal_confirmed(pool: LiquidityPool, direction: Direction, candle: Candle) -> bool:
        if direction == Direction.BEARISH:
            return candle.close < pool.price
        return candle.close > pool.price

    @staticmethod
    def _build_pool(
        pool_type: LiquidityPoolType, price: Decimal, created_at: datetime,
        is_major_swing: Optional[bool] = None, **score_overrides
    ) -> LiquidityPool:
        components = _zero_scored_components(**score_overrides)
        total = sum(
            getattr(components, f)
            for f in (
                "htf_significance", "directional_alignment", "session_relevance",
                "equal_touch_strength", "recency", "distance_reachability", "unconsumed_status",
            )
        )
        return LiquidityPool(
            pool_id=f"{pool_type.value}:{ensure_utc(created_at).isoformat()}:{price}",
            type=pool_type, price=price, timeframe=Timeframe.D1, created_at=created_at,
            score=total, score_components=components, is_major_swing=is_major_swing,
        )
