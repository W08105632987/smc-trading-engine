"""Strategy-side Protocols. Every detector/engine implements one of these
— defined here first, per Technical Architecture v1.0 ('interfaces before
implementations'). Deliberately data-in/data-out: no Protocol here may
depend on execution/ or brokers/ types (module boundary rule)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from core.domain.decision import TradeDecision
from core.domain.liquidity import DisplacementEvent, LiquidityPool, MSSEvent, SweepEvent
from core.domain.market_data import Candle
from core.domain.risk import RiskDecision
from core.domain.setups import Setup
from core.domain.structure import (
    BiasTransitionEvent,
    H4BiasState,
    ReversalConfirmation,
    StructureEvent,
    SwingPoint,
)
from core.domain.zones import Zone


class SwingDetector(Protocol):
    def detect(self, candles: Sequence[Candle]) -> Sequence[SwingPoint]: ...


class StructureDetector(Protocol):
    def detect(
        self, candles: Sequence[Candle], swings: Sequence[SwingPoint]
    ) -> Sequence[StructureEvent]: ...


class H4WarningEngine(Protocol):
    """Corrected from the original draft (Milestone 2 review): the
    original signature `evaluate(current_state, structure_events) ->
    H4BiasState` could not be satisfied by any correct implementation —
    ReversalConfirmation is deliberately a distinct type from
    StructureEvent (Strategy FROZEN v1.0 / v5 I2, precisely so it's
    never confused with a conventional BOS), and WARNING_EXPIRY_RESTORE
    requires a current-timestamp argument the original signature had no
    room for. This is the corrected contract, not a redefinition of
    behavior — every rule it encodes was already locked."""

    def evaluate(
        self,
        current_state: H4BiasState,
        new_choch: StructureEvent | None,
        reversal_confirmation: ReversalConfirmation | None,
        reconfirming_bos: StructureEvent | None,
        at: datetime,
    ) -> tuple[H4BiasState, BiasTransitionEvent | None]: ...


class LiquidityEngine(Protocol):
    def map_pools(self, candles: Sequence[Candle]) -> Sequence[LiquidityPool]: ...

    def detect_sweeps(
        self, candles: Sequence[Candle], pools: Sequence[LiquidityPool]
    ) -> Sequence[SweepEvent]: ...


class DisplacementEngine(Protocol):
    def detect(self, candles: Sequence[Candle]) -> Sequence[DisplacementEvent]: ...

    def detect_mss(
        self, sweeps: Sequence[SweepEvent], displacements: Sequence[DisplacementEvent]
    ) -> Sequence[MSSEvent]: ...


class ZoneEngine(Protocol):
    def detect(
        self, candles: Sequence[Candle], mss_events: Sequence[MSSEvent]
    ) -> Sequence[Zone]: ...


class SetupEngine(Protocol):
    def evaluate(
        self,
        structure: H4BiasState,
        zones: Sequence[Zone],
        existing_setups: Sequence[Setup],
    ) -> Sequence[Setup]: ...


class ScoringEngine(Protocol):
    def score(self, setup: Setup) -> Setup: ...


class RiskEngine(Protocol):
    def evaluate(self, setup: Setup) -> RiskDecision: ...


class TradeDecisionGenerator(Protocol):
    def generate(self, setup: Setup) -> TradeDecision | None: ...
