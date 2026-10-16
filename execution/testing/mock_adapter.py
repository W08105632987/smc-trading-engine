"""Mock ExecutionAdapter. Per the Milestone 6 authorization's explicit
clarification: this is a deterministic TEST DOUBLE for exercising the
execution state machines — NOT a paper-trading simulator. It does not
inherit Phase 3 cost-stress parameters, does not simulate 'realistic'
broker behavior, and introduces no randomness. Every scenario it can
produce is explicitly scripted by the test that uses it. If the frozen
documents don't define a behavior, this adapter doesn't invent one —
it just does exactly what the test tells it to."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from core.domain.decision import TradeDecision
from core.domain.execution import ExecutionOrder, OrderState, Position, ReconciliationResult


class ScriptedResponse(str, Enum):
    """Every response type the mock can be told to produce for a given
    decision_id. Nothing here is inferred or randomized."""

    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"  # simulates a timeout/no-confirmed-response scenario
    PARTIAL_FILL = "PARTIAL_FILL"


@dataclass
class MockExecutionAdapter:
    """Implements core.contracts.execution.ExecutionAdapter. All
    behavior is pre-scripted per decision_id via script_response() —
    calling submit_market_order() for an unscripted decision_id raises,
    rather than falling back to a guessed default behavior.

    Timestamps come from an explicit, test-controlled mock clock
    (set_mock_time / advance_mock_time) — NEVER from datetime.now().
    Wall-clock-dependent test outcomes are exactly what the Milestone 6
    authorization prohibits ('no wall-clock-dependent test outcomes')."""

    _scripts: dict[str, ScriptedResponse] = field(default_factory=dict)
    _partial_fill_ratio: dict[str, Decimal] = field(default_factory=dict)
    _orders: dict[str, ExecutionOrder] = field(default_factory=dict)
    _positions: dict[str, Position] = field(default_factory=dict)
    _submission_count: dict[str, int] = field(default_factory=dict)
    _connected: bool = True
    _mock_time: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))

    def set_mock_time(self, at: datetime) -> None:
        self._mock_time = at

    def advance_mock_time(self, by: timedelta) -> None:
        self._mock_time = self._mock_time + by

    def script_response(
        self, decision_id: str, response: ScriptedResponse, partial_fill_ratio: Optional[Decimal] = None
    ) -> None:
        self._scripts[decision_id] = response
        if response == ScriptedResponse.PARTIAL_FILL:
            if partial_fill_ratio is None:
                raise ValueError("PARTIAL_FILL requires an explicit partial_fill_ratio")
            self._partial_fill_ratio[decision_id] = partial_fill_ratio

    def set_connection_status(self, connected: bool) -> None:
        self._connected = connected

    def submission_count(self, decision_id: str) -> int:
        """Test-observability helper: how many times submit_market_order
        was actually called for this decision_id — this is how
        duplicate-submission tests verify a second call never happened."""
        return self._submission_count.get(decision_id, 0)

    def submit_market_order(self, decision: TradeDecision) -> ExecutionOrder:
        decision_id = decision.decision_id
        self._submission_count[decision_id] = self._submission_count.get(decision_id, 0) + 1

        if decision_id not in self._scripts:
            raise ValueError(
                f"no scripted response for decision_id {decision_id} — "
                f"this mock never guesses; script a response first"
            )

        response = self._scripts[decision_id]
        order_id = f"order:{decision_id}"

        if response == ScriptedResponse.REJECTED:
            order = ExecutionOrder(
                order_id=order_id, decision_id=decision_id, state=OrderState.BROKER_REJECTED,
                requested_volume=decision.position_size, filled_volume=Decimal("0"),
                requested_price=decision.intended_entry_price, submitted_at=decision.decision_timestamp,
            )
        elif response == ScriptedResponse.AMBIGUOUS:
            # Simulates a submission with no confirmed outcome — state
            # stays ORDER_SUBMITTED, never auto-progresses.
            order = ExecutionOrder(
                order_id=order_id, decision_id=decision_id, state=OrderState.ORDER_SUBMITTED,
                requested_volume=decision.position_size, filled_volume=Decimal("0"),
                requested_price=decision.intended_entry_price, submitted_at=decision.decision_timestamp,
            )
        elif response == ScriptedResponse.PARTIAL_FILL:
            ratio = self._partial_fill_ratio[decision_id]
            filled = decision.position_size * ratio
            order = ExecutionOrder(
                order_id=order_id, decision_id=decision_id, state=OrderState.ORDER_PARTIALLY_FILLED,
                requested_volume=decision.position_size, filled_volume=filled,
                requested_price=decision.intended_entry_price, avg_fill_price=decision.intended_entry_price,
                submitted_at=decision.decision_timestamp, acknowledged_at=decision.decision_timestamp,
            )
        else:  # SUCCESS
            order = ExecutionOrder(
                order_id=order_id, decision_id=decision_id, state=OrderState.ORDER_FILLED,
                requested_volume=decision.position_size, filled_volume=decision.position_size,
                requested_price=decision.intended_entry_price, avg_fill_price=decision.intended_entry_price,
                submitted_at=decision.decision_timestamp, acknowledged_at=decision.decision_timestamp,
            )

        self._orders[order_id] = order
        return order

    def modify_position(
        self, position_id: str, sl: Optional[Decimal] = None, tp: Optional[Decimal] = None
    ) -> ReconciliationResult:
        # Scripted per position_id using the same script table, keyed
        # by a "modify:" prefix so submit and modify scripts don't collide.
        response = self._scripts.get(f"modify:{position_id}")
        if response is None:
            raise ValueError(f"no scripted response for modify:{position_id}")
        resolved = response == ScriptedResponse.SUCCESS
        return ReconciliationResult(
            reconciliation_id=f"recon:modify:{position_id}",
            timestamp=self._mock_time,
            mismatches=() if resolved else (f"modify_failed:{position_id}",),
            resolution_action="modified" if resolved else None,
            resolved=resolved,
        )

    def close_position(self, position_id: str, volume: Optional[Decimal] = None) -> ExecutionOrder:
        response = self._scripts.get(f"close:{position_id}")
        if response is None:
            raise ValueError(f"no scripted response for close:{position_id}")
        state = OrderState.ORDER_FILLED if response == ScriptedResponse.SUCCESS else OrderState.BROKER_REJECTED
        return ExecutionOrder(
            order_id=f"close:{position_id}", decision_id=position_id, state=state,
            requested_volume=volume or Decimal("0"), filled_volume=volume or Decimal("0") if state == OrderState.ORDER_FILLED else Decimal("0"),
            requested_price=Decimal("0"), submitted_at=self._mock_time,
        )

    def get_open_positions(self) -> tuple[Position, ...]:
        return tuple(self._positions.values())

    def set_broker_position(self, position: Position) -> None:
        """Test setup helper — lets a test declare 'the broker reports
        this position exists' independent of anything submitted through
        submit_market_order(), which is exactly what's needed to
        construct orphan-position/reconciliation-mismatch scenarios."""
        self._positions[position.position_id] = position

    def reconcile(self) -> ReconciliationResult:
        return ReconciliationResult(
            reconciliation_id="recon:heartbeat", timestamp=self._mock_time,
            mismatches=(), resolution_action=None, resolved=True,
        )

    def get_connection_status(self) -> bool:
        return self._connected
