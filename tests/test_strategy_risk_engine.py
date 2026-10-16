from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.risk import RiskSnapshot
from strategy.risk.risk_engine import RiskEngineImpl


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_engine(**overrides) -> RiskEngineImpl:
    defaults = dict(
        risk_per_trade_pct=Decimal("0.5"), daily_loss_limit_pct=Decimal("2.0"),
        max_consecutive_losses=3, max_trades_per_day=3,
    )
    defaults.update(overrides)
    return RiskEngineImpl(**defaults)


class TestPositionSizing:
    def test_standard_sizing_matches_hand_verified_math(self):
        engine = make_engine()
        result = engine.compute_position_size(
            account_equity=Decimal("10000"), entry=Decimal("2400"), stop=Decimal("2395"),
            tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
            volume_step=Decimal("0.01"), min_volume=Decimal("0.01"), max_volume=Decimal("100"),
        )
        assert result.approved is True
        assert result.rounded_volume == Decimal("0.10")
        assert result.risk_amount == Decimal("50.00")

    def test_rounding_never_exceeds_computed_volume(self):
        engine = make_engine(risk_per_trade_pct=Decimal("0.53"))  # produces an uneven division
        result = engine.compute_position_size(
            account_equity=Decimal("10000"), entry=Decimal("2400"), stop=Decimal("2395"),
            tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
            volume_step=Decimal("0.01"), min_volume=Decimal("0.01"), max_volume=Decimal("100"),
        )
        assert result.rounded_volume <= result.computed_volume

    def test_zero_stop_distance_rejected(self):
        engine = make_engine()
        result = engine.compute_position_size(
            account_equity=Decimal("10000"), entry=Decimal("2400"), stop=Decimal("2400"),  # same price
            tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
            volume_step=Decimal("0.01"), min_volume=Decimal("0.01"), max_volume=Decimal("100"),
        )
        assert result.approved is False
        assert result.reason_code == "STOP_TOO_TIGHT"

    def test_volume_below_minimum_rejected(self):
        engine = make_engine(risk_per_trade_pct=Decimal("0.001"))  # tiny risk -> tiny volume
        result = engine.compute_position_size(
            account_equity=Decimal("10000"), entry=Decimal("2400"), stop=Decimal("2395"),
            tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
            volume_step=Decimal("0.01"), min_volume=Decimal("0.01"), max_volume=Decimal("100"),
        )
        assert result.approved is False
        assert result.reason_code == "LOT_BELOW_MINIMUM"

    def test_volume_clipped_to_maximum(self):
        engine = make_engine(risk_per_trade_pct=Decimal("50"))  # huge risk -> huge volume
        result = engine.compute_position_size(
            account_equity=Decimal("1000000"), entry=Decimal("2400"), stop=Decimal("2399.99"),  # tiny stop distance
            tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
            volume_step=Decimal("0.01"), min_volume=Decimal("0.01"), max_volume=Decimal("10"),
        )
        assert result.rounded_volume == Decimal("10")


class TestRiskGates:
    def make_snapshot(self, **overrides) -> RiskSnapshot:
        defaults = dict(
            snapshot_id="r1", timestamp=_dt(0), account_equity=Decimal("10000"),
            daily_starting_equity=Decimal("10000"), daily_realized_pnl=Decimal("0"),
            consecutive_losses_count=0, trades_today_count=0,
        )
        defaults.update(overrides)
        return RiskSnapshot(**defaults)

    def make_approved_sizing(self):
        engine = make_engine()
        return engine.compute_position_size(
            account_equity=Decimal("10000"), entry=Decimal("2400"), stop=Decimal("2395"),
            tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
            volume_step=Decimal("0.01"), min_volume=Decimal("0.01"), max_volume=Decimal("100"),
        )

    def test_all_gates_pass(self):
        engine = make_engine()
        snapshot = self.make_snapshot()
        sizing = self.make_approved_sizing()
        decision = engine.evaluate_gates(snapshot, sizing)
        assert decision.approved is True

    def test_daily_loss_limit_breached_blocks(self):
        engine = make_engine(daily_loss_limit_pct=Decimal("2.0"))
        # 2% of 10000 = 200; equity dropped to 9700 -> loss of 300 > 200
        snapshot = self.make_snapshot(account_equity=Decimal("9700"))
        sizing = self.make_approved_sizing()
        decision = engine.evaluate_gates(snapshot, sizing)
        assert decision.approved is False
        assert decision.reason_code == "DAILY_LOSS_LIMIT"

    def test_daily_loss_exactly_at_limit_still_blocks(self):
        # Strict less-than: exactly at the limit is NOT ok (matches the
        # RiskSnapshot LOCKED basis — the threshold is a hard boundary).
        engine = make_engine(daily_loss_limit_pct=Decimal("2.0"))
        snapshot = self.make_snapshot(account_equity=Decimal("9800"))  # loss of exactly 200 = 2%
        sizing = self.make_approved_sizing()
        decision = engine.evaluate_gates(snapshot, sizing)
        assert decision.approved is False

    def test_consecutive_loss_lock_blocks(self):
        engine = make_engine(max_consecutive_losses=3)
        snapshot = self.make_snapshot(consecutive_losses_count=3)
        sizing = self.make_approved_sizing()
        decision = engine.evaluate_gates(snapshot, sizing)
        assert decision.approved is False
        assert decision.reason_code == "CONSECUTIVE_LOSS_LOCK"

    def test_daily_trade_limit_blocks(self):
        engine = make_engine(max_trades_per_day=3)
        snapshot = self.make_snapshot(trades_today_count=3)
        sizing = self.make_approved_sizing()
        decision = engine.evaluate_gates(snapshot, sizing)
        assert decision.approved is False
        assert decision.reason_code == "DAILY_TRADE_LIMIT"

    def test_unapproved_sizing_blocks(self):
        engine = make_engine()
        snapshot = self.make_snapshot()
        rejected_sizing = engine.compute_position_size(
            account_equity=Decimal("10000"), entry=Decimal("2400"), stop=Decimal("2400"),  # zero distance
            tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
            volume_step=Decimal("0.01"), min_volume=Decimal("0.01"), max_volume=Decimal("100"),
        )
        decision = engine.evaluate_gates(snapshot, rejected_sizing)
        assert decision.approved is False
        assert decision.reason_code == "STOP_TOO_TIGHT"
