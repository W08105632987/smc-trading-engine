from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.liquidity import SweepEvent
from core.domain.market_data import Candle, DataSource
from core.domain.structure import SwingPoint, SwingType
from strategy.displacement.displacement_engine import DisplacementEngineImpl


def _dt(hour: float) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(hours=hour)


def make_candle(hour: float, open_, close, high=None, low=None) -> Candle:
    high = Decimal(str(high)) if high is not None else max(Decimal(str(open_)), Decimal(str(close)))
    low = Decimal(str(low)) if low is not None else min(Decimal(str(open_)), Decimal(str(close)))
    return Candle(
        candle_id=f"XAUUSD:H1:{_dt(hour).isoformat()}",
        symbol="XAUUSD", timeframe=Timeframe.H1,
        open=Decimal(str(open_)), high=high, low=low, close=Decimal(str(close)),
        timestamp_open=_dt(hour - 1), timestamp_close=_dt(hour),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


def noise_candles(n: int, base_hour: float, body: Decimal = Decimal("0.5")) -> list[Candle]:
    """n small-body candles for median-lookback context."""
    return [make_candle(base_hour + i, 2400, 2400 + body) for i in range(n)]


def make_major_swing(swing_type: SwingType, price, confirmed_at_hour: float) -> SwingPoint:
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{price}", symbol="XAUUSD", timeframe=Timeframe.H1,
        type=swing_type, price=Decimal(str(price)), pivot_candle_id="pivot",
        formation_timestamp=_dt(confirmed_at_hour - 1), confirmation_timestamp=_dt(confirmed_at_hour),
        major_flag=True, major_confirmed_at=_dt(confirmed_at_hour + 0.5),
    )


class TestDisplacementDetection:
    def test_qualifying_displacement_detected(self):
        history = noise_candles(20, base_hour=1)
        # Displacement candle must CLOSE beyond the swing (2398 < 2399)
        # for the swing to register as broken — not just have a big body.
        swing = make_major_swing(SwingType.LOW, price=2399, confirmed_at_hour=15)
        displacement_candle = make_candle(21, open_=2401, close=2398, high=2401.5, low=2397.5)
        # body = |2398-2401| = 3.0; median of noise bodies = 0.5 -> ratio 6.0 (>=1.5 required)
        # range = 4.0; with ATR=1.0 -> range_atr_ratio=4.0 (>=1.2 required)
        candles = history + [displacement_candle]
        atr_by_id = {displacement_candle.candle_id: Decimal("1.0")}

        engine = DisplacementEngineImpl(
            body_median_multiple=Decimal("1.5"), range_atr_multiple=Decimal("1.2")
        )
        events = engine.detect(candles, [swing], atr_by_id, median_lookback=20)

        assert len(events) == 1
        assert events[0].direction == Direction.BEARISH  # close < open
        assert events[0].broken_swing_id == swing.swing_id

    def test_insufficient_body_ratio_rejected(self):
        history = noise_candles(20, base_hour=1)
        swing = make_major_swing(SwingType.LOW, price=2399, confirmed_at_hour=15)
        # Body only 0.6 vs median 0.5 -> ratio 1.2, below the 1.5 requirement.
        weak_candle = make_candle(21, open_=2400.6, close=2400, high=2400.7, low=2398)
        candles = history + [weak_candle]
        atr_by_id = {weak_candle.candle_id: Decimal("1.0")}

        engine = DisplacementEngineImpl(
            body_median_multiple=Decimal("1.5"), range_atr_multiple=Decimal("1.2")
        )
        events = engine.detect(candles, [swing], atr_by_id, median_lookback=20)
        assert events == []

    def test_no_broken_swing_rejected_even_with_strong_candle(self):
        history = noise_candles(20, base_hour=1)
        # No swing at all provided — even a strong candle can't qualify.
        strong_candle = make_candle(21, open_=2401, close=2400, high=2401, low=2398.5)
        candles = history + [strong_candle]
        atr_by_id = {strong_candle.candle_id: Decimal("1.0")}

        engine = DisplacementEngineImpl(
            body_median_multiple=Decimal("1.5"), range_atr_multiple=Decimal("1.2")
        )
        events = engine.detect(candles, [], atr_by_id, median_lookback=20)
        assert events == []

    def test_insufficient_history_for_median_rejected(self):
        # Fewer than median_lookback candles exist — must not guess a median.
        swing = make_major_swing(SwingType.LOW, price=2399, confirmed_at_hour=1)
        few_candles = noise_candles(5, base_hour=1)
        atr_by_id = {c.candle_id: Decimal("1.0") for c in few_candles}

        engine = DisplacementEngineImpl(
            body_median_multiple=Decimal("1.5"), range_atr_multiple=Decimal("1.2")
        )
        events = engine.detect(few_candles, [swing], atr_by_id, median_lookback=20)
        assert events == []

    def test_swing_not_yet_confirmed_cannot_be_broken(self):
        # No-look-ahead: swing confirms AFTER the displacement candle.
        history = noise_candles(20, base_hour=1)
        swing = make_major_swing(SwingType.LOW, price=2399, confirmed_at_hour=50)  # far future
        displacement_candle = make_candle(21, open_=2401, close=2400, high=2401, low=2398.5)
        candles = history + [displacement_candle]
        atr_by_id = {displacement_candle.candle_id: Decimal("1.0")}

        engine = DisplacementEngineImpl(
            body_median_multiple=Decimal("1.5"), range_atr_multiple=Decimal("1.2")
        )
        events = engine.detect(candles, [swing], atr_by_id, median_lookback=20)
        assert events == []


