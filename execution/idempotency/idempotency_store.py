"""Idempotency store. Execution FROZEN v1.0 §10: 'the same Trade
Decision cannot accidentally create multiple broker positions because of
retries, reconnects, timeouts, or process restarts.'

This is an in-memory implementation — real persistence is a later
milestone, same deliberate scoping as CandleStore in market_data/
(Milestone 2). The CONTRACT (check before submit, never blind-resubmit)
is what matters for this milestone; the storage backend is swappable
later without touching any caller."""

from __future__ import annotations

from core.domain.execution import ExecutionOrder


class DuplicateDecisionError(ValueError):
    """Raised when code attempts to register a SECOND, DIFFERENT order
    for a decision_id that's already registered. Registering the exact
    same order again is a harmless no-op (idempotent by definition);
    registering a different one for the same decision_id is a bug
    somewhere upstream, not something to silently overwrite."""


class IdempotencyStore:
    def __init__(self) -> None:
        self._by_decision_id: dict[str, ExecutionOrder] = {}

    def get_existing(self, decision_id: str) -> ExecutionOrder | None:
        """The mandatory check before ANY submission or resubmission —
        Execution FROZEN v1.0 §10: never blindly resubmit after an
        ambiguous response; always check for an existing order first."""
        return self._by_decision_id.get(decision_id)

    def register(self, decision_id: str, order: ExecutionOrder) -> None:
        existing = self._by_decision_id.get(decision_id)
        if existing is not None:
            if existing.order_id == order.order_id:
                return  # identical re-registration — harmless no-op
            raise DuplicateDecisionError(
                f"decision_id {decision_id} already has order "
                f"{existing.order_id}; refusing to register a second, "
                f"different order {order.order_id} for the same decision"
            )
        self._by_decision_id[decision_id] = order

    def update(self, decision_id: str, order: ExecutionOrder) -> None:
        """For updating an already-registered order's state (e.g.
        ORDER_SUBMITTED -> ORDER_FILLED) — distinct from register() so
        that legitimate state progression isn't confused with the
        duplicate-decision error case above."""
        if decision_id not in self._by_decision_id:
            raise KeyError(f"cannot update unregistered decision_id {decision_id}")
        self._by_decision_id[decision_id] = order
