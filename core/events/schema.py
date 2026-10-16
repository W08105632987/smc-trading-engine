"""Event-sourcing / audit contract. Implementation Spec v1.0 §13: every
state transition emits one of these. This is what makes 'what did the
bot know, when did it know it' answerable after the fact rather than
just asserted (Strategy FROZEN v1.0 §1)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import field_validator

from core.domain.common import Environment, UTCModel, ensure_utc


class EventType(str, Enum):
    # Strategy-side
    SETUP_CREATED = "SETUP_CREATED"
    TOUCH_EVENT = "TOUCH_EVENT"
    MICRO_SWING_CONFIRMED = "MICRO_SWING_CONFIRMED"
    MSS_CONFIRMED = "MSS_CONFIRMED"
    ZONE_CREATED = "ZONE_CREATED"
    SETUP_CONFIRMED = "SETUP_CONFIRMED"
    SETUP_DISPLACED = "SETUP_DISPLACED"
    SETUP_EXPIRED = "SETUP_EXPIRED"
    SETUP_INVALIDATED = "SETUP_INVALIDATED"
    WARNING_ENTERED = "WARNING_ENTERED"
    WARNING_CANCELLED = "WARNING_CANCELLED"
    REVERSAL_CONFIRMED = "REVERSAL_CONFIRMED"
    # Decision
    TRADE_APPROVED = "TRADE_APPROVED"
    TRADE_REJECTED = "TRADE_REJECTED"
    # Execution-side
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACKNOWLEDGED = "ORDER_ACKNOWLEDGED"
    ORDER_FILLED = "ORDER_FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTED = "PROTECTED"
    EMERGENCY_CLOSE_REQUESTED = "EMERGENCY_CLOSE_REQUESTED"
    TP1_EXECUTED = "TP1_EXECUTED"
    TRAILING_STOP_UPDATED = "TRAILING_STOP_UPDATED"
    POSITION_CLOSED = "POSITION_CLOSED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class ExecutionEvent(UTCModel):
    """Same schema shape used for every event above — one event stream,
    one schema, regardless of which subsystem emitted it."""

    event_id: str
    event_type: EventType
    timestamp: datetime
    entity_id: str
    previous_state: str | None
    new_state: str
    source: str
    environment: Environment
    config_hash: str
    payload: dict[str, Any] = {}

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
