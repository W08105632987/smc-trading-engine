from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.domain.common import Timeframe
from core.domain.market_data import ATRContext, Candle, DataSource, SymbolSpecification


def _dt(hour: int) -> datetime:
    return datetime(2026, 1, 5, hour, 0, tzinfo=timezone.utc)


def make_candle(**overrides) -> Candle:
    defaults = dict(
        candle_id="XAUUSD:M5:2026-01-05T00:05:00+00:00",
        symbol="XAUUSD",
        timeframe=Timeframe.M5,
        open=Decimal("2400.00"),
        high=Decimal("2401.00"),
        low=Decimal("2399.50"),
        close=Decimal("2400.50"),
        volume=Decimal("100"),
        timestamp_open=_dt(0),
        timestamp_close=_dt(0).replace(minute=5),
        source=DataSource.BROKER_NATIVE,
        confirmed=True,
    )
    defaults.update(overrides)
    return Candle(**defaults)


class TestCandle:
    def test_valid_candle_constructs(self):
        c = make_candle()
        assert c.symbol == "XAUUSD"
        assert c.confirmed is True

    def test_naive_datetime_rejected(self):
        # Strategy FROZEN v1.0 §4 / Technical Architecture Time Model:
        # naive datetimes are a bug, not a style violation.
        with pytest.raises(ValidationError):
            make_candle(timestamp_open=datetime(2026, 1, 5, 0, 0))

    def test_high_below_open_close_rejected(self):
        with pytest.raises(ValidationError):
            make_candle(high=Decimal("2399.00"))  # below open/close

    def test_low_above_open_close_rejected(self):
        with pytest.raises(ValidationError):
            make_candle(low=Decimal("2400.20"))  # above open

    def test_high_below_low_rejected(self):
        with pytest.raises(ValidationError):
            make_candle(high=Decimal("2398.00"), low=Decimal("2399.00"))

    def test_close_before_open_timestamp_rejected(self):
        with pytest.raises(ValidationError):
            make_candle(timestamp_close=_dt(0))  # equal to timestamp_open

    def test_candle_is_immutable(self):
        c = make_candle()
        with pytest.raises(ValidationError):
            c.close = Decimal("9999")

    def test_build_id_is_deterministic(self):
        ts = _dt(0).replace(minute=5)
        id1 = Candle.build_id("XAUUSD", Timeframe.M5, ts)
        id2 = Candle.build_id("XAUUSD", Timeframe.M5, ts)
        assert id1 == id2
        assert "XAUUSD" in id1 and "M5" in id1

    def test_build_id_differs_by_timeframe(self):
        ts = _dt(0).replace(minute=5)
        id_m5 = Candle.build_id("XAUUSD", Timeframe.M5, ts)
        id_h1 = Candle.build_id("XAUUSD", Timeframe.H1, ts)
        assert id_m5 != id_h1


class TestSymbolSpecification:
    def test_valid_spec(self):
        spec = SymbolSpecification(
            symbol="XAUUSD",
            digits=2,
            tick_size=Decimal("0.01"),
            tick_value=Decimal("1.00"),
            contract_size=Decimal("100"),
            min_volume=Decimal("0.01"),
            max_volume=Decimal("100"),
            volume_step=Decimal("0.01"),
        )
        assert spec.symbol == "XAUUSD"

    def test_min_greater_than_max_rejected(self):
        with pytest.raises(ValidationError):
            SymbolSpecification(
                symbol="XAUUSD", digits=2, tick_size=Decimal("0.01"),
                tick_value=Decimal("1.00"), contract_size=Decimal("100"),
                min_volume=Decimal("10"), max_volume=Decimal("1"),
                volume_step=Decimal("0.01"),
            )

    def test_zero_volume_step_rejected(self):
        with pytest.raises(ValidationError):
            SymbolSpecification(
                symbol="XAUUSD", digits=2, tick_size=Decimal("0.01"),
                tick_value=Decimal("1.00"), contract_size=Decimal("100"),
                min_volume=Decimal("0.01"), max_volume=Decimal("100"),
                volume_step=Decimal("0"),
            )


class TestATRContext:
    def test_valid_atr(self):
        ctx = ATRContext(
            symbol="XAUUSD", timeframe=Timeframe.M5, period=14,
            value=Decimal("1.25"), computed_at=_dt(0),
        )
        assert ctx.value == Decimal("1.25")

    def test_non_positive_atr_rejected(self):
        with pytest.raises(ValidationError):
            ATRContext(
                symbol="XAUUSD", timeframe=Timeframe.M5, period=14,
                value=Decimal("0"), computed_at=_dt(0),
            )
