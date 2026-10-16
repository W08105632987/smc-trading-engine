from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.common import Direction
from backtest.trade_log import TradeRecord
from backtest.validation.walk_forward import partition_chronologically


def _dt(days: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)


def _trade(i: int, day: int) -> TradeRecord:
    return TradeRecord(
        trade_id=f"t{i}", decision_id=f"d{i}", setup_id=f"s{i}", config_hash="h",
        direction=Direction.BULLISH, entry_timestamp=_dt(day), entry_price=Decimal("100"),
        stop_loss=Decimal("99"), take_profit_1=Decimal("102"), take_profit_2=Decimal("104"),
        exit_timestamp=_dt(day) + timedelta(hours=1), exit_price=Decimal("101"),
        exit_reason="SL_HIT", realized_r=Decimal("1"),
    )


def _trades(days):
    return [_trade(i, day) for i, day in enumerate(days)]


class TestBoundarySplit:
    """Independently hand-verified before writing: days
    [1,5,10,15,20,25,30] split at (12, 22) gives development=[1,5,10],
    walk_forward=[15,20], holdout=[25,30]."""

    def test_trades_fall_into_the_correct_bucket_by_entry_timestamp(self):
        trades = _trades([1, 5, 10, 15, 20, 25, 30])
        result = partition_chronologically(trades, development_end=_dt(12), walk_forward_end=_dt(22))

        assert [t.trade_id for t in result.development] == ["t0", "t1", "t2"]
        assert [t.trade_id for t in result.walk_forward] == ["t3", "t4"]
        assert [t.trade_id for t in result.holdout] == ["t5", "t6"]

    def test_boundary_exactly_on_development_end_falls_into_walk_forward(self):
        # `< development_end` is the rule -- a trade AT exactly
        # development_end belongs to the NEXT period, not the one ending.
        trades = _trades([5, 12, 20])
        result = partition_chronologically(trades, development_end=_dt(12), walk_forward_end=_dt(22))
        assert [t.trade_id for t in result.development] == ["t0"]
        assert [t.trade_id for t in result.walk_forward] == ["t1", "t2"]


def test_never_shuffles_raises_on_out_of_order_input():
    unsorted = _trades([10, 5])
    with pytest.raises(ValueError, match="sorted by entry_timestamp"):
        partition_chronologically(unsorted, development_end=_dt(12), walk_forward_end=_dt(22))


def test_walk_forward_end_must_be_after_development_end():
    trades = _trades([1, 2])
    with pytest.raises(ValueError, match="strictly after"):
        partition_chronologically(trades, development_end=_dt(20), walk_forward_end=_dt(10))


def test_empty_trade_list_returns_three_empty_partitions():
    result = partition_chronologically([], development_end=_dt(12), walk_forward_end=_dt(22))
    assert result.development == ()
    assert result.walk_forward == ()
    assert result.holdout == ()


def test_all_trades_before_development_end_leaves_other_partitions_empty():
    trades = _trades([1, 2, 3])
    result = partition_chronologically(trades, development_end=_dt(100), walk_forward_end=_dt(200))
    assert len(result.development) == 3
    assert result.walk_forward == ()
    assert result.holdout == ()
