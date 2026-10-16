from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.common import Direction, Environment
from core.domain.decision import EntryType, TradeDecision
from core.domain.execution import OrderState
from execution.orders.order_validator import OrderValidator
from execution.testing.mock_adapter import MockExecutionAdapter, ScriptedResponse


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_decision(decision_id="d1", expiry_seconds=5) -> TradeDecision:
    return TradeDecision(
        decision_id=decision_id, strategy_version="v1", config_hash="h1", setup_id="s1",
        symbol="XAUUSD", direction=Direction.BULLISH, entry_type=EntryType.MARKET,
        intended_entry_price=Decimal("2400"), execution_price_policy=Decimal("0.10"),
        stop_loss=Decimal("2395"), take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
        tp_allocation=(Decimal("30"), Decimal("70")), position_size=Decimal("0.10"),
        risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
        decision_timestamp=_dt(0), decision_expiry=_dt(0) + timedelta(seconds=expiry_seconds),
        originating_event_ids=("s1",), score=Decimal("78"), environment=Environment.BACKTEST,
        idempotency_key=decision_id,
    )


class TestMockAdapterDeterminism:
    def test_unscripted_decision_raises_rather_than_guessing(self):
        adapter = MockExecutionAdapter()
        decision = make_decision()
        with pytest.raises(ValueError, match="never guesses"):
            adapter.submit_market_order(decision)

    def test_scripted_success_produces_filled_order(self):
        adapter = MockExecutionAdapter()
        decision = make_decision()
        adapter.script_response(decision.decision_id, ScriptedResponse.SUCCESS)
        order = adapter.submit_market_order(decision)
        assert order.state == OrderState.ORDER_FILLED
        assert order.filled_volume == decision.position_size

    def test_scripted_rejection_produces_rejected_order(self):
        adapter = MockExecutionAdapter()
        decision = make_decision()
        adapter.script_response(decision.decision_id, ScriptedResponse.REJECTED)
        order = adapter.submit_market_order(decision)
        assert order.state == OrderState.BROKER_REJECTED

    def test_scripted_ambiguous_stays_in_submitted_state(self):
        # Simulates a timeout — never auto-resolves to filled or rejected.
        adapter = MockExecutionAdapter()
        decision = make_decision()
        adapter.script_response(decision.decision_id, ScriptedResponse.AMBIGUOUS)
        order = adapter.submit_market_order(decision)
        assert order.state == OrderState.ORDER_SUBMITTED

    def test_scripted_partial_fill_produces_correct_filled_volume(self):
        adapter = MockExecutionAdapter()
        decision = make_decision()
        adapter.script_response(
            decision.decision_id, ScriptedResponse.PARTIAL_FILL, partial_fill_ratio=Decimal("0.6")
        )
        order = adapter.submit_market_order(decision)
        assert order.state == OrderState.ORDER_PARTIALLY_FILLED
        assert order.filled_volume == decision.position_size * Decimal("0.6")

    def test_partial_fill_without_ratio_raises(self):
        adapter = MockExecutionAdapter()
        with pytest.raises(ValueError, match="partial_fill_ratio"):
            adapter.script_response("d1", ScriptedResponse.PARTIAL_FILL)

    def test_mock_time_is_fully_controlled_never_wall_clock(self):
        adapter = MockExecutionAdapter()
        fixed_time = _dt(500)
        adapter.set_mock_time(fixed_time)
        adapter.script_response("close:p1", ScriptedResponse.SUCCESS)
        order = adapter.close_position("p1")
        assert order.submitted_at == fixed_time  # not datetime.now()

    def test_submission_count_tracks_actual_calls(self):
        adapter = MockExecutionAdapter()
        decision = make_decision()
        adapter.script_response(decision.decision_id, ScriptedResponse.SUCCESS)
        assert adapter.submission_count(decision.decision_id) == 0
        adapter.submit_market_order(decision)
        assert adapter.submission_count(decision.decision_id) == 1
        adapter.submit_market_order(decision)  # calling again (bad practice) still just counts
        assert adapter.submission_count(decision.decision_id) == 2


class TestOrderValidator:
    def test_valid_decision_passes(self):
        validator = OrderValidator()
        decision = make_decision(expiry_seconds=5)
        result = validator.validate(decision, at=_dt(0))
        assert result.valid is True

    def test_expired_decision_rejected(self):
        validator = OrderValidator()
        decision = make_decision(expiry_seconds=5)
        result = validator.validate(decision, at=_dt(1))  # 60s later, well past 5s expiry
        assert result.valid is False
        assert result.reason_code == "DECISION_EXPIRED"

    def test_exactly_at_expiry_boundary_is_still_valid(self):
        validator = OrderValidator()
        decision = make_decision(expiry_seconds=5)
        result = validator.validate(decision, at=decision.decision_expiry)
        assert result.valid is True
