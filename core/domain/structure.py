"""Structural domain objects — HTF swings, BOS/CHoCH, H4 WARNING machine.

These encode Strategy FROZEN v1.0 §2 and v4 §C exactly: 2-left/2-right
fractal = the ONLY definition used here (MicroSwing in setups.py is a
deliberately different, lighter concept — see that module's docstring for
why they must never be conflated, per v5 I9).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import field_validator, model_validator

from core.domain.common import Direction, Timeframe, UTCModel, ensure_utc


class SwingType(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class SwingPoint(UTCModel):
    """The HTF 'major swing' fractal: high[i] > high[i-2..i+2] (inverse for
    lows). `confirmation_timestamp` is deliberately later than
    `formation_timestamp` — the swing is not knowable until its right-side
    candles (i+1, i+2) have closed. Nothing in this engine may read a
    SwingPoint before `confirmation_timestamp` (Strategy FROZEN v1.0,
    Non-Negotiable Invariants: no look-ahead)."""

    swing_id: str
    symbol: str
    timeframe: Timeframe
    type: SwingType
    price: Decimal
    pivot_candle_id: str
    formation_timestamp: datetime
    confirmation_timestamp: datetime
    major_flag: bool = False
    major_confirmed_at: Optional[datetime] = None
    invalidated_at: Optional[datetime] = None

    @field_validator(
        "formation_timestamp",
        "confirmation_timestamp",
        "major_confirmed_at",
        "invalidated_at",
    )
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def _confirmation_after_formation(self) -> "SwingPoint":
        if self.confirmation_timestamp < self.formation_timestamp:
            raise ValueError(
                "confirmation_timestamp cannot precede formation_timestamp "
                "— this would be look-ahead by construction"
            )
        if self.major_flag and self.major_confirmed_at is None:
            raise ValueError("major_flag=True requires major_confirmed_at")
        return self


class StructureEventType(str, Enum):
    BOS = "BOS"
    CHOCH = "CHOCH"


class StructureEvent(UTCModel):
    """A close-beyond-level event. Wick-only penetration is never a valid
    StructureEvent — Strategy FROZEN v1.0 §2: 'A wick through a level is
    not a BOS.' This is enforced by callers (the detector), not by this
    schema alone, but the schema has no field for 'wick_only=True' on
    purpose — there is no such thing as a wick-based StructureEvent."""

    event_id: str
    type: StructureEventType
    direction: Direction
    broken_swing_id: str
    confirming_candle_id: str
    confirmed_at: datetime
    timeframe: Timeframe

    @field_validator("confirmed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class H4BiasStateValue(str, Enum):
    """v4 §C: WARNING belongs only to H4 (T3). Four resting states —
    REVERSAL_CONFIRMATION and WARNING_CANCELLED are transition events
    (see BiasTransitionEvent), never resting states themselves."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    BULLISH_WARNING = "BULLISH_WARNING"
    BEARISH_WARNING = "BEARISH_WARNING"


#: Valid transitions, enumerated explicitly rather than left to be inferred.
#: v4 §C: no direct BULLISH<->BEARISH without WARNING; no direct
#: BULLISH_WARNING<->BEARISH_WARNING without first resolving.
VALID_H4_BIAS_TRANSITIONS: dict[H4BiasStateValue, frozenset[H4BiasStateValue]] = {
    H4BiasStateValue.BULLISH: frozenset({H4BiasStateValue.BEARISH_WARNING}),
    H4BiasStateValue.BEARISH: frozenset({H4BiasStateValue.BULLISH_WARNING}),
    H4BiasStateValue.BULLISH_WARNING: frozenset(
        {H4BiasStateValue.BULLISH, H4BiasStateValue.BEARISH}
    ),
    H4BiasStateValue.BEARISH_WARNING: frozenset(
        {H4BiasStateValue.BEARISH, H4BiasStateValue.BULLISH}
    ),
}


class H4BiasState(UTCModel):
    """Singleton-per-symbol current bias state. Each transition produces a
    new H4BiasState instance (immutable model) plus a BiasTransitionEvent
    recording why — never an in-place mutation."""

    state_id: str
    symbol: str
    current_state: H4BiasStateValue
    entered_at: datetime
    warning_started_at: Optional[datetime] = None
    warning_expires_at: Optional[datetime] = None
    triggering_event_id: str

    @field_validator("entered_at", "warning_started_at", "warning_expires_at")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def _warning_fields_consistent(self) -> "H4BiasState":
        is_warning = self.current_state in (
            H4BiasStateValue.BULLISH_WARNING,
            H4BiasStateValue.BEARISH_WARNING,
        )
        if is_warning and (self.warning_started_at is None or self.warning_expires_at is None):
            raise ValueError(
                "a *_WARNING state requires warning_started_at and warning_expires_at"
            )
        if not is_warning and (
            self.warning_started_at is not None or self.warning_expires_at is not None
        ):
            raise ValueError(
                "warning_started_at/warning_expires_at must be null outside a WARNING state"
            )
        return self


class BiasTransitionTrigger(str, Enum):
    CHOCH = "CHOCH"
    REVERSAL_CONFIRMATION = "REVERSAL_CONFIRMATION"
    WARNING_EXPIRY_RESTORE = "WARNING_EXPIRY_RESTORE"
    WARNING_CANCELLED = "WARNING_CANCELLED"


class BiasTransitionEvent(UTCModel):
    transition_id: str
    from_state: H4BiasStateValue
    to_state: H4BiasStateValue
    trigger_type: BiasTransitionTrigger
    triggering_structural_event_id: str
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _transition_is_legal(self) -> "BiasTransitionEvent":
        allowed = VALID_H4_BIAS_TRANSITIONS.get(self.from_state, frozenset())
        if self.to_state not in allowed:
            raise ValueError(
                f"illegal H4 bias transition {self.from_state} -> {self.to_state} "
                f"(Strategy FROZEN v1.0 / v4 §C)"
            )
        return self


class ReversalConfirmation(UTCModel):
    """v5 I2: pure structural-break event, measured on H4 swings. Never
    requires displacement or liquidity interaction — that would blur it
    with MSSEvent, which owns that job (Strategy FROZEN v1.0 §B)."""

    confirmation_id: str
    direction: Direction
    confirming_swing_id: str
    confirmed_at: datetime
    warning_state_id: str

    @field_validator("confirmed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
