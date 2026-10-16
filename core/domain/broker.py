"""BrokerProfile — Execution FROZEN v1.0 OD1: broker-neutral architecture.
The strategy layer never reads this; only execution/ and brokers/ do."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import model_validator

from core.domain.common import UTCModel


class AccountMode(str, Enum):
    HEDGING = "HEDGING"
    NETTING = "NETTING"


class TPRepresentationMode(str, Enum):
    """OD7 (Execution FROZEN v1.0): genuinely broker-dependent — this
    field is what gives the question a deterministic home rather than
    leaving it orphaned. Resolved once a concrete broker is chosen."""

    SEPARATE_ORDERS = "SEPARATE_ORDERS"
    STAGED_PARTIAL_CLOSE = "STAGED_PARTIAL_CLOSE"


class BrokerProfile(UTCModel):
    broker_id: str
    account_mode: AccountMode
    symbol: str
    digits: int
    tick_size: Decimal
    tick_value: Decimal
    min_volume: Decimal
    max_volume: Decimal
    volume_step: Decimal
    minimum_stop_distance: Decimal
    freeze_level: Decimal
    filling_mode: str
    tp_representation_mode: TPRepresentationMode
    trading_sessions: tuple[str, ...]
    margin_rules: dict[str, Decimal]
    supported_operations: tuple[str, ...]
    spread: Decimal = Decimal("0")
    commission_per_lot: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")

    @model_validator(mode="after")
    def _volume_bounds(self) -> "BrokerProfile":
        if self.min_volume > self.max_volume:
            raise ValueError("min_volume must be <= max_volume")
        return self
