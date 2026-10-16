from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.domain.common import Direction, Environment
from core.domain.decision import EntryType, TradeDecision


def _dt(minute: int) -> datetime:
    # timedelta-based so any minute offset (not just 0-59) is valid.
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_decision(**overrides) -> TradeDecision:
    defaults = dict(
        decision_id="d1", strategy_version="strategy-v1", config_hash="abc123",
        setup_id="s1", symbol="XAUUSD", direction=Direction.BULLISH,
        entry_type=EntryType.MARKET, intended_entry_price=Decimal("2400"),
        execution_price_policy=Decimal("0.10"), stop_loss=Decimal("2395"),
        take_profit_1=Decimal("2405"), take_profit_2=Decimal("2415"),
        tp_allocation=(Decimal("30"), Decimal("70")),
        position_size=Decimal("0.10"), risk_amount=Decimal("50"),
        risk_percentage=Decimal("0.5"), decision_timestamp=_dt(0),
        decision_expiry=_dt(0) + timedelta(seconds=5),
        originating_event_ids=("e1", "e2"), score=Decimal("78"),
        environment=Environment.BACKTEST, idempotency_key="d1",
    )
    defaults.update(overrides)
    return TradeDecision(**defaults)


class TestTradeDecision:
    def test_valid_decision(self):
        d = make_decision()
        assert d.direction == Direction.BULLISH

    def test_immutable_after_creation(self):
        d = make_decision()
        with pytest.raises(ValidationError):
            d.stop_loss = Decimal("2390")

    def test_expiry_before_timestamp_rejected(self):
        with pytest.raises(ValidationError):
            make_decision(decision_expiry=_dt(0), decision_timestamp=_dt(1))

    def test_tp_allocation_must_sum_to_100(self):
        with pytest.raises(ValidationError):
            make_decision(tp_allocation=(Decimal("30"), Decimal("60")))

    def test_bullish_sl_must_be_below_entry(self):
        with pytest.raises(ValidationError):
            make_decision(direction=Direction.BULLISH, stop_loss=Decimal("2405"))

    def test_bearish_sl_must_be_above_entry(self):
        with pytest.raises(ValidationError):
            make_decision(
                direction=Direction.BEARISH, stop_loss=Decimal("2395"),
                take_profit_1=Decimal("2395"), take_profit_2=Decimal("2385"),
            )

    def test_non_positive_position_size_rejected(self):
        with pytest.raises(ValidationError):
            make_decision(position_size=Decimal("0"))

    def test_is_expired_false_before_expiry(self):
        d = make_decision()
        assert d.is_expired(_dt(0)) is False

    def test_is_expired_true_after_expiry(self):
        d = make_decision()
        assert d.is_expired(_dt(0) + timedelta(seconds=10)) is True

    def test_is_expired_exactly_at_expiry_is_not_expired(self):
        # OD3: expiry is a hard boundary — exactly at expiry is still valid.
        d = make_decision()
        assert d.is_expired(d.decision_expiry) is False
