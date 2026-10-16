from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataSource
from market_data.quality.gap_detector import detect_gaps_in_candles, detect_gaps_in_timestamps


def _dt(minutes: int) -> datetime:
    return datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _candle(open_min: int, close_min: int, timeframe: Timeframe = Timeframe.M5) -> Candle:
    ts_open, ts_close = _dt(open_min), _dt(close_min)
    return Candle(
        candle_id=Candle.build_id("XAUUSD", timeframe, ts_close), symbol="XAUUSD", timeframe=timeframe,
        open=Decimal("2400"), high=Decimal("2401"), low=Decimal("2399"), close=Decimal("2400.5"),
        timestamp_open=ts_open, timestamp_close=ts_close, source=DataSource.BROKER_NATIVE, confirmed=True,
    )


class TestCandleGapDetection:
    """Category A/B: continuous sequence -> no gap; discontinuity -> gap detected."""

    def test_continuous_sequence_has_no_gaps(self):
        candles = [_candle(0, 5), _candle(5, 10), _candle(10, 15)]
        assert detect_gaps_in_candles(candles) == []

    def test_discontinuity_is_detected(self):
        candles = [_candle(0, 5), _candle(60, 65)]  # 55-minute silence
        gaps = detect_gaps_in_candles(candles)
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap.gap_start == _dt(5)
        assert gap.gap_end == _dt(60)
        assert gap.duration == timedelta(minutes=55)
        assert gap.affected_timeframe == "M5"
        assert gap.classification == "DATA_GAP"

    def test_single_candle_has_no_gaps(self):
        assert detect_gaps_in_candles([_candle(0, 5)]) == []

    def test_empty_list_has_no_gaps(self):
        assert detect_gaps_in_candles([]) == []


class TestMultipleGapsDetectedIndependently:
    """Category F."""

    def test_three_separate_gaps_each_recorded(self):
        candles = [
            _candle(0, 5),
            _candle(60, 65),     # gap 1: 5 -> 60
            _candle(70, 75),     # gap 2: 65 -> 70
            _candle(1000, 1005),  # gap 3: 75 -> 1000
        ]
        gaps = detect_gaps_in_candles(candles)
        assert len(gaps) == 3
        assert [g.duration for g in gaps] == [
            timedelta(minutes=55), timedelta(minutes=5), timedelta(minutes=925),
        ]


class TestDeterminism:
    """Category G."""

    def test_same_input_produces_identical_gap_records(self):
        candles = [_candle(0, 5), _candle(60, 65)]
        first = detect_gaps_in_candles(candles)
        second = detect_gaps_in_candles(candles)
        assert first == second


class TestWeekendFact:
    def test_gap_spanning_a_saturday_is_flagged_as_such(self):
        # 2026-01-09 is a Friday; 2026-01-12 is a Monday -- the interval includes Sat 1/10 and Sun 1/11.
        friday_close = datetime(2026, 1, 9, 22, 0, tzinfo=timezone.utc)
        monday_open = datetime(2026, 1, 12, 0, 0, tzinfo=timezone.utc)
        c1 = _candle(0, 5)  # placeholder, will overwrite timestamps directly below
        candles = [
            c1.model_copy(update={"timestamp_open": friday_close - timedelta(minutes=5), "timestamp_close": friday_close}),
            c1.model_copy(update={"timestamp_open": monday_open, "timestamp_close": monday_open + timedelta(minutes=5)}),
        ]
        gaps = detect_gaps_in_candles(candles)
        assert len(gaps) == 1
        assert gaps[0].spans_weekend is True

    def test_midweek_gap_does_not_span_weekend(self):
        candles = [_candle(0, 5), _candle(60, 65)]  # same Monday, no Saturday in range
        gaps = detect_gaps_in_candles(candles)
        assert gaps[0].spans_weekend is False


class TestTickTimestampGapDetection:
    def test_no_gap_within_threshold(self):
        timestamps = [_dt(0), _dt(1), _dt(2)]
        assert detect_gaps_in_timestamps(timestamps, timedelta(minutes=5)) == []

    def test_gap_beyond_threshold_detected(self):
        timestamps = [_dt(0), _dt(1), _dt(200)]
        gaps = detect_gaps_in_timestamps(timestamps, timedelta(minutes=5))
        assert len(gaps) == 1
        assert gaps[0].duration == timedelta(minutes=199)
        assert gaps[0].affected_timeframe == "TICK"

    def test_custom_label_is_used(self):
        gaps = detect_gaps_in_timestamps([_dt(0), _dt(200)], timedelta(minutes=5), label="CUSTOM")
        assert gaps[0].affected_timeframe == "CUSTOM"
