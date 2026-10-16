from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.domain.execution import ExecutionOrder, OrderState
from execution.idempotency.idempotency_store import DuplicateDecisionError, IdempotencyStore


def make_order(decision_id: str, order_id: str, state=OrderState.ORDER_SUBMITTED) -> ExecutionOrder:
    return ExecutionOrder(
        order_id=order_id, decision_id=decision_id, state=state,
        requested_volume=Decimal("0.10"), filled_volume=Decimal("0"),
        requested_price=Decimal("2400"), submitted_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )


class TestIdempotencyStore:
    def test_get_existing_returns_none_for_unknown_decision(self):
        store = IdempotencyStore()
        assert store.get_existing("d1") is None

    def test_register_then_get_existing_returns_it(self):
        store = IdempotencyStore()
        order = make_order("d1", "o1")
        store.register("d1", order)
        assert store.get_existing("d1") == order

    def test_registering_identical_order_twice_is_a_no_op(self):
        store = IdempotencyStore()
        order = make_order("d1", "o1")
        store.register("d1", order)
        store.register("d1", order)  # must not raise
        assert store.get_existing("d1") == order

    def test_registering_a_different_order_for_the_same_decision_raises(self):
        # THE critical duplicate-submission guard.
        store = IdempotencyStore()
        order1 = make_order("d1", "o1")
        order2 = make_order("d1", "o2")  # different order_id, same decision_id
        store.register("d1", order1)
        with pytest.raises(DuplicateDecisionError):
            store.register("d1", order2)

    def test_update_progresses_state_without_raising(self):
        store = IdempotencyStore()
        order = make_order("d1", "o1", state=OrderState.ORDER_SUBMITTED)
        store.register("d1", order)
        filled = make_order("d1", "o1", state=OrderState.ORDER_FILLED)
        store.update("d1", filled)
        assert store.get_existing("d1").state == OrderState.ORDER_FILLED

    def test_update_on_unregistered_decision_raises(self):
        store = IdempotencyStore()
        with pytest.raises(KeyError):
            store.update("unknown", make_order("unknown", "o1"))

    def test_different_decisions_never_collide(self):
        store = IdempotencyStore()
        store.register("d1", make_order("d1", "o1"))
        store.register("d2", make_order("d2", "o2"))
        assert store.get_existing("d1").order_id == "o1"
        assert store.get_existing("d2").order_id == "o2"
