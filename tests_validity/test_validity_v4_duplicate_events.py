"""VALIDITY DEFECT V4 -- many setup candidates represent the SAME underlying market event.

STATUS: the defect assertion is EXPECTED TO FAIL on Phase-A-v2/E0; the control tests PASS today and must keep passing after a fix.

CLASSIFICATION
  PROVEN CORRECTNESS DEFECT (duplicated, non-independent setups; inflates counts, churns the 3-slot pending cap):
    * OB zones are linked per MSS (`linked_mss_id`, zone id embeds the MSS id) and an MSS id embeds the swept POOL id. Equal-level
      pools are re-mapped every step with a tolerance taken from the LATEST ATR (backtest/pipeline.py ~466-476,
      strategy/liquidity/liquidity_engine.py:134), so the same cluster of lows yields pools with slightly different average price
      => different pool ids => different MSS ids => new OB zone ids => a new setup for the same sweep + displacement.
    * MEASURED (frozen October run, id analysis of 9,153 created setups): 2,769 pool-free event groups, 1,501 of them with more than
      one variant (7,885 variants). Largest October event: 13 variants constructed from 16:20 to 20:40 for one sweep (14:55) and
      displacement (15:00). Of the 294 decided candidates, 215 remain after merging at the equal-level tolerance.
    * FVG zones link by displacement and are deduplicated by the zone-id marker already; only OB setups are duplicated.
  WHAT IS PROVEN vs POLICY
    * Proven: duplicates exist and are the same entry zone (0 of 179 in-window groups with zone data have members with different zone bounds).
    * Policy (needs approval): the MERGE CRITERION. A naive key (direction, zone, sweep candle, displacement, pool type) would also
      merge pool prices up to 0.44 ATR apart (5 of 56 in-window multi-variant groups > 0.25 ATR, max 0.444 ATR) which are probably DIFFERENT levels with different
      invalidation. The proposed safe key adds a price band equal to the EXISTING equal-level tolerance (0.05 ATR, min 2 ticks) --
      i.e. the strategy's own definition of "the same level". No new parameter.
  Separately (NOT part of V4, would be its own approved change "V5"): the root cause is pool identity depending on future volatility.
"""
import tests.test_backtest_pipeline as T
import tests.test_phase_a_correctness as P
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from core.domain.common import Direction, Timeframe
from core.domain.liquidity import LiquidityPool, LiquidityPoolType as LT, MSSEvent, SweepEvent
from core.domain.market_data import Candle, DataSource
from core.domain.zones import Zone, ZoneType
from strategy.liquidity.liquidity_engine import LiquidityEngineImpl
from tests.test_target_selector import _zero_components

T0 = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
ATR = D("2.0")                    # tolerance at 0.05 ATR = 0.10 points
BULL, BEAR = Direction.BULLISH, Direction.BEARISH


def _variant(i, price, *, pool_type=LT.EQUAL_LOW, sweep_candle="c-sweep", disp="disp-1", direction=BULL, zone_type=ZoneType.ORDER_BLOCK,
             zone_id=None, upper="3830.0", lower="3825.0"):
    """One pool variant -> sweep -> MSS -> zone, exactly as the detectors would link them."""
    pool = LiquidityPool(pool_id=f"pool-{i}", type=pool_type, price=D(str(price)), timeframe=Timeframe.M5, created_at=T0 - timedelta(hours=2),
                         score=D("5"), score_components=_zero_components())
    sweep = SweepEvent(sweep_id=f"sweep-{i}", liquidity_pool_id=pool.pool_id, direction=direction, penetration_atr=D("0.2"),
                       sweep_candle_id=sweep_candle, close_back_candle_id="c-cb", reversal_confirmed_at=T0, expiry_at=T0 + timedelta(hours=1), valid=True)
    mss = MSSEvent(mss_id=f"mss-{i}", direction=direction, sweep_id=sweep.sweep_id, displacement_id=disp, broken_swing_id="bs", confirmed_at=T0 + timedelta(minutes=5))
    zid = zone_id or (f"ob:mss-{i}" if zone_type == ZoneType.ORDER_BLOCK else f"fvg:{disp}")
    zone = Zone(zone_id=zid, zone_type=zone_type, direction=direction, upper=D(upper), lower=D(lower), origin_candle_id="c-ob",
                creation_timestamp=T0 + timedelta(minutes=5), linked_mss_id=mss.mss_id if zone_type == ZoneType.ORDER_BLOCK else None,
                linked_displacement_id=disp)
    return pool, sweep, mss, zone