class TestMSSComposition:
    def test_valid_sweep_plus_matching_displacement_produces_mss(self):
        sweep = SweepEvent(
            sweep_id="sw1", liquidity_pool_id="p1", direction=Direction.BULLISH,
            penetration_atr=Decimal("0.2"), sweep_candle_id="c1", close_back_candle_id="c1",
            reversal_confirmed_at=_dt(10), expiry_at=_dt(13), valid=True,
        )
        history = noise_candles(20, base_hour=1)
        swing = make_major_swing(SwingType.HIGH, price=2420, confirmed_at_hour=5)
        displacement_candle = make_candle(21, open_=2400, close=2422, high=2423, low=2399)
        candles = history + [displacement_candle]
        atr_by_id = {displacement_candle.candle_id: Decimal("1.0")}

        disp_engine = DisplacementEngineImpl(
            body_median_multiple=Decimal("1.5"), range_atr_multiple=Decimal("1.2")
        )
        displacements = disp_engine.detect(candles, [swing], atr_by_id, median_lookback=20)
        assert len(displacements) == 1  # sanity check on the fixture itself

        mss_events = disp_engine.detect_mss([sweep], displacements)
        assert len(mss_events) == 1
        assert mss_events[0].sweep_id == "sw1"
        assert mss_events[0].confirmed_at == max(sweep.reversal_confirmed_at, displacements[0].confirmed_at)

    def test_invalid_sweep_never_produces_mss(self):
        sweep = SweepEvent(
            sweep_id="sw1", liquidity_pool_id="p1", direction=Direction.BULLISH,
            penetration_atr=Decimal("0.2"), sweep_candle_id="c1", close_back_candle_id="c1",
            reversal_confirmed_at=None, expiry_at=_dt(13), valid=False,
        )
        engine = DisplacementEngineImpl(
            body_median_multiple=Decimal("1.5"), range_atr_multiple=Decimal("1.2")
        )
        assert engine.detect_mss([sweep], []) == []

    def test_mismatched_direction_never_pairs(self):
        sweep = SweepEvent(
            sweep_id="sw1", liquidity_pool_id="p1", direction=Direction.BULLISH,
            penetration_atr=Decimal("0.2"), sweep_candle_id="c1", close_back_candle_id="c1",
            reversal_confirmed_at=_dt(10), expiry_at=_dt(13), valid=True,
        )
        history = noise_candles(20, base_hour=1)
        # LOW-type swing priced ABOVE the candle's close so it actually
        # gets broken by this bearish candle (close must be < swing price).
        swing = make_major_swing(SwingType.LOW, price=2400, confirmed_at_hour=5)
        # BEARISH displacement — direction mismatch with the BULLISH sweep.
        bearish_displacement = make_candle(21, open_=2401, close=2399, high=2402, low=2379)
        candles = history + [bearish_displacement]
        atr_by_id = {bearish_displacement.candle_id: Decimal("1.0")}

        engine = DisplacementEngineImpl(
            body_median_multiple=Decimal("1.5"), range_atr_multiple=Decimal("1.2")
        )
        displacements = engine.detect(candles, [swing], atr_by_id, median_lookback=20)
        assert len(displacements) == 1
        assert displacements[0].direction == Direction.BEARISH

        mss_events = engine.detect_mss([sweep], displacements)
        assert mss_events == []
