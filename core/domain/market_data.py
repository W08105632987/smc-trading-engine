"""Market-data domain objects.

Numeric Precision Boundary (Technical Architecture v1.0): price, volume,
and every field that participates in a financial decision uses Decimal.
Analytical-only fields (e.g. ATR values used purely for scoring/filters,
never for SL/TP/position-size arithmetic) may use float — this module
keeps OHLC prices as Decimal since they feed directly into structural and
financial calculations downstream.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import field_validator, model_validator

from core.domain.common import Timeframe, UTCModel, ensure_utc


class DataSource(str, Enum):
    BROKER_NATIVE = "BROKER_NATIVE"
    AGGREGATED = "AGGREGATED"


class DataQualityFlag(str, Enum):
    GAP = "GAP"
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    OUTLIER_SUSPECTED = "OUTLIER_SUSPECTED"


class Candle(UTCModel):
    """One OHLC bar. `candle_id` is deterministic (symbol+timeframe+close
    timestamp), matching the dedup rule in Strategy FROZEN v1.0 §4 ('never
    infer missing candles as real candles', 'dedupe by symbol + timeframe
    + close timestamp')."""

    candle_id: str
    symbol: str
    timeframe: Timeframe
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Optional[Decimal] = None
    timestamp_open: datetime
    timestamp_close: datetime
    source: DataSource
    confirmed: bool
    data_quality_flags: tuple[DataQualityFlag, ...] = ()

    @field_validator("timestamp_open", "timestamp_close")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _validate_ohlc_integrity(self) -> "Candle":
        # Strategy FROZEN v1.0 §4: "Validate OHLC integrity: high >= max
        # (open, close), low <= min(open, close), high >= low."
        if self.high < max(self.open, self.close):
            raise ValueError("high must be >= max(open, close)")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be <= min(open, close)")
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if self.timestamp_close <= self.timestamp_open:
            raise ValueError("timestamp_close must be after timestamp_open")
        return self

    @staticmethod
    def build_id(symbol: str, timeframe: Timeframe, timestamp_close: datetime) -> str:
        """Deterministic candle_id — the mechanism behind the dedup rule."""
        ts = ensure_utc(timestamp_close).isoformat()
        return f"{symbol}:{timeframe.value}:{ts}"


class SymbolSpecification(UTCModel):
    symbol: str
    digits: int
    tick_size: Decimal
    tick_value: Decimal
    contract_size: Decimal
    min_volume: Decimal
    max_volume: Decimal
    volume_step: Decimal

    @model_validator(mode="after")
    def _validate_volume_bounds(self) -> "SymbolSpecification":
        if self.min_volume > self.max_volume:
            raise ValueError("min_volume must be <= max_volume")
        if self.volume_step <= 0:
            raise ValueError("volume_step must be positive")
        return self


class ATRContext(UTCModel):
    """Analytical value — computed with float/NumPy upstream (Technical
    Architecture v1.0, Numeric Precision Boundary) and normalized to
    Decimal here, since ATR feeds directly into SL/TP/sizing math
    downstream and therefore crosses into the financial-decision side of
    the boundary at this point."""

    symbol: str
    timeframe: Timeframe
    period: int
    value: Decimal
    computed_at: datetime

    @field_validator("computed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("value")
    @classmethod
    def _positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("ATR value must be positive")
        return value
