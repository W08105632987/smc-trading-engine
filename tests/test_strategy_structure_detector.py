from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.market_data import Candle, DataSource
from core.domain.structure import StructureEventType, SwingPoint, SwingType
from strategy.structure.bos_choch_detector import StructureDetectorImpl


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_candle(minute: int, high, low, close, open_=None) -> Candle:
    open_price = Decimal(str(open_ if open_ is not None else close))
    return Candle(
        candle_id=f"XAUUSD:H4:{_dt(minute + 5).isoformat()}",
        symbol="XAUUSD", timeframe=Timeframe.H4,
        open=open_price, high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        timestamp_open=_dt(minute), timestamp_close=_dt(minute + 5),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


def make_major_swing(swing_type: SwingType, price, confirmed_at_minute: int) -> SwingPoint:
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{price}",
        symbol="XAUUSD", timeframe=Timeframe.H4, type=swing_type, price=Decimal(str(price)),
        pivot_candle_id="pivot-candle", formation_timestamp=_dt(0),
        confirmation_timestamp=_dt(confirmed_at_minute),
        major_flag=True, major_confirmed_at=_dt(confirmed_at_minute + 1),
    )


class TestStructureDetector:
    def test_close_beyond_major_high_in_prevailing_direction_is_bos(self):
        swing = make_major_swing(SwingType.HIGH, 2410, confirmed_at_minute=10)
        candle = make_candle(20, high=2415, low=2408, close=2412)  # closes above 2410

        detector = StructureDetectorImpl()
        events = detector.detect([candle], [swing], prevailing_direction=Direction.BULLISH)

        assert len(events) == 1
        assert events[0].type == StructureEventType.BOS
        assert events[0].direction == Direction.BULLISH

    def test_close_beyond_major_high_against_prevailing_direction_is_choch(self):
        swing = make_major_swing(SwingType.HIGH, 2410, confirmed_at_minute=10)
        candle = make_candle(20, high=2415, low=2408, close=2412)

        detector = StructureDetectorImpl()
        # Prevailing direction is BEARISH — a bullish break is counter-trend.
        events = detector.detect([candle], [swing], prevailing_direction=Direction.BEARISH)

        assert len(events) == 1
        assert events[0].type == StructureEventType.CHOCH

    def test_wick_only_penetration_is_never_a_structure_event(self):
        # High pokes above 2410 but CLOSE stays below it — must not count.
        swing = make_major_swing(SwingType.HIGH, 2410, confirmed_at_minute=10)
        candle = make_candle(20, high=2412, low=2405, close=2408)  # wick above, close below

        detector = StructureDetectorImpl()
        events = detector.detect([candle], [swing], prevailing_direction=Direction.BULLISH)

        assert events == []

    def test_candle_before_swing_confirmation_cannot_trigger_event(self):
        # No-look-ahead: a candle closing beyond the level BEFORE the
        # swing's own confirmation_timestamp must never produce an event.
        swing = make_major_swing(SwingType.HIGH, 2410, confirmed_at_minute=30)
        early_candle = make_candle(10, high=2415, low=2408, close=2412)  # closes at minute 15

        detector = StructureDetectorImpl()
        events = detector.detect([early_candle], [swing], prevailing_direction=Direction.BULLISH)

        assert events == []

    def test_minor_swing_never_produces_a_structure_event(self):
        minor_swing = SwingPoint(
            swing_id="minor-1", symbol="XAUUSD", timeframe=Timeframe.H4, type=SwingType.HIGH,
            price=Decimal("2410"), pivot_candle_id="p1", formation_timestamp=_dt(0),
            confirmation_timestamp=_dt(10), major_flag=False,
        )
        candle = make_candle(20, high=2415, low=2408, close=2412)

        detector = StructureDetectorImpl()
        events = detector.detect([candle], [minor_swing], prevailing_direction=Direction.BULLISH)
        assert events == []

    def test_each_major_swing_only_broken_once(self):
        swing = make_major_swing(SwingType.HIGH, 2410, confirmed_at_minute=10)
        candle1 = make_candle(20, high=2415, low=2408, close=2412)
        candle2 = make_candle(30, high=2420, low=2411, close=2418)  # also closes above 2410

        detector = StructureDetectorImpl()
        events = detector.detect([candle1, candle2], [swing], prevailing_direction=Direction.BULLISH)

        assert len(events) == 1  # only the first break counts; swing is "used up"
