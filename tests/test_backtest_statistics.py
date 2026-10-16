from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction
from backtest.statistics import check_plausibility, compute_outlier_robustness, compute_statistics
from backtest.trade_log import TradeRecord


def _dt(minutes: int) -> datetime:
    return datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _trade(i: int, r, reason: str = "SL_HIT", closed: bool = True) -> TradeRecord:
    return TradeRecord(
        trade_id=f"t{i}", decision_id=f"d{i}", setup_id=f"s{i}", config_hash="h",
        direction=Direction.BULLISH, entry_timestamp=_dt(i * 10), entry_price=Decimal("100"),
        stop_loss=Decimal("99"), take_profit_1=Decimal("102"), take_profit_2=Decimal("104"),
        exit_timestamp=_dt(i * 10 + 5) if closed else None,
        exit_price=Decimal("101") if closed else None,
        exit_reason=reason if closed else "OPEN",
        realized_r=Decimal(str(r)) if closed else None,
    )


class TestKnownFixture:
    """Independently hand-verified before writing (see milestone chat
    log): the R-series [2,-1,-1,3,-2] gives expectancy=0.2, max_dd=2."""

    def test_expectancy_and_max_drawdown_match_hand_verified_fixture(self):
        rs = [2, -1, -1, 3, -2]
        trades = [_trade(i, r) for i, r in enumerate(rs)]
        stats = compute_statistics(trades)

        assert stats.max_drawdown_r == Decimal("2")
        assert abs(stats.expectancy_r - Decimal("0.2")) < Decimal("0.0001")
        assert stats.win_rate == Decimal("0.4")
        assert stats.profit_factor == Decimal("1.25")
        assert stats.longest_winning_streak == 1
        assert stats.longest_losing_streak == 2
        assert stats.net_r == Decimal("1")
        assert stats.winning_trades == 2
        assert stats.losing_trades == 3


def test_empty_trade_list_returns_zeroed_statistics_without_dividing_by_zero():
    stats = compute_statistics([])
    assert stats.total_trades == 0
    assert stats.win_rate == Decimal("0")
    assert stats.expectancy_r == Decimal("0")
    assert stats.profit_factor is None
    assert stats.max_drawdown_r == Decimal("0")


def test_open_trades_excluded_from_closed_trade_statistics():
    trades = [_trade(0, 2), _trade(1, None, closed=False)]
    stats = compute_statistics(trades)
    assert stats.total_trades == 2
    assert stats.closed_trades == 1
    assert stats.open_trades == 1
    assert stats.win_rate == Decimal("1")  # only the closed winner counts


def test_no_losses_gives_undefined_profit_factor_not_infinity():
    trades = [_trade(0, 2), _trade(1, 3)]
    stats = compute_statistics(trades)
    assert stats.profit_factor is None
    assert stats.average_loss_r is None


def test_exit_reason_counts_include_open_trades():
    trades = [_trade(0, 2, reason="TP2_HIT"), _trade(1, None, closed=False)]
    stats = compute_statistics(trades)
    assert stats.exit_reason_counts == {"TP2_HIT": 1, "OPEN": 1}


def test_drawdown_and_streaks_are_computed_in_exit_timestamp_order_not_input_order():
    # Same trades as the known fixture, but supplied OUT of chronological
    # order — compute_statistics must sort by exit_timestamp itself
    # rather than trusting input order, since drawdown is inherently
    # sequence-dependent.
    rs = [2, -1, -1, 3, -2]
    trades = [_trade(i, r) for i, r in enumerate(rs)]
    shuffled = [trades[3], trades[0], trades[4], trades[1], trades[2]]
    stats = compute_statistics(shuffled)
    assert stats.max_drawdown_r == Decimal("2")
    assert abs(stats.expectancy_r - Decimal("0.2")) < Decimal("0.0001")


def test_outlier_robustness_excludes_top_winners():
    # rs = [10, 5, -1, -1, 2] (winners: 10, 5, 2; losses: -1, -1)
    rs = [10, 5, -1, -1, 2]
    trades = [_trade(i, r) for i, r in enumerate(rs)]

    robustness = compute_outlier_robustness(trades, top_n_list=(1, 2))
    top_1 = robustness["top_1_excluded"]
    assert top_1.excluded_top_n == 1
    assert top_1.excluded_r_values == (Decimal("10"),)
    assert top_1.remaining_closed_trades == 4
    assert top_1.winning_trades == 2  # 5, 2
    assert top_1.losing_trades == 2   # -1, -1
    assert top_1.net_r == Decimal("5")  # 5 + 2 - 1 - 1 = 5
    assert top_1.win_rate == Decimal("0.5")

    top_2 = robustness["top_2_excluded"]
    assert top_2.excluded_top_n == 2
    assert top_2.excluded_r_values == (Decimal("10"), Decimal("5"))
    assert top_2.remaining_closed_trades == 3
    assert top_2.winning_trades == 1  # 2
    assert top_2.losing_trades == 2   # -1, -1
    assert top_2.net_r == Decimal("0")  # 2 - 1 - 1 = 0


class TestPlausibilityGuard:
    """TRAIL-01 (docs/DECISIONS.md): standing guard added after
    walk_forward_6m_v3 produced 11 trades with realized_r up to 614,
    all from a since-fixed trailing-stop bug. This check is an
    independent second layer -- it must flag an implausible trade
    regardless of what future mechanism might produce one, and must
    never flag a normal, legitimate trade."""

    def test_flags_trade_above_default_ceiling(self):
        trades = [_trade(0, 2), _trade(1, -1), _trade(2, 614)]
        warnings = check_plausibility(trades)
        assert len(warnings) == 1
        assert warnings[0].trade_id == "t2"
        assert warnings[0].realized_r == Decimal("614")
        assert "614" in warnings[0].reason

    def test_does_not_flag_normal_trades(self):
        rs = [2, -1, -1, 3, -2, 5.24, -1, 14.8, 8.4]
        trades = [_trade(i, r) for i, r in enumerate(rs)]
        assert check_plausibility(trades) == []

    def test_does_not_flag_open_trades(self):
        trades = [_trade(0, None, closed=False)]
        assert check_plausibility(trades) == []

    def test_respects_custom_ceiling(self):
        trades = [_trade(0, 15)]
        assert check_plausibility(trades, max_abs_r=Decimal("20")) == []
        assert len(check_plausibility(trades, max_abs_r=Decimal("10"))) == 1

    def test_flags_large_negative_r_too(self):
        # realized_r should never be more negative than -1 under normal
        # SL-capped risk, but the guard checks magnitude in both
        # directions deliberately -- it should never assume losses are
        # safe just because wins are the historically-observed failure
        # mode.
        trades = [_trade(0, -25)]
        warnings = check_plausibility(trades)
        assert len(warnings) == 1
        assert warnings[0].realized_r == Decimal("-25")
