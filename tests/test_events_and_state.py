from datetime import datetime, timedelta, timezone

import pytest

from core.domain.common import Environment
from core.events.schema import EventType, ExecutionEvent
from core.state.machine import IllegalTransitionError, TransitionTable


def _dt(minute: int) -> datetime:
    # timedelta-based so any minute offset (not just 0-59) is valid.
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


class TestExecutionEvent:
    def test_valid_event(self):
        ev = ExecutionEvent(
            event_id="ev1", event_type=EventType.SETUP_CREATED, timestamp=_dt(0),
            entity_id="s1", previous_state=None, new_state="WAITING_FOR_RETEST",
            source="setup_engine", environment=Environment.BACKTEST,
            config_hash="abc123",
        )
        assert ev.previous_state is None
        assert ev.payload == {}


class TestTransitionTable:
    def test_valid_transition_passes(self):
        table = TransitionTable("test", {"A": frozenset({"B"}), "B": frozenset()})
        table.validate("A", "B")  # must not raise

    def test_invalid_transition_raises(self):
        table = TransitionTable("test", {"A": frozenset({"B"}), "B": frozenset()})
        with pytest.raises(IllegalTransitionError):
            table.validate("A", "C")

    def test_terminal_state_detection(self):
        table = TransitionTable("test", {"A": frozenset({"B"}), "B": frozenset()})
        assert table.is_terminal("B") is True
        assert table.is_terminal("A") is False

    def test_can_transition_does_not_raise(self):
        table = TransitionTable("test", {"A": frozenset({"B"}), "B": frozenset()})
        assert table.can_transition("A", "B") is True
        assert table.can_transition("A", "C") is False

    def test_error_message_contains_states_and_machine_name(self):
        table = TransitionTable("OrderState", {"A": frozenset(), "B": frozenset()})
        with pytest.raises(IllegalTransitionError) as exc_info:
            table.validate("A", "B")
        assert "OrderState" in str(exc_info.value)
