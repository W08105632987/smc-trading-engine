"""Temporal leakage tests. These don't just confirm correct behavior —
they actively try to construct a situation where future information
could leak into a past decision, and assert it's impossible.
Milestone 2 review gate, item 6."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.market_data import Candle, DataSource
from core.domain.structure import StructureEventType, SwingType
from market_data.candle_store.store import CandleStore
from market_data.replay.replay_engine import ReplayEngine
from strategy.structure.bos_choch_detector import StructureDetectorImpl
from strategy.swings.swing_detector import SwingDetectorImpl


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_candle(index: int, high, low, close) -> Candle:
    return Candle(
        candle_id=f"XAUUSD:M5:{_dt(index * 5 + 5).isoformat()}",
        symbol="XAUUSD", timeframe=Timeframe.M5,
        open=Decimal(str(close)), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        timestamp_open=_dt(index * 5), timestamp_close=_dt(index * 5 + 5),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


# A swing high at index 2: 105 > all neighbors within the fractal window.
HIGHS = [100, 101, 105, 102, 101]
LOWS = [95, 96, 100, 97, 96]
CLOSES = [99, 100, 103, 101, 100]


class TestSwingDetectorCannotSeeFutureCandles:
    """The strongest possible leakage attack on the fractal algorithm:
    feed it truncated candle windows and prove the swing simply cannot
    be produced before ALL of its confirming right-side candles exist
    in the input — there's no way for the algorithm to 'peek ahead'
    because the data it would need to peek at literally isn't there."""

    def test_swing_absent_when_only_pivot_candle_available(self):
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(HIGHS, LOWS, CLOSES))]
        truncated = candles[:3]  # candles 0,1,2 — pivot itself, but zero right-side candles
        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        assert detector.detect(truncated) == []

    def test_swing_absent_with_only_one_of_two_required_right_candles(self):
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(HIGHS, LOWS, CLOSES))]
        truncated = candles[:4]  # missing the second required right-side candle
        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        assert detector.detect(truncated) == []

    def test_swing_present_only_once_all_confirming_candles_exist(self):
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(HIGHS, LOWS, CLOSES))]
        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))
        assert len(detector.detect(candles)) == 1


class TestReplayPipelineNeverExposesUnconfirmedSwings:
    """Integration-level leakage test: at every step of a realistic
    replay, run the full swing detector against only the data visible at
    that step, and assert no swing's confirmation_timestamp is ever
    later than the replay step's own timestamp — which would be a
    contradiction (a thing 'confirmed' after the moment you're claiming
    to know it)."""

    def test_no_swing_confirmation_timestamp_exceeds_its_own_replay_step(self):
        store = CandleStore()
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(HIGHS, LOWS, CLOSES))]
        # Add a few more candles so replay has more than one step to walk through.
        candles += [
            make_candle(5, 99, 94, 96),
            make_candle(6, 98, 93, 95),
        ]
        store.add_many(candles)
        replay = ReplayEngine(store, "XAUUSD", Timeframe.M5)
        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))

        violations = []
        for step in replay.run():
            swings = detector.detect(step.candles)
            for swing in swings:
                if swing.confirmation_timestamp > step.at:
                    violations.append((step.at, swing.confirmation_timestamp))

        assert violations == [], f"look-ahead detected: {violations}"

    def test_swing_becomes_visible_exactly_at_its_confirmation_step_not_before(self):
        store = CandleStore()
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(HIGHS, LOWS, CLOSES))]
        store.add_many(candles)
        replay = ReplayEngine(store, "XAUUSD", Timeframe.M5)
        detector = SwingDetectorImpl(major_swing_atr_multiple=Decimal("1.0"))

        # The swing's confirmation_timestamp is candle[4]'s close (_dt(25)).
        # It must be ABSENT at every step before that, and PRESENT from
        # that step onward.
        seen_before_confirmation = False
        seen_at_confirmation = False
        for step in replay.run():
            swings = detector.detect(step.candles)
            if step.at < _dt(25) and len(swings) > 0:
                seen_before_confirmation = True
            if step.at == _dt(25) and len(swings) == 1:
                seen_at_confirmation = True

        assert seen_before_confirmation is False
        assert seen_at_confirmation is True


class TestStructureDetectorCannotUseUnconfirmedSwings:
    """Complements the existing structure-detector test suite with an
    explicit adversarial framing: construct a swing that is NOT yet
    confirmed relative to the candle under test, and confirm the
    detector refuses to react to it — even though the price data alone
    would otherwise justify a BOS/CHoCH."""

    def test_structurally_valid_break_is_still_rejected_before_swing_confirmation(self):
        from core.domain.structure import SwingPoint

        # Swing confirms at minute 100 — deliberately far in the future
        # relative to the candle that "breaks" it.
        swing = SwingPoint(
            swing_id="s1", symbol="XAUUSD", timeframe=Timeframe.H4, type=SwingType.HIGH,
            price=Decimal("2410"), pivot_candle_id="p1", formation_timestamp=_dt(0),
            confirmation_timestamp=_dt(100), major_flag=True, major_confirmed_at=_dt(101),
        )
        # This candle closes convincingly above 2410 at minute 15 — long
        # before the swing is confirmed at minute 100.
        early_break_candle = make_candle(2, 2415, 2408, 2412)  # closes at minute 15

        detector = StructureDetectorImpl()
        events = detector.detect(
            [early_break_candle], [swing], prevailing_direction=Direction.BULLISH
        )
        assert events == []
