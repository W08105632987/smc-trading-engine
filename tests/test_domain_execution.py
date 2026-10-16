from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.domain.common import Direction
from core.domain.execution import (
    ExceptionResolutionStatus,
    ExecutionExceptionState,
    ExecutionExceptionType,
    ExecutionOrder,
    Fill,
    OrderState,
    Position,
    PositionLifecycleState,
    ProtectionState,
    ReconciliationResult,
    TERMINAL_ORDER_STATES,
    VALID_ORDER_TRANSITIONS,
    VALID_PROTECTION_TRANSITIONS,
)


def _dt(minute: int) -> datetime:
    # timedelta-based so any minute offset (not just 0-59) is valid.
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


class TestExecutionOrder:
    def test_fill_exceeding_request_rejected(self):
        with pytest.raises(ValidationError):
            ExecutionOrder(
                order_id="o1", decision_id="d1", state=OrderState.ORDER_PARTIALLY_FILLED,
                requested_volume=Decimal("0.10"), filled_volume=Decimal("0.20"),
                requested_price=Decimal("2400"), submitted_at=_dt(0),
            )

    def test_valid_order(self):
        order = ExecutionOrder(
            order_id="o1", decision_id="d1", state=OrderState.ORDER_FILLED,
            requested_volume=Decimal("0.10"), filled_volume=Decimal("0.10"),
            requested_price=Decimal("2400"), submitted_at=_dt(0),
        )
        assert order.state == OrderState.ORDER_FILLED


class TestOrderStateTransitionTable:
    def test_terminal_states_have_no_outgoing_transitions(self):
        for state in TERMINAL_ORDER_STATES:
            assert VALID_ORDER_TRANSITIONS[state] == frozenset()

    def test_submitted_to_acknowledged_is_legal(self):
        assert OrderState.ORDER_ACKNOWLEDGED in VALID_ORDER_TRANSITIONS[OrderState.ORDER_SUBMITTED]

    def test_pending_submission_cannot_jump_to_filled(self):
        assert (
            OrderState.ORDER_FILLED
            not in VALID_ORDER_TRANSITIONS[OrderState.ORDER_PENDING_SUBMISSION]
        )


class TestProtectionStateTransitionTable:
    """Execution FROZEN v1.0 OD2: the resolved SL-attach-failure
    lifecycle — PROTECTION_PENDING -> ATTACH_FAILED ->
    EMERGENCY_CLOSE_REQUESTED -> {CLOSED | RECONCILIATION_REQUIRED}."""

    def test_pending_to_protected_is_legal(self):
        assert (
            ProtectionState.PROTECTED
            in VALID_PROTECTION_TRANSITIONS[ProtectionState.PROTECTION_PENDING]
        )

    def test_pending_cannot_jump_straight_to_emergency_close(self):
        assert (
            ProtectionState.EMERGENCY_CLOSE_REQUESTED
            not in VALID_PROTECTION_TRANSITIONS[ProtectionState.PROTECTION_PENDING]
        )

    def test_attach_failed_can_still_reach_protected(self):
        # A later retry succeeding is legal, not just escalation to emergency close.
        assert (
            ProtectionState.PROTECTED
            in VALID_PROTECTION_TRANSITIONS[ProtectionState.ATTACH_FAILED]
        )

    def test_emergency_close_never_silently_assumed_successful(self):
        # Execution FROZEN v1.0: "never assume the close itself succeeded."
        allowed = VALID_PROTECTION_TRANSITIONS[ProtectionState.EMERGENCY_CLOSE_REQUESTED]
        assert allowed == frozenset({ProtectionState.CLOSED, ProtectionState.RECONCILIATION_REQUIRED})


class TestPosition:
    def test_closed_requires_closed_at(self):
        with pytest.raises(ValidationError):
            Position(
                position_id="p1", decision_id="d1", symbol="XAUUSD",
                direction=Direction.BULLISH, volume=Decimal("0.1"),
                entry_price=Decimal("2400"), stop_loss=Decimal("2395"),
                take_profit_1=Decimal("2405"), take_profit_2=Decimal("2415"),
                protection_state=ProtectionState.PROTECTED,
                lifecycle_state=PositionLifecycleState.POSITION_CLOSED,
                opened_at=_dt(0),
            )

    def test_active_position_forbids_closed_at(self):
        with pytest.raises(ValidationError):
            Position(
                position_id="p1", decision_id="d1", symbol="XAUUSD",
                direction=Direction.BULLISH, volume=Decimal("0.1"),
                entry_price=Decimal("2400"), stop_loss=Decimal("2395"),
                take_profit_1=Decimal("2405"), take_profit_2=Decimal("2415"),
                protection_state=ProtectionState.PROTECTED,
                lifecycle_state=PositionLifecycleState.POSITION_ACTIVE,
                opened_at=_dt(0), closed_at=_dt(10),
            )


class TestFill:
    def test_non_positive_volume_rejected(self):
        with pytest.raises(ValidationError):
            Fill(fill_id="f1", order_id="o1", volume=Decimal("0"), price=Decimal("2400"),
                 timestamp=_dt(0), is_partial=False)


class TestExecutionExceptionState:
    def test_resolved_requires_resolution_details(self):
        with pytest.raises(ValidationError):
            ExecutionExceptionState(
                exception_id="x1", exception_type=ExecutionExceptionType.ORPHAN_POSITION,
                detected_at=_dt(0), detection_source="reconciliation",
                resolution_status=ExceptionResolutionStatus.RESOLVED,
            )

    def test_open_without_resolution_fields_ok(self):
        exc = ExecutionExceptionState(
            exception_id="x1", exception_type=ExecutionExceptionType.ORPHAN_POSITION,
            detected_at=_dt(0), detection_source="reconciliation",
            resolution_status=ExceptionResolutionStatus.OPEN,
        )
        assert exc.resolution_status == ExceptionResolutionStatus.OPEN


class TestReconciliationResult:
    def test_resolved_with_mismatches_requires_action(self):
        with pytest.raises(ValidationError):
            ReconciliationResult(
                reconciliation_id="r1", timestamp=_dt(0),
                mismatches=("quantity_mismatch",), resolved=True,
            )

    def test_resolved_with_no_mismatches_ok(self):
        result = ReconciliationResult(reconciliation_id="r1", timestamp=_dt(0), resolved=True)
        assert result.resolved is True
