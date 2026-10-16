from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.liquidity import DisplacementEvent, MSSEvent
from core.domain.market_data import Candle, DataSource
from strategy.zones.zone_engine import ZoneEngineImpl


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_candle(minute: int, open_, high, low, close) -> Candle:
    return Candle(
        candle_id=f"XAUUSD:M5:{_dt(minute).isoformat()}",
        symbol="XAUUSD", timeframe=Timeframe.M5,
        open=Decimal(str(open_)), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        timestamp_open=_dt(minute - 5), timestamp_close=_dt(minute),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


class TestFVGDetection:
    def test_bullish_fvg_detected(self):
        # candle3.low (2405) > candle1.high (2402) -> bullish FVG
        c1 = make_candle(5, 2400, 2402, 2399, 2401)
        c2 = make_candle(10, 2401, 2404, 2400, 2403)
        c3 = make_candle(15, 2403, 2407, 2402.5, 2406)
        engine = ZoneEngineImpl()
        fvgs = engine.detect_fvgs([c1, c2, c3])

        assert len(fvgs) == 1
        assert fvgs[0].direction == Direction.BULLISH
        assert fvgs[0].lower == Decimal("2402")
        assert fvgs[0].upper == Decimal("2402.5")
        assert fvgs[0].creation_timestamp == c3.timestamp_close

    def test_bearish_fvg_detected(self):
        # candle3.high (2395) < candle1.low (2398) -> bearish FVG
        c1 = make_candle(5, 2400, 2401, 2398, 2399)
        c2 = make_candle(10, 2399, 2400, 2396, 2397)
        c3 = make_candle(15, 2394.5, 2395, 2393, 2394)
        engine = ZoneEngineImpl()
        fvgs = engine.detect_fvgs([c1, c2, c3])

        assert len(fvgs) == 1
        assert fvgs[0].direction == Direction.BEARISH
        assert fvgs[0].upper == Decimal("2398")
        assert fvgs[0].lower == Decimal("2395")

    def test_no_gap_produces_no_fvg(self):
        c1 = make_candle(5, 2400, 2402, 2399, 2401)
        c2 = make_candle(10, 2401, 2403, 2400, 2402)
        c3 = make_candle(15, 2402, 2403, 2401, 2402)  # overlaps c1's range — no gap
        engine = ZoneEngineImpl()
        assert engine.detect_fvgs([c1, c2, c3]) == []

    def test_fvg_links_to_displacement_when_candle3_matches(self):
        c1 = make_candle(5, 2400, 2402, 2399, 2401)
        c2 = make_candle(10, 2401, 2404, 2400, 2403)
        c3 = make_candle(15, 2403, 2407, 2402.5, 2406)
        displacement = DisplacementEvent(
            displacement_id="disp1", direction=Direction.BULLISH, candle_id=c3.candle_id,
            body_ratio=Decimal("2.0"), range_atr_ratio=Decimal("1.5"),
            broken_swing_id="swing1", valid=True, confirmed_at=c3.timestamp_close,
        )
        engine = ZoneEngineImpl()
        fvgs = engine.detect_fvgs([c1, c2, c3], displacements_by_candle_id={c3.candle_id: displacement})
        assert fvgs[0].linked_displacement_id == "disp1"


class TestOrderBlockDetection:
    def test_bullish_ob_from_bearish_origin_candle(self):
        origin = make_candle(5, open_=2401, close=2399, high=2401.5, low=2398.5)  # bearish (close<open)
        displacement_candle = make_candle(10, open_=2399, close=2405, high=2405.5, low=2398.5)
        candles = [origin, displacement_candle]

        displacement = DisplacementEvent(
            displacement_id="disp1", direction=Direction.BULLISH, candle_id=displacement_candle.candle_id,
            body_ratio=Decimal("2.0"), range_atr_ratio=Decimal("1.5"),
            broken_swing_id="swing1", valid=True, confirmed_at=displacement_candle.timestamp_close,
        )
        mss = MSSEvent(
            mss_id="mss1", direction=Direction.BULLISH, sweep_id="sw1",
            displacement_id="disp1", broken_swing_id="swing1", confirmed_at=displacement_candle.timestamp_close,
        )

        engine = ZoneEngineImpl()
        obs = engine.detect_order_blocks(candles, [mss], {"disp1": displacement})

        assert len(obs) == 1
        assert obs[0].direction == Direction.BULLISH
        assert obs[0].origin_candle_id == origin.candle_id
        assert obs[0].origin_candle_direction == Direction.BEARISH

    def test_bullish_mss_with_bullish_origin_candle_rejected(self):
        # Origin candle is BULLISH (close>open) — doesn't qualify as a
        # bullish OB origin (Phase 2 §13: not every opposite candle counts).
        origin = make_candle(5, open_=2399, close=2401, high=2401.5, low=2398.5)  # bullish
        displacement_candle = make_candle(10, open_=2399, close=2405, high=2405.5, low=2398.5)
        candles = [origin, displacement_candle]

        displacement = DisplacementEvent(
            displacement_id="disp1", direction=Direction.BULLISH, candle_id=displacement_candle.candle_id,
            body_ratio=Decimal("2.0"), range_atr_ratio=Decimal("1.5"),
            broken_swing_id="swing1", valid=True, confirmed_at=displacement_candle.timestamp_close,
        )
        mss = MSSEvent(
            mss_id="mss1", direction=Direction.BULLISH, sweep_id="sw1",
            displacement_id="disp1", broken_swing_id="swing1", confirmed_at=displacement_candle.timestamp_close,
        )

        engine = ZoneEngineImpl()
        obs = engine.detect_order_blocks(candles, [mss], {"disp1": displacement})
        assert obs == []

    def test_missing_displacement_reference_is_skipped_not_erroring(self):
        mss = MSSEvent(
            mss_id="mss1", direction=Direction.BULLISH, sweep_id="sw1",
            displacement_id="does-not-exist", broken_swing_id="swing1", confirmed_at=_dt(10),
        )
        engine = ZoneEngineImpl()
        obs = engine.detect_order_blocks([], [mss], {})
        assert obs == []
