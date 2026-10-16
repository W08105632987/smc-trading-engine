from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataSource
from market_data.candle_store.store import CandleStore


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_candle(close_min: int) -> Candle:
    return Candle(
        candle_id=f"XAUUSD:M5:{_dt(close_min).isoformat()}",
        symbol="XAUUSD", timeframe=Timeframe.M5,
        open=Decimal("2400"), high=Decimal("2401"), low=Decimal("2399"), close=Decimal("2400.5"),
        timestamp_open=_dt(close_min - 5), timestamp_close=_dt(close_min),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


class TestCandleStore:
    def test_get_up_to_excludes_future_candles(self):
        # THE critical test: this is the data-layer's no-look-ahead guarantee.
        store = CandleStore()
        store.add_many([make_candle(5), make_candle(10), make_candle(15), make_candle(20)])

        result = store.get_up_to("XAUUSD", Timeframe.M5, _dt(12))
        closes = [c.timestamp_close for c in result]
        assert closes == [_dt(5), _dt(10)]
        assert _dt(15) not in closes and _dt(20) not in closes

    def test_get_up_to_is_inclusive_of_exact_timestamp(self):
        store = CandleStore()
        store.add_many([make_candle(5), make_candle(10)])
        result = store.get_up_to("XAUUSD", Timeframe.M5, _dt(10))
        assert len(result) == 2

    def test_get_up_to_unknown_symbol_returns_empty(self):
        store = CandleStore()
        result = store.get_up_to("EURUSD", Timeframe.M5, _dt(100))
        assert result == []

    def test_latest_respects_up_to_boundary(self):
        store = CandleStore()
        store.add_many([make_candle(5), make_candle(10), make_candle(20)])
        latest = store.latest("XAUUSD", Timeframe.M5, _dt(12))
        assert latest.timestamp_close == _dt(10)

    def test_add_maintains_sorted_order_regardless_of_insertion_order(self):
        store = CandleStore()
        store.add(make_candle(20))
        store.add(make_candle(5))
        store.add(make_candle(10))
        all_candles = store.all("XAUUSD", Timeframe.M5)
        closes = [c.timestamp_close for c in all_candles]
        assert closes == sorted(closes)

    def test_symbols_and_timeframes_are_isolated(self):
        store = CandleStore()
        store.add(make_candle(5))
        h1_candle = make_candle(5).model_copy(update={"timeframe": Timeframe.H1})
        store.add(h1_candle)
        assert len(store.all("XAUUSD", Timeframe.M5)) == 1
        assert len(store.all("XAUUSD", Timeframe.H1)) == 1
