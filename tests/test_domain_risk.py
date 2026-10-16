from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.domain.risk import PositionSizingResult, RiskDecision, RiskSnapshot


def _dt(minute: int) -> datetime:
    # timedelta-based so any minute offset (not just 0-59) is valid.
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


class TestRiskSnapshot:
    def test_daily_loss_uses_mark_to_market_equity(self):
        # Implementation Spec v1.0 §9 LOCKED decision: account_equity is
        # mark-to-market (includes floating P&L), and daily_loss is
        # computed directly off it — no separate floating-P&L field to
        # add in, because it's already baked into account_equity.
        snap = RiskSnapshot(
            snapshot_id="r1", timestamp=_dt(0),
            account_equity=Decimal("9800"),  # includes floating loss on open position
            daily_starting_equity=Decimal("10000"),
            daily_realized_pnl=Decimal("-50"),
            consecutive_losses_count=1, trades_today_count=1,
        )
        assert snap.daily_loss() == Decimal("200")

    def test_negative_count_rejected(self):
        with pytest.raises(ValidationError):
            RiskSnapshot(
                snapshot_id="r1", timestamp=_dt(0), account_equity=Decimal("10000"),
                daily_starting_equity=Decimal("10000"), daily_realized_pnl=Decimal("0"),
                consecutive_losses_count=-1, trades_today_count=0,
            )


class TestPositionSizingResult:
    def test_valid_sizing(self):
        result = PositionSizingResult(
            entry=Decimal("2400"), stop=Decimal("2395"), tick_size=Decimal("0.01"),
            tick_value=Decimal("1"), volume_step=Decimal("0.01"),
            min_volume=Decimal("0.01"), max_volume=Decimal("100"),
            computed_volume=Decimal("0.106"), rounded_volume=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage_actual=Decimal("0.5"),
            approved=True, reason_code="OK",
        )
        assert result.approved is True

    def test_rounding_that_increases_risk_is_rejected(self):
        # Execution FROZEN v1.0 §13: broker rounding must NEVER silently
        # increase approved risk.
        with pytest.raises(ValidationError, match="never silently increase"):
            PositionSizingResult(
                entry=Decimal("2400"), stop=Decimal("2395"), tick_size=Decimal("0.01"),
                tick_value=Decimal("1"), volume_step=Decimal("0.01"),
                min_volume=Decimal("0.01"), max_volume=Decimal("100"),
                computed_volume=Decimal("0.100"), rounded_volume=Decimal("0.11"),
                risk_amount=Decimal("55"), risk_percentage_actual=Decimal("0.55"),
                approved=True, reason_code="OK",
            )


class TestRiskDecision:
    def test_approved_requires_all_gates(self):
        with pytest.raises(ValidationError, match="requires every individual gate"):
            RiskDecision(
                daily_loss_limit_ok=True, consecutive_loss_ok=True,
                trade_count_ok=False, sizing_ok=True,
                approved=True, reason_code="OK",
            )

    def test_rejected_with_failing_gate_ok(self):
        decision = RiskDecision(
            daily_loss_limit_ok=True, consecutive_loss_ok=True,
            trade_count_ok=False, sizing_ok=True,
            approved=False, reason_code="DAILY_TRADE_LIMIT",
        )
        assert decision.approved is False
