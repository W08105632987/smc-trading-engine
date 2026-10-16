from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataSource
from core.domain.structure import SwingType
from strategy.swings.swing_detector import SwingDetectorImpl


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_candles(highs: list[float], lows: list[float], closes: list[float]) -> list[Candle]:
    # open == close for each synthetic candle: this is a test-fixture
    # simplification (real candles have independent opens), but it
    # guarantees high >= max(open, close) and low <= min(open, close)
    # hold for arbitrary high/low/close arrays without needing the
    # fixture to reason about cross-candle continuity.
    candles = []
    for i, (h, l, c) in enumerate(zip(highs, lows, closes)):
        candles.append(
            Candle(
                candle_id=f"XAUUSD:M5:{_dt(i * 5 + 5).isoformat()}",
                symbol="XAUUSD", timeframe=Timeframe.M5,
                open=Decimal(str(c)), high=Decimal(str(h)), low=Decimal(str(l)), close=Decimal(str(c)),
                timestamp_open=_dt(i * 5), timestamp_close=_dt(i * 5 + 5),
                source=DataSource.BROKER_NATIVE, confirmed=True,
            )
        )
    return candles


class TestSwingHighDetection:
    def test_clear_swing_high_detected(self):
        # index 2 is a clear swing high: 105 > 100,101,102,101
        highs = [100, 101, 105, 102, 101]
        lows = [95, 96, 100, 97, 96]
        closes = [99, 100, 103, 101, 100]
        candles = make_candles(highs, lows, closes)

        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        swings = detector.detect(candles)

        highs_found = [s for s in swings if s.type == SwingType.HIGH]
        assert len(highs_found) == 1
        assert highs_found[0].price == Decimal("105")
        assert highs_found[0].pivot_candle_id == candles[2].candle_id

    def test_confirmation_timestamp_is_two_candles_after_formation(self):
        # THE critical no-look-ahead check for this detector: confirmation
        # must be candle[i+2]'s close, never the pivot's own close.
        highs = [100, 101, 105, 102, 101]
        lows = [95, 96, 100, 97, 96]
        closes = [99, 100, 103, 101, 100]
        candles = make_candles(highs, lows, closes)

        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        swings = detector.detect(candles)
        swing = swings[0]

        assert swing.formation_timestamp == candles[2].timestamp_close
        assert swing.confirmation_timestamp == candles[4].timestamp_close
        assert swing.confirmation_timestamp > swing.formation_timestamp

    def test_no_swing_when_pivot_is_not_strictly_greatest(self):
        # Tie at the pivot — not strictly greater, so NOT a swing high.
        highs = [100, 101, 101, 102, 101]
        lows = [95, 96, 96, 97, 96]
        closes = [99, 100, 100, 101, 100]
        candles = make_candles(highs, lows, closes)

        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        swings = detector.detect(candles)
        assert len(swings) == 0

    def test_insufficient_candles_returns_empty(self):
        highs = [100, 101, 105]
        lows = [95, 96, 100]
        closes = [99, 100, 103]
        candles = make_candles(highs, lows, closes)  # only 3 candles, need 5 minimum

        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        assert detector.detect(candles) == []


class TestSwingLowDetection:
    def test_clear_swing_low_detected(self):
        highs = [105, 104, 100, 103, 104]
        lows = [100, 99, 90, 96, 97]  # index 2 = clear swing low
        closes = [101, 100, 92, 98, 99]
        candles = make_candles(highs, lows, closes)

        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        swings = detector.detect(candles)

        lows_found = [s for s in swings if s.type == SwingType.LOW]
        assert len(lows_found) == 1
        assert lows_found[0].price == Decimal("90")


class TestMajorSwingDetermination:
    def test_major_flag_true_when_displacement_qualifies(self):
        highs = [100, 101, 105, 102, 101, 98, 95, 92]
        lows = [95, 96, 100, 97, 96, 93, 90, 87]
        closes = [99, 100, 103, 101, 100, 94, 91, 88]  # strong bearish move away from the high
        candles = make_candles(highs, lows, closes)

        # ATR of 1.0 means threshold = 1.0 x 1.0 = 1.0 — easily reached by
        # the ~15-point move away from the swing high (105) down to 88.
        atr_by_id = {c.candle_id: Decimal("1.0") for c in candles}

        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        swings = detector.detect(candles, atr_by_candle_id=atr_by_id)
        swing_high = next(s for s in swings if s.type == SwingType.HIGH)

        assert swing_high.major_flag is True
        assert swing_high.major_confirmed_at is not None

    def test_major_flag_false_without_atr_context(self):
        # No ATR supplied at all — must not guess; major_flag stays False.
        highs = [100, 101, 105, 102, 101, 98, 95, 92]
        lows = [95, 96, 100, 97, 96, 93, 90, 87]
        closes = [99, 100, 103, 101, 100, 94, 91, 88]
        candles = make_candles(highs, lows, closes)

        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        swings = detector.detect(candles)  # atr_by_candle_id defaults to None
        swing_high = next(s for s in swings if s.type == SwingType.HIGH)

        assert swing_high.major_flag is False
        assert swing_high.major_confirmed_at is None

    def test_major_flag_false_when_invalidated_before_qualifying(self):
        # Candles 0-4 form the same clean swing high at idx2 (105) as the
        # earlier tests — idx5-7 sit AFTER the fractal window and close
        # back above 105 immediately, invalidating before displacing far
        # enough away to qualify as major.
        highs = [100, 101, 105, 102, 101, 107, 108, 109]
        lows = [95, 96, 100, 97, 96, 101, 102, 103]
        closes = [99, 100, 103, 101, 100, 106, 107, 108]
        candles = make_candles(highs, lows, closes)
        atr_by_id = {c.candle_id: Decimal("1.0") for c in candles}

        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        swings = detector.detect(candles, atr_by_candle_id=atr_by_id)
        swing_high = next(s for s in swings if s.type == SwingType.HIGH)

        assert swing_high.price == Decimal("105")
        assert swing_high.major_flag is False
