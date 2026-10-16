"""Execution-side Protocols. Module boundary rule (Technical Architecture
v1.0): execution/ consumes TradeDecision as an opaque contract; nothing
here imports strategy/ internals."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Protocol

from core.domain.decision import TradeDecision
from core.domain.execution import ExecutionOrder, Position, ReconciliationResult


class ExecutionAdapter(Protocol):
    """The MT5 adapter — and only the MT5 adapter — implements this
    against the real MetaTrader5 package. Backtest/paper implement it
    against a simulator. Same interface, same TradeDecision contract,
    same event schema either way (Execution FROZEN v1.0 §17)."""

    def submit_market_order(self, decision: TradeDecision) -> ExecutionOrder: ...

    def modify_position(
        self,
        position_id: str,
        sl: Optional[Decimal] = None,
        tp: Optional[Decimal] = None,
    ) -> ReconciliationResult: ...

    def close_position(self, position_id: str, volume: Optional[Decimal] = None) -> ExecutionOrder: ...

    def get_open_positions(self) -> tuple[Position, ...]: ...

    def reconcile(self) -> ReconciliationResult: ...

    def get_connection_status(self) -> bool: ...
