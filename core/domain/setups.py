"""Setup, TouchEvent, MicroSwing.

MicroSwing here is DELIBERATELY a different concept from structure.SwingPoint
(v5 I9 — the two must never be conflated): SwingPoint is the HTF 2-left/
2-right fractal used for structure/trailing; MicroSwing is a lightweight,
TouchEvent-scoped 1-candle pivot used only for M5 entry confirmation
(v4 §A, v5 I1). The `touch_id` foreign key on MicroSwing is what makes
"a micro-swing from touch N can never be used by touch N+1" a schema-level
guarantee rather than a rule someone has to remember to enforce by hand.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import field_validator, model_validator

from core.domain.common import Direction, UTCModel, ensure_utc


class MicroSwingQualification(str, Enum):
    PENDING = "PENDING"
    QUALIFIED = "QUALIFIED"
    REJECTED_INSUFFICIENT_RETRACEMENT = "REJECTED_INSUFFICIENT_RETRACEMENT"
    SUPERSEDED = "SUPERSEDED"


class MicroSwing(UTCModel):
    """v4 §A: a 1-candle pivot (immediate left/right neighbor rule),
    scoped to a single TouchEvent. confirmation_candle_id is always
    pivot_candle + 1 (1-candle lag — deliberately lighter than the HTF
    fractal's 2-candle lag, per the rationale in the strategy spec: M5
    confirmation shouldn't be structurally slower than the reaction it's
    reading)."""

    micro_swing_id: str
    touch_id: str
    direction: Direction
    pivot_candle_id: str
    pivot_price: Decimal
    confirmation_candle_id: str
    confirmed_at: datetime
    retracement_atr: Decimal
    qualification_status: MicroSwingQualification

    @field_validator("confirmed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("retracement_atr")
    @classmethod
    def _non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("retracement_atr cannot be negative")
        return value


class TouchOutcome(str, Enum):
    ACTIVE = "ACTIVE"
    NO_BREAK = "NO_BREAK"
    BREAK_INVALID = "BREAK_INVALID"
    CONFIRMED = "CONFIRMED"


class TouchEvent(UTCModel):
    """One touch of a Setup's zone. v4 §A / v5 I1: each touch resets
    micro-swing tracking — earlier pivots are logged (via micro_swings)
    but never reused once superseded or once the touch ends."""

    touch_id: str
    setup_id: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    reaction_leg_start_candle_id: str
    micro_swings: tuple[MicroSwing, ...] = ()
    selected_micro_swing_id: Optional[str] = None
    break_candle_id: Optional[str] = None
    outcome: TouchOutcome
    reason_code: Optional[str] = None

    @field_validator("started_at", "ended_at")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def _micro_swings_belong_to_this_touch(self) -> "TouchEvent":
        # Structural enforcement of the no-cross-touch-reuse invariant.
        for ms in self.micro_swings:
            if ms.touch_id != self.touch_id:
                raise ValueError(
                    f"MicroSwing {ms.micro_swing_id} belongs to touch "
                    f"{ms.touch_id}, not {self.touch_id} — a micro-swing "
                    f"from another TouchEvent can never be reused "
                    f"(v4 §A / v5 I1)"
                )
        if self.selected_micro_swing_id is not None:
            ids = {ms.micro_swing_id for ms in self.micro_swings}
            if self.selected_micro_swing_id not in ids:
                raise ValueError(
                    "selected_micro_swing_id must reference one of this "
                    "touch's own micro_swings"
                )
        if self.outcome != TouchOutcome.ACTIVE and self.ended_at is None:
            raise ValueError("a non-ACTIVE outcome requires ended_at to be set")
        return self


class SetupState(str, Enum):
    """The 7-state model, v4 §D / v5. CONFIRMED does not imply
    TRADE_APPROVED — hard gates + risk validation still run (v5 I4)."""

    WAITING_FOR_RETEST = "WAITING_FOR_RETEST"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    TRADE_APPROVED = "TRADE_APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    DISPLACED = "DISPLACED"  # v5 final freeze: distinct from REJECTED/EXPIRED/INVALIDATED


#: Terminal states — no legal transition out of any of these.
TERMINAL_SETUP_STATES: frozenset[SetupState] = frozenset(
    {
        SetupState.TRADE_APPROVED,
        SetupState.REJECTED,
        SetupState.EXPIRED,
        SetupState.INVALIDATED,
        SetupState.DISPLACED,
    }
)

#: Explicit legal-transition table (Implementation Spec v1.0 §5 / v5).
VALID_SETUP_TRANSITIONS: dict[SetupState, frozenset[SetupState]] = {
    SetupState.WAITING_FOR_RETEST: frozenset(
        {
            SetupState.WAITING_FOR_CONFIRMATION,
            SetupState.EXPIRED,
            SetupState.INVALIDATED,
            SetupState.DISPLACED,
        }
    ),
    SetupState.WAITING_FOR_CONFIRMATION: frozenset(
        {
            SetupState.WAITING_FOR_RETEST,  # touch fizzled, no break, not invalidated
            SetupState.CONFIRMED,
            SetupState.EXPIRED,
            SetupState.INVALIDATED,
            SetupState.DISPLACED,
        }
    ),
    SetupState.CONFIRMED: frozenset(
        {
            SetupState.TRADE_APPROVED,
            SetupState.REJECTED,
        }
    ),
    # Terminal states have no outgoing transitions.
    SetupState.TRADE_APPROVED: frozenset(),
    SetupState.REJECTED: frozenset(),
    SetupState.EXPIRED: frozenset(),
    SetupState.INVALIDATED: frozenset(),
    SetupState.DISPLACED: frozenset(),
}


class HTFContextSnapshot(UTCModel):
    """Captured once at setup creation (Implementation Spec v1.0 §5's
    resolved gap: score/context are immutable after creation; HTF
    *validity* is re-checked separately at approval via the hard gate,
    never by mutating this snapshot)."""

    d1_state: str
    h4_state: str
    h1_state: str


class Setup(UTCModel):
    setup_id: str
    originating_zone_id: str
    originating_candle_id: str
    direction: Direction
    created_at: datetime
    state: SetupState
    score: Decimal
    score_snapshot: dict[str, Decimal]
    htf_context: HTFContextSnapshot
    expiry_at: datetime
    invalidation_level: Decimal
    touch_events: tuple[TouchEvent, ...] = ()
    confirmation_event_id: Optional[str] = None
    trade_decision_id: Optional[str] = None
    displaced_by_setup_id: Optional[str] = None
    displaces_setup_id: Optional[str] = None

    @field_validator("created_at", "expiry_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _invariants(self) -> "Setup":
        if self.expiry_at <= self.created_at:
            raise ValueError("expiry_at must be after created_at")
        for touch in self.touch_events:
            if touch.setup_id != self.setup_id:
                raise ValueError(
                    f"TouchEvent {touch.touch_id} belongs to setup "
                    f"{touch.setup_id}, not {self.setup_id}"
                )
        if self.state == SetupState.DISPLACED and self.displaced_by_setup_id is None:
            raise ValueError("DISPLACED state requires displaced_by_setup_id")
        if self.state != SetupState.DISPLACED and self.displaced_by_setup_id is not None:
            raise ValueError("displaced_by_setup_id must be null outside DISPLACED state")
        if self.state == SetupState.TRADE_APPROVED and self.trade_decision_id is None:
            raise ValueError("TRADE_APPROVED state requires trade_decision_id")
        return self


def validate_setup_transition(from_state: SetupState, to_state: SetupState) -> None:
    """Raise ValueError on an illegal Setup state transition. Callers
    (the setup engine) use this before constructing the successor Setup
    instance — this is the single source of truth for what's legal,
    shared so no engine module can quietly diverge from it."""

    if from_state in TERMINAL_SETUP_STATES:
        raise ValueError(f"{from_state} is terminal; no transition is legal from it")
    allowed = VALID_SETUP_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise ValueError(
            f"illegal Setup transition {from_state} -> {to_state} "
            f"(Implementation Spec v1.0 §5)"
        )
