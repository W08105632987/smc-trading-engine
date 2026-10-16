"""REFERENCE (canonical) M5 layer — PHASE A, audit 3.1 / owner decision 6.

The reference engine is the canonical CORRECTNESS engine. It recomputes
swings, liquidity pools, sweeps, displacement, MSS and FVG/order-block zones
from the FULL candle history on every call, using the original, exact,
non-truncated strategy algorithms. It contains NO performance cutoffs:

  * equal-level pools are mapped over the entire history (the FAST layer
    only looks at the last `equal_level_lookback_candles`, default 100),
  * unswept pools never expire (FAST gives up after 5,000 candles),
  * pending major-swing checks never expire (FAST gives up after 3,000).

It is deliberately slow (equal-level mapping is O(n^2) per call) and is
intended for canonical/final backtests and for validating the FAST engine —
never the other way round. Same `update()` signature and return shape as
`IncrementalM5Layer`, so `ReplayPipeline(m5_engine="REFERENCE")` is a pure
drop-in swap.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from core.domain.market_data import Candle
from strategy.displacement.displacement_engine import DisplacementEngineImpl
from strategy.liquidity.liquidity_engine import LiquidityEngineImpl
from strategy.swings.swing_detector import SwingDetectorImpl
from strategy.zones.zone_engine import ZoneEngineImpl

# Same constant the FAST layer's displacement cache uses (median lookback is
# a strategy parameter of the displacement detector, not a cutoff).
DISPLACEMENT_MEDIAN_LOOKBACK = 20


@dataclass
class ReferenceM5Layer:
    swing_detector: SwingDetectorImpl
    liquidity_engine: LiquidityEngineImpl
    displacement_engine: DisplacementEngineImpl
    zone_engine: ZoneEngineImpl

    engine_name = "REFERENCE"

    def update(
        self, m5_candles: Sequence[Candle], m5_atr: dict[str, Decimal],
        equal_level_tolerance: Decimal, sweep_min_penetration_atr: Decimal,
        sweep_max_penetration_atr: Decimal, sweep_reversal_window: int,
        mss_max_sweep_age=None,
    ):
        candles = list(m5_candles)
        swings = self.swing_detector.detect(candles, atr_by_candle_id=m5_atr)

        stable_pools = list(self.liquidity_engine.map_daily_weekly_pools(candles))
        stable_pools += list(self.liquidity_engine.map_swing_pools(swings))

        equal_level_pools = []
        if candles:
            equal_level_pools = list(
                self.liquidity_engine.map_equal_level_pools(candles, equal_level_tolerance)
            )
        pools = stable_pools + equal_level_pools

        sweeps = list(self.liquidity_engine.detect_sweeps(
            candles, pools, m5_atr, sweep_min_penetration_atr, sweep_max_penetration_atr,
            sweep_reversal_window,
        ))

        displacements = self.displacement_engine.detect(
            candles, swings, m5_atr, median_lookback=DISPLACEMENT_MEDIAN_LOOKBACK,
        )
        mss_events = self.displacement_engine.detect_mss(
            sweeps, displacements, max_sweep_to_displacement_age=mss_max_sweep_age,
        )

        disp_by_candle = {d.candle_id: d for d in displacements}
        disp_by_id = {d.displacement_id: d for d in displacements}
        fvgs = self.zone_engine.detect_fvgs(candles, disp_by_candle)
        obs = self.zone_engine.detect_order_blocks(candles, mss_events, disp_by_id)
        return swings, pools, sweeps, displacements, mss_events, fvgs, obs
