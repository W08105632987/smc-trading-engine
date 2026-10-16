"""Execution state machines. These are deliberately thin — the actual
legal-transition tables (VALID_ORDER_TRANSITIONS, VALID_PROTECTION_TRANSITIONS)
were already frozen in core/domain/execution.py during Milestone 1. This
module wires them into the generic core/state/machine.py infrastructure
so the execution engine checks transitions the same way every other
state machine in this codebase does — no separate, competing validation
logic."""

from __future__ import annotations

from core.domain.execution import (
    OrderState,
    ProtectionState,
    VALID_ORDER_TRANSITIONS,
    VALID_PROTECTION_TRANSITIONS,
)
from core.state.machine import TransitionTable

ORDER_STATE_MACHINE: TransitionTable[OrderState] = TransitionTable(
    "OrderState", VALID_ORDER_TRANSITIONS
)

PROTECTION_STATE_MACHINE: TransitionTable[ProtectionState] = TransitionTable(
    "ProtectionState", VALID_PROTECTION_TRANSITIONS
)
