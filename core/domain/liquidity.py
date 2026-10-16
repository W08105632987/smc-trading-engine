"""Liquidity domain objects: LiquidityPool -> SweepEvent -> DisplacementEvent
-> MSSEvent, per Strategy FROZEN v1.0 §3, Phase 2 §7/§10/§11."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import field_validator, model_validator

from core.domain.common import Direction, Timeframe, UTCModel, ensure_utc


class LiquidityPoolType(str, Enum):
    PDH = "PDH"
    PDL = "PDL"
    PWH = "PWH"
    PWL = "PWL"
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"
    EQUAL_HIGH = "EQUAL_HIGH"
    EQUAL_LOW = "EQUAL_LOW"
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    ROUND_NUMBER = "ROUND_NUMBER"


class LiquidityScoreComponents(UTCModel):
    """Phase 2 §8 weights (defaults, PROVISIONAL — see core/config).
    Stored per-pool so research can later identify which components
    actually contribute, per the same document's own note."""

    htf_significance: Decimal
    directional_alignment: Decimal
    session_relevance: Decimal
    equal_touch_strength: Decimal
    recency: Decimal
    distance_reachability: Decimal
    unconsumed_status: Decimal


class LiquidityPool(UTCModel):
    pool_id: str
    type: LiquidityPoolType
    price: Decimal
    price_range_high: Optional[Decimal] = None
    price_range_low: Optional[Decimal] = None
    timeframe: Timeframe
    created_at: datetime
    score: Decimal
    score_components: LiquidityScoreComponents
    consumed_at: Optional[datetime] = None
    consumption_reason: Optional[str] = None
    is_major_swing: Optional[bool] = None
    """TP1/TP2 Resolution Gate, Decision 2: preserves SwingPoint.major_flag
    for SWING_HIGH/SWING_LOW pools so target selection can classify them
    internal (minor) vs external (major) per Discovery §3, without the
    swing/structure system itself changing. None for every other pool
    type, and for swing pools only if the caller genuinely never knew
    (never expected in practice — strategy/liquidity/liquidity_engine.py
    always sets it for swing pools)."""

    @field_validator("created_at", "consumed_at")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def _consumption_consistent(self) -> "LiquidityPool":
        if (self.consumed_at is None) != (self.consumption_reason is None):
            raise ValueError(
                "consumed_at and consumption_reason must be set together or not at all"
            )
        return self


class SweepEvent(UTCModel):
    """Strategy FROZEN v1.0 §5 / Phase 2 §10: penetration in
    [0.05, 0.50] x ATR (PROVISIONAL values live in config), close back
    inside the prior range required, reversal must confirm within 3
    execution candles or the sweep expires."""

    sweep_id: str
    liquidity_pool_id: str
    direction: Direction
    penetration_atr: Decimal
    sweep_candle_id: str
    close_back_candle_id: str
    reversal_confirmed_at: Optional[datetime] = None
    expiry_at: datetime
    valid: bool

    @field_validator("reversal_confirmed_at", "expiry_at")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else ensure_utc(value)

    @field_validator("penetration_atr")
    @classmethod
    def _positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("penetration_atr must be positive")
        return value


class DisplacementEvent(UTCModel):
    """Phase 2 §11: qualifies on body-size, range, and a break of a
    meaningful opposing swing — all evaluable from the displacement
    candle's own close, so confirmed_at == that candle's close (v4 §F:
    zero extra lag, self-contained)."""

    displacement_id: str
    direction: Direction
    candle_id: str
    body_ratio: Decimal
    range_atr_ratio: Decimal
    broken_swing_id: str
    fvg_id: Optional[str] = None
    valid: bool
    confirmed_at: datetime

    @field_validator("confirmed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class MSSEvent(UTCModel):
    """Strategy FROZEN v1.0: liquidity event + displacement + break of a
    meaningful opposing swing. confirmed_at is the LATEST of its three
    constituent confirmations (v4 §F) — never earlier, since MSS isn't
    knowable until all three parts are individually confirmed."""

    mss_id: str
    direction: Direction
    sweep_id: str
    displacement_id: str
    broken_swing_id: str
    confirmed_at: datetime

    @field_validator("confirmed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