def _construct(variants):
    p = P._setup_pipeline(min_rr="1")
    p.state.active_setups.clear()
    pools, sweeps, msss, zones = zip(*variants)
    created_at = T0 + timedelta(minutes=5)
    atr_key = Candle.build_id("XAUUSD", Timeframe.M5, created_at)
    events = p._construct_setup_candidates(created_at + timedelta(hours=3), list(msss), {z.zone_id: z for z in zones}, list(sweeps), list(pools), {atr_key: ATR})
    return p, events


def _constructed(events):
    return [e for e in events if e.event_type.value == "SETUP_CREATED"]


class TestOneEconomicEventYieldsOneSetup:
    def test_pool_variants_within_the_equal_level_tolerance_yield_a_single_setup(self):
        # 3 variants of the SAME swept level: prices within 0.09 points (< 0.05 ATR = 0.10)
        _, events = _construct([_variant(i, p) for i, p in enumerate(["3827.86", "3827.90", "3827.95"])])
        assert len(_constructed(events)) == 1, f"{len(_constructed(events))} setups for ONE sweep + displacement + zone"


class TestDistinctOpportunitiesAreNeverMerged:
    """These PASS today (no dedup exists) and are the safeguard that V4's key must not break: every case below is a genuinely
    different opportunity (different entry zone, trigger, direction or invalidation level)."""

    def _n(self, variants):
        return len(_constructed(_construct(variants)[1]))

    def test_pools_further_apart_than_the_equal_level_tolerance_stay_distinct(self):
        assert self._n([_variant(0, "3827.86"), _variant(1, "3828.30")]) == 2          # 0.44 points = 0.22 ATR > 0.05 ATR

    def test_different_sweep_candle_stays_distinct(self):
        assert self._n([_variant(0, "3827.86", sweep_candle="c-sweep-A"), _variant(1, "3827.86", sweep_candle="c-sweep-B")]) == 2

    def test_different_displacement_stays_distinct(self):
        assert self._n([_variant(0, "3827.86", disp="disp-1"), _variant(1, "3827.86", disp="disp-2")]) == 2

    def test_different_pool_type_at_the_same_price_stays_distinct(self):
        assert self._n([_variant(0, "3827.86", pool_type=LT.EQUAL_LOW), _variant(1, "3827.86", pool_type=LT.SWING_LOW)]) == 2

    def test_different_zone_geometry_stays_distinct(self):
        assert self._n([_variant(0, "3827.86", upper="3830.0", lower="3825.0"), _variant(1, "3827.86", upper="3833.0", lower="3829.0")]) == 2

    def test_opposite_directions_stay_distinct(self):
        bear = _variant(1, "3827.86", pool_type=LT.EQUAL_HIGH, direction=BEAR)
        assert self._n([_variant(0, "3827.86"), bear]) == 2

    def test_fvg_and_order_block_for_the_same_displacement_stay_distinct(self):
        assert self._n([_variant(0, "3827.86"), _variant(1, "3827.86", zone_type=ZoneType.FVG)]) == 2

    def test_fvg_shared_by_pool_variants_is_already_created_once(self):
        """FVG zones link by displacement; the existing zone-id marker already prevents duplicates (control)."""
        assert self._n([_variant(0, "3827.86", zone_type=ZoneType.FVG), _variant(1, "3827.90", zone_type=ZoneType.FVG)]) == 1


class TestMechanismEvidence:
    """Documents the CAUSE (PASSES today; this is evidence, not a fix target of V4): the same historical candles produce different
    equal-level pool ids when mapped with a different tolerance, and the pipeline derives the tolerance from the LATEST ATR."""

    def _candles(self):
        out = []
        for i, low in enumerate(["2400.00", "2400.10", "2400.45"]):
            ts = T0 + timedelta(minutes=5 * i)
            out.append(Candle(candle_id=f"m{i}", symbol="XAUUSD", timeframe=Timeframe.M5, open=D("2410"), high=D("2412"), low=D(low), close=D("2410"),
                              timestamp_open=ts, timestamp_close=ts + timedelta(minutes=5), source=DataSource.BROKER_NATIVE, confirmed=True))
        return out

    def test_pool_identity_depends_on_the_tolerance_in_force(self):
        eng = LiquidityEngineImpl()
        small = {p.pool_id for p in eng.map_equal_level_pools(self._candles(), D("0.15")) if "LOW" in p.type.value}
        large = {p.pool_id for p in eng.map_equal_level_pools(self._candles(), D("0.60")) if "LOW" in p.type.value}
        assert small != large, "same candles, different tolerance -> different pool ids (the drift mechanism)"

    def test_pipeline_recomputes_tolerance_from_the_latest_atr_each_step(self):
        import inspect, backtest.pipeline as bp
        src = inspect.getsource(bp.ReplayPipeline.step)
        assert "equal_level_tolerance" in src and "atr" in src.lower()
