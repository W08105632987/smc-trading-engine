"""Protection engine. Execution FROZEN v1.0 (OD2, locked): attempt SL
attachment, retry up to 2 additional times, verify after each attempt.
If still unprotected, submit an emergency close — but NEVER assume the
close itself succeeded just because it was requested. The position
stays under active reconciliation until broker-CONFIRMED closure.

Every transition is validated against PROTECTION_STATE_MACHINE before
being returned — an engine bug that tried to skip a state or invent an
illegal transition would raise here, not silently succeed."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from core.domain.execution import ExecutionOrder, OrderState, ProtectionState, ReconciliationResult
from execution.state_machine.tables import PROTECTION_STATE_MACHINE


class ProtectionAdapter(Protocol):
    """The subset of ExecutionAdapter this engine actually needs —
    named separately so tests can supply a narrower fake if useful,
    without pulling in the full ExecutionAdapter surface."""

    def modify_position(self, position_id: str, sl: Decimal | None = None, tp: Decimal | None = None) -> ReconciliationResult: ...
    def close_position(self, position_id: str, volume: Decimal | None = None) -> ExecutionOrder: ...


@dataclass
class ProtectionAttemptLog:
    """Every attempt this engine made, in order — the audit trail proof
    that 'never assume emergency close succeeded' was actually honored,
    not just claimed."""

    attempts: list[str] = field(default_factory=list)

    def record(self, event: str) -> None:
        self.attempts.append(event)


class ProtectionEngine:
    def __init__(self, adapter: ProtectionAdapter, max_retries: int = 2) -> None:
        self._adapter = adapter
        self._max_retries = max_retries

    def attach_protection(
        self, position_id: str, sl: Decimal, tp: Decimal
    ) -> tuple[ProtectionState, ProtectionAttemptLog]:
        log = ProtectionAttemptLog()
        state = ProtectionState.PROTECTION_PENDING
        log.record(f"START:{state.value}")

        attempt = 0
        while attempt <= self._max_retries:
            result = self._adapter.modify_position(position_id, sl=sl, tp=tp)
            attempt += 1
            if result.resolved:
                PROTECTION_STATE_MACHINE.validate(state, ProtectionState.PROTECTED)
                state = ProtectionState.PROTECTED
                log.record(f"ATTACH_SUCCEEDED:attempt_{attempt}")
                return state, log

            log.record(f"ATTACH_FAILED:attempt_{attempt}")
            if state == ProtectionState.PROTECTION_PENDING:
                PROTECTION_STATE_MACHINE.validate(state, ProtectionState.ATTACH_FAILED)
                state = ProtectionState.ATTACH_FAILED

        # Retries exhausted — escalate to emergency close. NEVER assume
        # it succeeded just because it was requested.
        PROTECTION_STATE_MACHINE.validate(state, ProtectionState.EMERGENCY_CLOSE_REQUESTED)
        state = ProtectionState.EMERGENCY_CLOSE_REQUESTED
        log.record("EMERGENCY_CLOSE_REQUESTED")

        close_order = self._adapter.close_position(position_id)

        if close_order.state == OrderState.ORDER_FILLED:
            PROTECTION_STATE_MACHINE.validate(state, ProtectionState.CLOSED)
            state = ProtectionState.CLOSED
            log.record("EMERGENCY_CLOSE_CONFIRMED")
        else:
            # Broker did NOT confirm the close — this is exactly the
            # scenario the locked rule exists for. Never treat an
            # unconfirmed close as success.
            PROTECTION_STATE_MACHINE.validate(state, ProtectionState.RECONCILIATION_REQUIRED)
            state = ProtectionState.RECONCILIATION_REQUIRED
            log.record("EMERGENCY_CLOSE_UNCONFIRMED")

        return state, log
