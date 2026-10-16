import pytest

from core.domain.execution import OrderState, ProtectionState
from core.state.machine import IllegalTransitionError
from execution.state_machine.tables import ORDER_STATE_MACHINE, PROTECTION_STATE_MACHINE


class TestOrderStateMachine:
    def test_pending_submission_to_submitted_is_legal(self):
        ORDER_STATE_MACHINE.validate(OrderState.ORDER_PENDING_SUBMISSION, OrderState.ORDER_SUBMITTED)

    def test_pending_submission_cannot_jump_to_filled(self):
        with pytest.raises(IllegalTransitionError):
            ORDER_STATE_MACHINE.validate(OrderState.ORDER_PENDING_SUBMISSION, OrderState.ORDER_FILLED)

    def test_terminal_states_have_no_outgoing_transitions(self):
        for state in (OrderState.ORDER_FILLED, OrderState.BROKER_REJECTED, OrderState.CANCELLED, OrderState.EXPIRED):
            assert ORDER_STATE_MACHINE.is_terminal(state)

    def test_filled_state_cannot_transition_anywhere(self):
        with pytest.raises(IllegalTransitionError):
            ORDER_STATE_MACHINE.validate(OrderState.ORDER_FILLED, OrderState.CANCELLED)

    def test_repeated_illegal_transition_always_fails_the_same_way(self):
        for _ in range(3):
            with pytest.raises(IllegalTransitionError):
                ORDER_STATE_MACHINE.validate(OrderState.ORDER_FILLED, OrderState.ORDER_SUBMITTED)


class TestProtectionStateMachine:
    def test_pending_to_protected_is_legal(self):
        PROTECTION_STATE_MACHINE.validate(ProtectionState.PROTECTION_PENDING, ProtectionState.PROTECTED)

    def test_pending_cannot_skip_straight_to_emergency_close(self):
        with pytest.raises(IllegalTransitionError):
            PROTECTION_STATE_MACHINE.validate(
                ProtectionState.PROTECTION_PENDING, ProtectionState.EMERGENCY_CLOSE_REQUESTED
            )

    def test_attach_failed_can_still_recover_to_protected(self):
        PROTECTION_STATE_MACHINE.validate(ProtectionState.ATTACH_FAILED, ProtectionState.PROTECTED)

    def test_emergency_close_never_silently_assumed_successful(self):
        allowed = PROTECTION_STATE_MACHINE.allowed_next(ProtectionState.EMERGENCY_CLOSE_REQUESTED)
        assert allowed == frozenset({ProtectionState.CLOSED, ProtectionState.RECONCILIATION_REQUIRED})

    def test_closed_and_reconciliation_required_are_terminal(self):
        assert PROTECTION_STATE_MACHINE.is_terminal(ProtectionState.CLOSED)
        assert PROTECTION_STATE_MACHINE.is_terminal(ProtectionState.RECONCILIATION_REQUIRED)
