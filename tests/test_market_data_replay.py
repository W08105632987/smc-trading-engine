from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataSource
from market_data.candle_store.store import CandleStore
from market_data.replay.replay_engine import ReplayEngine


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


class TestReplayEngine:
    def test_each_step_only_sees_candles_up_to_that_point(self):
        store = CandleStore()
        store.add_many([make_candle(5), make_candle(10), make_candle(15)])
        engine = ReplayEngine(store, "XAUUSD", Timeframe.M5)

        steps = list(engine.run())
        assert len(steps) == 3
        # Step 0 (at candle 5's close): only candle 5 visible.
        assert [c.timestamp_close for c in steps[0].candles] == [_dt(5)]
        # Step 1 (at candle 10's close): candles 5 and 10 visible, never 15.
        assert [c.timestamp_close for c in steps[1].candles] == [_dt(5), _dt(10)]
        # Step 2: all three visible.
        assert [c.timestamp_close for c in steps[2].candles] == [_dt(5), _dt(10), _dt(15)]

    def test_empty_store_yields_no_steps(self):
        store = CandleStore()
        engine = ReplayEngine(store, "XAUUSD", Timeframe.M5)
        assert list(engine.run()) == []
