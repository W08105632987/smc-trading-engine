"""Zone domain objects: FVG and OrderBlock, per Strategy FROZEN v1.0 §7,
Phase 2 §12-13. A Zone's identity (specifically origin_candle_id) is what
Setup.originating_zone_id/originating_candle_id ultimately key off —
Setup identity is tied to the zone's creation event, never to price level
alone (v4 §E)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import field_validator, model_validator

from core.domain.common import Direction, UTCModel, ensure_utc


class ZoneType(str, Enum):
    FVG = "FVG"
    ORDER_BLOCK = "ORDER_BLOCK"


class Zone(UTCModel):
    zone_id: str
    zone_type: ZoneType
    direction: Direction
    upper: Decimal
    lower: Decimal
    origin_candle_id: str
    creation_timestamp: datetime
    linked_displacement_id: Optional[str] = None
    linked_mss_id: Optional[str] = None
    invalidated_at: Optional[datetime] = None
    retest_count: int = 0

    @field_validator("creation_timestamp", "invalidated_at")
    @classmethod
    def _utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def _bounds_consistent(self) -> "Zone":
        if self.upper <= self.lower:
            raise ValueError("upper must be > lower")
        if self.retest_count < 0:
            raise ValueError("retest_count cannot be negative")
        return self


class FVG(Zone):
    """Bullish: low[candle3] > high[candle1]. Bearish: high[candle3] <
    low[candle1]. creation_timestamp == candle3's close (Phase 2 §12) —
    no extra confirmation lag beyond that candle closing."""

    zone_type: ZoneType = ZoneType.FVG
    candle1_id: str
    candle2_id: str
    candle3_id: str


class OrderBlock(Zone):
    """Phase 2 §13: 'among the most interpretation-sensitive SMC
    concepts' — kept isolated from everything else so alternative OB
    models can be tested later without touching other detectors."""

    zone_type: ZoneType = ZoneType.ORDER_BLOCK
    origin_candle_direction: Direction


class ZoneCreationEvent(UTCModel):
    event_id: str
    zone_id: str
    zone_type: ZoneType
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
