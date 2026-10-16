"""Typed configuration — Implementation Spec v1.0 §12. Every tunable
parameter carries {value, unit, status, source_version, phase3_testable}.
Backtester and live engine consume this exact representation — no
parallel/duplicated config anywhere else in the codebase."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T", int, Decimal, str, bool)


class ParameterStatus(str, Enum):
    LOCKED = "LOCKED"
    PROVISIONAL = "PROVISIONAL"
    DEFERRED = "DEFERRED"


class Parameter(BaseModel, Generic[T]):
    """A single configuration value with full provenance. Deliberately
    NOT frozen — Phase 3 sensitivity testing needs to construct new
    Parameter instances with different values while everything else
    (unit, status, source_version) stays traceable to its origin."""

    model_config = ConfigDict(extra="forbid")

    value: T
    unit: str
    status: ParameterStatus
    source_version: str
    phase3_testable: bool


class ScoringWeights(BaseModel):
    """Phase 2 §17's 100-point model. Milestone 5 review finding: these
    weights were originally hardcoded as class constants inside
    strategy/scoring/scoring_engine.py — a genuine violation of 'every
    configurable parameter must come from the existing configuration
    system,' since these are exactly the kind of PROVISIONAL,
    Phase-3-testable values every other parameter in this file already
    goes through. Moved here; the engine now reads from this."""

    model_config = ConfigDict(extra="forbid")

    d1_h4_alignment: Parameter[Decimal] = Parameter(
        value=Decimal("15"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    h1_alignment: Parameter[Decimal] = Parameter(
        value=Decimal("10"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    premium_discount: Parameter[Decimal] = Parameter(
        value=Decimal("10"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    liquidity_sweep: Parameter[Decimal] = Parameter(
        value=Decimal("15"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    mss: Parameter[Decimal] = Parameter(
        value=Decimal("15"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    displacement: Parameter[Decimal] = Parameter(
        value=Decimal("10"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    fvg: Parameter[Decimal] = Parameter(
        value=Decimal("5"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    order_block: Parameter[Decimal] = Parameter(
        value=Decimal("5"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    ote: Parameter[Decimal] = Parameter(
        value=Decimal("5"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    session_alignment: Parameter[Decimal] = Parameter(
        value=Decimal("5"), unit="points", status=ParameterStatus.DEFERRED,
        source_version="phase2-v1", phase3_testable=False,
    )
    target_liquidity: Parameter[Decimal] = Parameter(
        value=Decimal("5"), unit="points", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )


class StrategyConfig(BaseModel):
    """Values pulled directly from Strategy FROZEN v1.0 §3's parameter
    registry. Every one of these is PROVISIONAL by design — the
    algorithms that consume them are LOCKED, the numbers are not
    (Strategy FROZEN v1.0 §2 / Implementation Spec v1.0 §12)."""

    model_config = ConfigDict(extra="forbid")

    fractal_left: Parameter[int] = Parameter(
        value=2, unit="candles", status=ParameterStatus.LOCKED,
        source_version="phase2-v1", phase3_testable=False,
    )
    fractal_right: Parameter[int] = Parameter(
        value=2, unit="candles", status=ParameterStatus.LOCKED,
        source_version="phase2-v1", phase3_testable=False,
    )
    major_swing_atr: Parameter[Decimal] = Parameter(
        value=Decimal("1.0"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    equal_level_tolerance_atr: Parameter[Decimal] = Parameter(
        value=Decimal("0.05"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    sweep_min_penetration_atr: Parameter[Decimal] = Parameter(
        value=Decimal("0.05"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    sweep_max_penetration_atr: Parameter[Decimal] = Parameter(
        value=Decimal("0.50"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    sweep_reversal_window: Parameter[int] = Parameter(
        value=3, unit="candles", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    displacement_body_median_multiple: Parameter[Decimal] = Parameter(
        value=Decimal("1.5"), unit="x median", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    displacement_range_atr: Parameter[Decimal] = Parameter(
        value=Decimal("1.2"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    premium_zone_pct: Parameter[Decimal] = Parameter(
        value=Decimal("52.5"), unit="%", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    discount_zone_pct: Parameter[Decimal] = Parameter(
        value=Decimal("47.5"), unit="%", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    micro_swing_min_retracement_atr: Parameter[Decimal] = Parameter(
        value=Decimal("0.1"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="v5-I1", phase3_testable=True,
    )
    initial_sl_buffer_atr: Parameter[Decimal] = Parameter(
        value=Decimal("0.10"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    trailing_sl_buffer_atr: Parameter[Decimal] = Parameter(
        value=Decimal("0.10"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="v4-G", phase3_testable=True,
    )
    minimum_stop_atr: Parameter[Decimal] = Parameter(
        value=Decimal("0.25"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    max_stop_atr: Parameter[Decimal] = Parameter(
        value=Decimal("3.5"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="fix-zero-trade", phase3_testable=True,
    )
    """Spec §8.3: if structural SL distance > max_stop_atr * ATR, skip the trade.
    On M5 XAUUSD with ATR ~8 pts, 3.5x = 28 pts max stop. Stops wider than this
    indicate either a very wide zone (inefficient entry) or a mismatched structural
    anchor and are not worth the unfavourable risk geometry."""
    min_target_atr: Parameter[Decimal] = Parameter(
        value=Decimal("1.0"), unit="x ATR", status=ParameterStatus.PROVISIONAL,
        source_version="fix-zero-trade", phase3_testable=True,
    )
    """Minimum distance from entry to a TP pool expressed in ATR multiples.
    Pools closer than this are micro-noise (e.g. a 0.50-pt equal high on M5
    with ATR=8) and should not qualify as meaningful opposing liquidity targets.
    Prevents the blended R:R gate from always failing because TP1 is trivially
    close. 1.0x ATR ≈ 8 pts on XAUUSD M5 — still reachable, not a big target."""
    minimum_rr: Parameter[Decimal] = Parameter(
        value=Decimal("3.8"), unit="ratio", status=ParameterStatus.PROVISIONAL,
        source_version="RR-02", phase3_testable=True,
    )
    """Blended R:R gate (RR-01 LOCKED algorithm, value PROVISIONAL).
    RR-02 (docs/DECISIONS.md): raised from 2.0 to 3.8, the first deliberate
    tuning pass following walk_forward_6m_v3, done AFTER both TRAIL-01 bugs
    (confirmation-engine regression, stale-swing trailing stop) were fixed,
    so this is tuning against a run no longer corrupted by either. Clean
    baseline before this change: 35 trades/6mo, 42.9% win rate, expectancy
    +0.95R, profit factor 2.72. Target reward floor is 3.8; 3.5 is the
    agreed fallback if 3.8 leaves too few trades (~15-20) to be
    statistically meaningful over a 6-month window — re-run and compare
    trade count before accepting 3.8 as final. Expect fewer trades, a
    higher average win size, and likely a lower win rate (high-RR setups
    are structurally harder to reach before a stop triggers) — a
    deliberate quality-over-volume tradeoff, not a free improvement."""
    """HISTORY — phase2-v1 note, superseded by RR-02 above, kept per
    DECISIONS.md protocol (history is useful, not deleted). Lowered from
    2.0 to 1.5 — diagnostic runs showed that even with structurally valid
    setups on M5 XAUUSD a blended 2.0 is very hard to satisfy simultaneously
    with the structural stop anchor. NOTE: the code's actual value was
    never changed to 1.5 — it remained 2.0 until RR-02 above; this
    discrepancy is kept visible rather than silently erased."""

    tp1_allocation_pct: Parameter[Decimal] = Parameter(
        value=Decimal("30"), unit="%", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    minimum_score: Parameter[int] = Parameter(
        value=70, unit="points/100", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    choch_warning_max_candles: Parameter[int] = Parameter(
        value=20, unit="H4 candles", status=ParameterStatus.PROVISIONAL,
        source_version="v4-C", phase3_testable=True,
    )
    setup_expiry_m15_candles: Parameter[int] = Parameter(
        value=48, unit="M15 candles", status=ParameterStatus.PROVISIONAL,
        source_version="v5-I8", phase3_testable=True,
    )
    max_pending_setups: Parameter[int] = Parameter(
        value=3, unit="count", status=ParameterStatus.PROVISIONAL,
        source_version="v5-I6", phase3_testable=True,
    )
    # ------------------------------------------------------------------
    # PHASE A — configurable timing parameters, DISABLED (0) for the
    # Phase A baseline so Phase A does not change strategy behaviour.
    # 0 == disabled / current behaviour. Candidate values are for Phase B
    # research only; none was chosen from backtest results.
    # ------------------------------------------------------------------
    touch_confirmation_timeout_minutes: Parameter[int] = Parameter(
        value=0, unit="minutes (0 = disabled)", status=ParameterStatus.PROVISIONAL,
        source_version="phase-a-audit-5.1", phase3_testable=True,
    )
    """Audit 5.1: maximum time between a zone touch and its micro-structure
    confirmation. 0 = disabled (current behaviour: a touched setup stays
    WAITING_FOR_CONFIRMATION until the broad setup expiry)."""
    sweep_to_displacement_max_age_candles: Parameter[int] = Parameter(
        value=0, unit="M5 candles (0 = disabled)", status=ParameterStatus.PROVISIONAL,
        source_version="phase-a-audit-5.2", phase3_testable=True,
    )
    """Audit 5.2: maximum age of a sweep when paired with a displacement.
    0 = disabled (current behaviour: next qualifying displacement, any age)."""
    scoring_weights: ScoringWeights = ScoringWeights()


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_per_trade_pct: Parameter[Decimal] = Parameter(
        value=Decimal("0.5"), unit="% equity", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    daily_loss_limit_pct: Parameter[Decimal] = Parameter(
        value=Decimal("2.0"), unit="% equity", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    max_consecutive_losses: Parameter[int] = Parameter(
        value=3, unit="count", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    max_trades_per_day: Parameter[int] = Parameter(
        value=3, unit="count", status=ParameterStatus.PROVISIONAL,
        source_version="phase2-v1", phase3_testable=True,
    )
    # PHASE A (owner decisions 2026-10-03) -----------------------------
    sizing_mode: Parameter[str] = Parameter(
        value="FIXED_RISK", unit="FIXED_RISK | COMPOUNDING", status=ParameterStatus.LOCKED,
        source_version="phase-a-P0.3", phase3_testable=False,
    )
    """FIXED_RISK = risk_per_trade_pct of the initial/reference balance
    (research default, no compounding). COMPOUNDING = risk_per_trade_pct
    of current equity (live/realistic growth simulation)."""
    reset_consecutive_losses_at_utc_midnight: Parameter[bool] = Parameter(
        value=True, unit="bool", status=ParameterStatus.LOCKED,
        source_version="phase-a-P0.3", phase3_testable=False,
    )
    """Daily counters and the consecutive-loss lock reset at 00:00 UTC —
    never broker server time (deterministic, timezone-independent)."""


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_expiry_seconds: Parameter[int] = Parameter(
        value=5, unit="seconds", status=ParameterStatus.PROVISIONAL,
        source_version="execution-v1-OD3", phase3_testable=True,
    )
    max_acceptable_slippage_atr: Parameter[Decimal] = Parameter(
        value=Decimal("0.10"), unit="x ATR(M5)", status=ParameterStatus.PROVISIONAL,
        source_version="execution-v1-OD4", phase3_testable=True,
    )
    reconciliation_heartbeat_seconds: Parameter[int] = Parameter(
        value=15, unit="seconds", status=ParameterStatus.PROVISIONAL,
        source_version="execution-v1-OD5", phase3_testable=True,
    )
    retry_max_attempts: Parameter[int] = Parameter(
        value=3, unit="count", status=ParameterStatus.PROVISIONAL,
        source_version="execution-v1-OD6", phase3_testable=True,
    )
    sl_attach_max_retries: Parameter[int] = Parameter(
        value=2, unit="count", status=ParameterStatus.LOCKED,
        source_version="execution-v1-OD2", phase3_testable=False,
    )


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: Parameter[str] = Parameter(
        value="XAUUSD", unit="symbol", status=ParameterStatus.LOCKED,
        source_version="phase1-v1", phase3_testable=False,
    )


class EngineConfig(BaseModel):
    """The full config bundle. `config_hash()` is what ties every
    TradeDecision/experiment back to the exact configuration that
    produced it (Implementation Spec v1.0 §4/§13, Phase 3's
    reproducible-experiment schema)."""

    model_config = ConfigDict(extra="forbid")

    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    execution: ExecutionConfig = ExecutionConfig()
    data: DataConfig = DataConfig()

    def config_hash(self) -> str:
        canonical = self.model_dump_json(
            exclude_none=False,
        )
        # Deterministic: rely on pydantic's stable field ordering, then hash.
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
