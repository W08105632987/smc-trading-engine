"""Execution domain objects — Execution FROZEN v1.0 + Implementation Spec
v1.0 §11. ORPHAN_ORDER/ORPHAN_POSITION/EXECUTION_ABORTED are modeled as a
distinct ExecutionExceptionState, never folded into OrderState/
ProtectionState and never treated as a plain reason code — one
unambiguous answer to "what kind of thing is an orphan position?"."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import field_validator, model_validator

from core.domain.common import Direction, UTCModel, ensure_utc


class OrderState(str, Enum):
    ORDER_PENDING_SUBMISSION = "ORDER_PENDING_SUBMISSION"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACKNOWLEDGED = "ORDER_ACKNOWLEDGED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_FILLED = "ORDER_FILLED"
    SUBMISSION_FAILED = "SUBMISSION_FAILED"
    BROKER_REJECTED = "BROKER_REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


TERMINAL_ORDER_STATES: frozenset[OrderState] = frozenset(
    {
        OrderState.ORDER_FILLED,
        OrderState.SUBMISSION_FAILED,
        OrderState.BROKER_REJECTED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
    }
)

VALID_ORDER_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.ORDER_PENDING_SUBMISSION: frozenset(
        {OrderState.ORDER_SUBMITTED, OrderState.SUBMISSION_FAILED}
    ),
    OrderState.ORDER_SUBMITTED: frozenset(
        {OrderState.ORDER_ACKNOWLEDGED, OrderState.BROKER_REJECTED, OrderState.EXPIRED}
    ),
    OrderState.ORDER_ACKNOWLEDGED: frozenset(
        {
            OrderState.ORDER_FILLED,
            OrderState.ORDER_PARTIALLY_FILLED,
            OrderState.BROKER_REJECTED,
        }
    ),
    OrderState.ORDER_PARTIALLY_FILLED: frozenset({OrderState.ORDER_FILLED, OrderState.CANCELLED}),
    OrderState.ORDER_FILLED: frozenset(),
    OrderState.SUBMISSION_FAILED: frozenset(),
    OrderState.BROKER_REJECTED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.EXPIRED: frozenset(),
}


class ProtectionState(str, Enum):
    """Execution FROZEN v1.0 (OD2 resolution): the updated protection
    lifecycle with PROTECTION_PENDING/EMERGENCY_CLOSE_REQUESTED closing
    off the ambiguity of an already-filled, not-yet-protected position."""

    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTED = "PROTECTED"
    ATTACH_FAILED = "ATTACH_FAILED"
    EMERGENCY_CLOSE_REQUESTED = "EMERGENCY_CLOSE_REQUESTED"
    CLOSED = "CLOSED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


VALID_PROTECTION_TRANSITIONS: dict[ProtectionState, frozenset[ProtectionState]] = {
    ProtectionState.PROTECTION_PENDING: frozenset(
        {ProtectionState.PROTECTED, ProtectionState.ATTACH_FAILED}
    ),
    ProtectionState.ATTACH_FAILED: frozenset(
        {ProtectionState.PROTECTED, ProtectionState.EMERGENCY_CLOSE_REQUESTED}
    ),
    ProtectionState.EMERGENCY_CLOSE_REQUESTED: frozenset(
        {ProtectionState.CLOSED, ProtectionState.RECONCILIATION_REQUIRED}
    ),
    ProtectionState.PROTECTED: frozenset(),
    ProtectionState.CLOSED: frozenset(),
    ProtectionState.RECONCILIATION_REQUIRED: frozenset(),
}


class PositionLifecycleState(str, Enum):
    POSITION_ACTIVE = "POSITION_ACTIVE"
    POSITION_PARTIALLY_CLOSED = "POSITION_PARTIALLY_CLOSED"
    POSITION_CLOSED = "POSITION_CLOSED"


class ExecutionOrder(UTCModel):
    order_id: str
    decision_id: str
    broker_ticket: Optional[str] = None
    state: OrderState
    requested_volume: Decimal
    filled_volume: Decimal = Decimal("0")
    requested_price: Decimal
    avg_fill_price: Optional[Decimal] = None
    submitted_at: datetime
    acknowledged_at: Optional[datetime] = None

    @field_validator("submitted_at", "acknowledged_at")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def _fill_never_exceeds_request(self) -> "ExecutionOrder":
        if self.filled_volume > self.requested_volume:
            raise ValueError("filled_volume cannot exceed requested_volume")
        return self


class Fill(UTCModel):
    fill_id: str
    order_id: str
    volume: Decimal
    price: Decimal
    timestamp: datetime
    is_partial: bool

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("volume")
    @classmethod
    def _positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("fill volume must be positive")
        return value


class Position(UTCModel):
    position_id: str
    decision_id: str
    symbol: str
    direction: Direction
    volume: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    protection_state: ProtectionState
    lifecycle_state: PositionLifecycleState
    opened_at: datetime
    closed_at: Optional[datetime] = None
    realized_r: Optional[Decimal] = None

    @field_validator("opened_at", "closed_at")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def _closed_consistency(self) -> "Position":
        if self.lifecycle_state == PositionLifecycleState.POSITION_CLOSED:
            if self.closed_at is None:
                raise ValueError("POSITION_CLOSED requires closed_at")
        else:
            if self.closed_at is not None:
                raise ValueError("closed_at must be null unless POSITION_CLOSED")
        return self


class ExecutionExceptionType(str, Enum):
    ORPHAN_ORDER = "ORPHAN_ORDER"
    ORPHAN_POSITION = "ORPHAN_POSITION"
    EXECUTION_ABORTED = "EXECUTION_ABORTED"


class ExceptionResolutionStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ExecutionExceptionState(UTCModel):
    """Implementation Spec v1.0 §11 fix: exactly one answer to "is this a
    state, an event, or an error code?" — it's this, a dedicated
    exception-classification record, and nothing else."""

    exception_id: str
    exception_type: ExecutionExceptionType
    related_order_id: Optional[str] = None
    related_position_id: Optional[str] = None
    detected_at: datetime
    detection_source: str
    resolution_status: ExceptionResolutionStatus
    resolved_at: Optional[datetime] = None
    resolution_action: Optional[str] = None

    @field_validator("detected_at", "resolved_at")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def _resolution_consistency(self) -> "ExecutionExceptionState":
        if self.resolution_status == ExceptionResolutionStatus.RESOLVED:
            if self.resolved_at is None or self.resolution_action is None:
                raise ValueError("RESOLVED requires resolved_at and resolution_action")
        return self


class ReconciliationResult(UTCModel):
    reconciliation_id: str
    timestamp: datetime
    mismatches: tuple[str, ...] = ()
    resolution_action: Optional[str] = None
    resolved: bool

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _resolved_requires_action(self) -> "ReconciliationResult":
        if self.resolved and self.mismatches and self.resolution_action is None:
            raise ValueError("resolved=True with mismatches requires resolution_action")
        return self
