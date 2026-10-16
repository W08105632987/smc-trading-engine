"""ReplayPipeline — Milestone 9's full-integration orchestrator.

This module builds ONLY what MILESTONE_9_HANDOFF.md Sections C/J already
approved: the upstream loop that runs the complete detector chain
continuously across a replay, one M5 candle at a time, wiring together
the already-verified Milestone 1-8 engines. No new SMC rule, threshold,
or trading behavior is introduced here — every decision below is either
(a) a direct restatement of an already-locked rule, or (b) mechanical
orchestration wiring (constructing a domain object from already-computed
fields, converting one existing event type into another existing type),
or (c) an explicitly flagged external seam for something genuinely
unbuilt elsewhere in this codebase (never silently invented).

======================================================================
EXTERNAL INPUTS THIS ORCHESTRATOR REQUIRES — NOT INVENTED, GENUINELY
UNBUILT ELSEWHERE IN MILESTONES 1-8. Every one of these was ALREADY an
external/caller-supplied input in every M1-M8 engine and test (e.g.
BacktestEngine.run_setup took take_profit_1/2, scoring_factors, and
risk_snapshot as parameters, never computed them) — this pipeline
continues that same, already-established pattern rather than inventing
new logic to fill any of them:

  1. atr_provider(timeframe, candles) -> {candle_id: Decimal}
     No ATR computation exists anywhere in this codebase (every engine
     signature takes atr_by_candle_id as a parameter). Required.

  2. risk_snapshot_provider(at) -> RiskSnapshot
     No account/equity tracking subsystem exists in Milestones 1-8;
     RiskSnapshot has always been externally supplied (see every M5/M6
     test and BacktestEngine.run_setup). Required.

  3. tp_provider(setup, entry, stop) -> (tp1, tp2) | None — OPTIONAL.
     TP1/TP2 target selection was a known, explicitly unresolved gap
     through Milestone 9. It was resolved at the TP1/TP2 Resolution
     Gate (see project chat log) and is now implemented deterministically
     in strategy/targets/target_selector.py. When `tp_provider` is not
     supplied (the default), this pipeline uses that real selector
     against the SAME step's already-computed liquidity pools/sweeps —
     no external input is needed anymore. `tp_provider` remains
     available purely as an explicit test-injection seam; supplying one
     overrides the real selector entirely. Returning None from either
     path means "cannot evaluate yet" — the setup stays CONFIRMED and
     is retried the next step, never forced to a fallback value.

  4. scoring_factor_overrides(zone, mss) -> dict[str, bool], optional
     Supplies the confluence factors no detector in this codebase can
     currently determine: d1_h4_alignment, h1_alignment (D1/H1 are not
     wired — §F), premium_discount and ote (no premium/discount or OTE
     detector exists in any milestone), target_liquidity (couples to
     the same target-selection gap as #3). Defaults to all-False —
     conservative, never silently inflates a setup's score.
     liquidity_sweep / mss / displacement / fvg / order_block ARE
     legitimately derivable (a candidate is only ever constructed from
     an already-confirmed MSS+Zone pair) and are computed directly by
     this module, never delegated.

  5. htf_context_provider(at, h4_bias_state) -> HTFContextSnapshot,
     optional. D1/H1 structural wiring does not exist (§F: "only H4+M5
     was the concrete pair actually reasoned through"). Default reuses
     the real, tracked H4 state and marks d1_state/h1_state
     "NOT_WIRED" rather than fabricating a value.

None of these fill a gap with invented strategy logic — they are the
same seams M1-M8 already left external, now surfaced explicitly instead
of silently assumed.
======================================================================

TWO WIRING DECISIONS THAT REQUIRED JUDGMENT (flagged, not hidden):

  A. ReversalConfirmation / reconfirming_bos construction. No engine in
     M1-M8 ever constructs a ReversalConfirmation (it's only ever built
     directly in tests) — H4WarningEngineImpl.evaluate() needs one but
     nothing produces it. Per core/domain/structure.py's own docstring,
     v5 I2 defines a ReversalConfirmation as "a pure structural-break
     event, measured on H4 swings" with no independent formula beyond
     that. This module derives it mechanically from the SAME
     StructureDetectorImpl output already used everywhere else: while
     H4 bias is in a *_WARNING state, prevailing_direction is held at
     the pre-warning ("original") direction (structure detection itself
     owns no bias-transition logic, per its own docstring), so a
     further CHOCH-classified break in the warning's implied reversal
     direction IS exactly a second structural break against the
     original bias — translated 1:1 into a ReversalConfirmation using
     only fields the StructureEvent already carries. A BOS-classified
     break in the original direction is passed straight through as
     reconfirming_bos. No new detection logic, only a type conversion.

  B. Setup.invalidation_level for a newly-constructed candidate.
     MILESTONE_9_HANDOFF.md §F says invalidation_level IS derivable
     from existing MSS/structural data (unlike TP1/TP2). The zone's own
     far boundary is the direct, structurally-grounded choice: a
     BULLISH setup invalidates on a close below zone.lower, a BEARISH
     setup on a close above zone.upper — the same boundary
     backtest/setup_lifecycle.py's _is_invalidated() already checks
     price against, applied to the only boundary a Zone actually has.

KNOWN GAP, NOT ADDRESSED HERE (surfaced per MILESTONE_9_HANDOFF.md's
"stop and ask rather than guess" rule): the WAITING_FOR_CONFIRMATION ->
WAITING_FOR_RETEST "touch fizzled" transition is legal in
core.domain.setups.VALID_SETUP_TRANSITIONS, but ConfirmationEngineImpl
deliberately never declares a touch NO_BREAK/BREAK_INVALID (see its own
docstring: "this does NOT mean the touch has concluded... Declaring
NO_BREAK here would be this detector unilaterally deciding something is
finished"). No rule anywhere in the frozen specs or built engines
defines WHEN a touch should be considered fizzled. Rather than invent a
timeout, this pipeline never triggers that transition: a setup that
enters WAITING_FOR_CONFIRMATION stays there until it either CONFIRMS or
the setup itself EXPIRES/is INVALIDATED via the already-frozen
lifecycle checks. This is a real, disclosed scope limitation, not a
silent omission.

Also: core.events.schema.EventType has no member covering
BiasTransitionTrigger.WARNING_EXPIRY_RESTORE. Per "do not invent a
parallel event structure," this pipeline still applies that state
transition (bias state updates correctly) but does not emit a
dedicated ExecutionEvent for it this milestone.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Iterator, NamedTuple, Optional, Sequence

from core.config.registry import EngineConfig
from core.domain.common import Direction, Environment, Timeframe
from core.domain.execution import Position, PositionLifecycleState, ProtectionState
from core.domain.liquidity import MSSEvent
from core.domain.market_data import Candle
from core.domain.risk import RiskSnapshot
from core.domain.setups import (
    HTFContextSnapshot,
    MicroSwingQualification,
    Setup,
    SetupState,
    TouchOutcome,
    validate_setup_transition,
)
from core.domain.structure import (
    BiasTransitionTrigger,
    H4BiasState,
    H4BiasStateValue,
    ReversalConfirmation,
    StructureEvent,
    StructureEventType,
)
from core.domain.zones import Zone, ZoneType
from core.events.schema import EventType, ExecutionEvent

from backtest.telemetry import describe_targets as _telemetry_describe_targets
from backtest.accounting import (
    AccountLedger, LegResult, TP1Split, combine_legs, initial_dollar_risk, leg_pnl, split_tp1_volume,
)
from backtest.execution_adapter import BacktestExecutionAdapter, find_first_exit
from backtest.execution_adapter import ExitCheck
from backtest.setup_lifecycle import evaluate_setup_lifecycle
from backtest.tick_execution import find_first_exit_ticks, ohlc_ambiguous
from backtest.trade_log import TradeRecord, compute_realized_r
from decision.trade_decision_generator import TradeDecisionGeneratorImpl, TradeDecisionRejection
from market_data.candle_store.store import CandleStore
from market_data.replay.incremental_m5_layer import IncrementalM5Layer
from market_data.replay.reference_m5_layer import ReferenceM5Layer
from market_data.replay.replay_engine import ReplayEngine
from strategy.confirmation.confirmation_engine import ConfirmationEngineImpl
from strategy.displacement.displacement_engine import DisplacementEngineImpl
from strategy.liquidity.liquidity_engine import LiquidityEngineImpl
from strategy.risk.risk_engine import RiskEngineImpl
from strategy.scoring.scoring_engine import ScoringEngineImpl, ScoringFactors
from strategy.setups.setup_engine import SetupAdmissionEngine, apply_displacement
from strategy.structure.bos_choch_detector import StructureDetectorImpl
from strategy.structure.h4_warning_engine import H4WarningEngineImpl
from strategy.swings.swing_detector import SwingDetectorImpl
from strategy.targets.target_selector import select_targets
from strategy.trailing.trailing_engine import TrailingEngineImpl
from strategy.zones.zone_engine import ZoneEngineImpl


# TRAIL-01 (docs/DECISIONS.md): candle-count margin before a position's
# entry within which a major swing is still eligible for trailing-stop
# consideration. See the bugfix comment at its use site in
# _manage_open_position for the full rationale -- this bounds
# compute_new_stop's swing scan away from the full replay-to-date
# history (which produced economically meaningless stops from
# unrelated, much-earlier price regimes) while still covering
# legitimate structure immediately around entry. Mirrors the existing
# median_lookback=20 precedent in market_data/replay/incremental_m5_layer.py.
# The actual correctness guarantee is the invariant inside
# TrailingEngineImpl.compute_new_stop itself; this window is a
# secondary, performance-motivated bound (avoids rescanning the entire
# replay-to-date history on every POST_TP1 step).
TRAILING_SWING_LOOKBACK_MARGIN = 20


# --------------------------------------------------------------------
# ReplayState — EXACT fields per MILESTONE_9_HANDOFF.md §C.3.
# --------------------------------------------------------------------


@dataclass
class ReplayState:
    h4_bias_state: Optional[H4BiasState] = None
    last_seen_candle_count: dict = field(default_factory=dict)  # Timeframe -> int
    emitted_event_ids: set = field(default_factory=set)  # str
    active_setups: dict = field(default_factory=dict)  # setup_id -> Setup
    open_position: Optional[Position] = None
    open_position_phase: Optional[str] = None  # "PRE_TP1" | "POST_TP1" | None
    trailing_current_sl: Optional[Decimal] = None
    completed_trades: list = field(default_factory=list)  # TradeRecord

    # PHASE A (P0.2): explicit position-quantity accounting for the TP1
    # partial close. `original_volume` is the full volume at entry;
    # `tp1_leg` is the realized P&L of the TP1 leg (None if no partial
    # close happened); `tp1_split` records what the split decided
    # (including WHY a split was skipped, e.g. SPLIT_BELOW_MIN_VOLUME).
    original_volume: Optional[Decimal] = None
    tp1_leg: Optional[LegResult] = None
    tp1_split: Optional[TP1Split] = None
    tp1_exit_timestamp: Optional[datetime] = None

    # Milestone 10 blocker-5 addition. Strategy FROZEN v1.0 (v5 I10,
    # DEFERRED — see market_data/normalization/candle_validator.py and
    # market_data/quality/data_validator.py, the two places this exact
    # citation already appears) explicitly locks that a gap-affected
    # candle is "not automatically invalid" — this codebase is NOT
    # authorized to invent a rule that excludes or specially reacts to
    # gap-degraded candles by default. What WAS genuinely missing (the
    # readiness audit's finding) is observability: nothing recorded
    # WHICH H4/M5 candles feeding a given bias transition or structural
    # event were gap-affected, making it impossible to later tell "this
    # BOS/CHOCH used clean continuous data" from "this one was computed
    # across/adjacent to a real data outage" during result analysis.
    # These two sets are that missing audit trail — populated every
    # step from Candle.data_quality_flags, never used to alter which
    # candles the strategy engines see UNLESS the caller explicitly
    # supplies a `gap_policy` (see ReplayPipeline.__init__) — the
    # "isolate any remaining policy choice behind an explicit,
    # deterministic interface" requirement, not a new default behavior.
    gap_affected_h4_candle_ids: set = field(default_factory=set)  # str
    gap_affected_m5_candle_ids: set = field(default_factory=set)  # str


class PipelineStepResult(NamedTuple):
    at: datetime
    events: tuple  # tuple[ExecutionEvent, ...]
    trade: Optional[TradeRecord]


@dataclass(frozen=True)
class RunConfig:
    """Non-strategy numeric plumbing the risk engine needs — same
    fields BacktestEngine.run_setup (Milestone 8) already required as
    plain caller-supplied values, not strategy parameters."""

    tick_size: Decimal
    tick_value: Decimal
    volume_step: Decimal
    min_volume: Decimal
    max_volume: Decimal


def _default_scoring_overrides(zone: Zone, mss: MSSEvent) -> dict:
    return {}


def _default_htf_context(at: datetime, h4_state: H4BiasState) -> HTFContextSnapshot:
    return HTFContextSnapshot(
        d1_state="NOT_WIRED", h4_state=h4_state.current_state.value, h1_state="NOT_WIRED",
    )


def _h4_original_and_new_direction(state: H4BiasStateValue) -> tuple[Direction, Direction]:
    """Mirrors H4WarningEngineImpl._evaluate_warning's own internal
    mapping exactly (that method is private) — not a new rule."""
    if state == H4BiasStateValue.BEARISH_WARNING:
        return Direction.BULLISH, Direction.BEARISH
    if state == H4BiasStateValue.BULLISH_WARNING:
        return Direction.BEARISH, Direction.BULLISH
    raise ValueError(f"{state} is not a WARNING state")


def _h4_prevailing_direction(state: H4BiasState) -> Direction:
    if state.current_state == H4BiasStateValue.BULLISH:
        return Direction.BULLISH
    if state.current_state == H4BiasStateValue.BEARISH:
        return Direction.BEARISH
    original, _ = _h4_original_and_new_direction(state.current_state)
    return original


def _identity_gap_policy(candles: Sequence[Candle]) -> Sequence[Candle]:
    """Default gap policy — the frozen "not automatically invalid" rule
    (v5 I10, DEFERRED): every candle is passed through unchanged. A
    caller may supply a different, explicit policy (e.g. dropping
    GAP-flagged candles, or truncating to the most recent contiguous
    run) — that is a genuine unresolved decision this codebase does not
    make on anyone's behalf."""
    return candles


def _gap_affected_ids(candles: Sequence[Candle]) -> set:
    from core.domain.market_data import DataQualityFlag
    return {c.candle_id for c in candles if DataQualityFlag.GAP in c.data_quality_flags}


class ReplayPipeline:
    def __init__(
        self,
        symbol: str,
        candle_store: CandleStore,
        config: EngineConfig,
        run_config: RunConfig,
        *,
        swing_detector: SwingDetectorImpl,
        structure_detector: StructureDetectorImpl,
        h4_warning_engine: H4WarningEngineImpl,
        liquidity_engine: LiquidityEngineImpl,
        displacement_engine: DisplacementEngineImpl,
        zone_engine: ZoneEngineImpl,
        setup_admission_engine: SetupAdmissionEngine,
        confirmation_engine: ConfirmationEngineImpl,
        scoring_engine: ScoringEngineImpl,
        risk_engine: RiskEngineImpl,
        decision_generator: TradeDecisionGeneratorImpl,
        trailing_engine: TrailingEngineImpl,
        execution_adapter: BacktestExecutionAdapter,
        atr_provider: Callable[[Timeframe, Sequence[Candle]], dict],
        risk_snapshot_provider: Optional[Callable[[datetime], RiskSnapshot]] = None,
        tp_provider: Optional[Callable[[Setup, Decimal, Decimal], Optional[tuple]]] = None,
        genesis_at: datetime,
        initial_h4_bias: H4BiasStateValue = H4BiasStateValue.BULLISH,
        scoring_factor_overrides: Optional[Callable[[Zone, MSSEvent], dict]] = None,
        htf_context_provider: Optional[Callable[[datetime, H4BiasState], HTFContextSnapshot]] = None,
        gap_policy: Optional[Callable[[Sequence[Candle]], Sequence[Candle]]] = None,
        account_ledger: Optional[AccountLedger] = None,
        m5_engine: str = "FAST",
        tick_window_provider: Optional[Callable[[datetime, datetime], Sequence]] = None,
        trading_start: Optional[datetime] = None,
        telemetry=None,
    ) -> None:
        # E0: optional observation-only telemetry (backtest/telemetry.py). None => every hook below is skipped.
        self.telemetry = telemetry
        self._last_build_ctx = None  # (sweep, pool, atr_at_setup) of the most recent _build_setup_candidate; read by telemetry only
        # PHASE A (audit 3.2): chronological warm-up. Before `trading_start`
        # the whole detector/H4-bias machinery runs normally (so H4 state is
        # the real chronological state at the start of the test window), but
        # NO trade may open: setups that confirm during warm-up are REJECTED
        # with reason WARMUP_PERIOD (never left pending, which would later
        # trade at a stale warm-up entry price).
        self._trading_start = trading_start
        # PHASE A (P0.4): optional tick-level exit resolution. When a provider
        # `(window_start, window_end) -> ticks` is supplied, stop/TP1/TP2 hits
        # for each candle are resolved on the real bid/ask path instead of
        # OHLC. Candles with no ticks fall back to OHLC and are COUNTED.
        self._tick_window_provider = tick_window_provider
        self.tick_exit_resolutions = 0
        self.tick_fallback_to_ohlc = 0
        self.ohlc_ambiguous_candles = 0
        # PHASE A (P0.3): the risk state comes from a real AccountLedger
        # (balance/equity/daily P&L/UTC-reset counters). The legacy
        # `risk_snapshot_provider` remains only so pre-Phase-A callers and
        # tests keep working; when a ledger is supplied it always wins.
        if account_ledger is None and risk_snapshot_provider is None:
            raise ValueError("ReplayPipeline needs an account_ledger (preferred) or a risk_snapshot_provider")
        self._ledger = account_ledger
        self._symbol = symbol
        self._candle_store = candle_store
        self._config = config
        self._config_hash = config.config_hash()
        self._run_config = run_config

        self._swing_detector = swing_detector
        self._structure_detector = structure_detector
        self._h4_warning_engine = h4_warning_engine
        self._liquidity_engine = liquidity_engine
        self._displacement_engine = displacement_engine
        self._zone_engine = zone_engine
        # Milestone 10 performance fix (PERF-01, docs/DECISIONS.md): the
        # M5 layer (swings, pools, sweeps, displacement, MSS, FVGs,
        # order blocks) is computed incrementally instead of rescanning
        # the entire M5 candle history every single step — see
        # market_data/replay/incremental_m5_layer.py for the full
        # reasoning and its equivalence test. H4-side swing detection
        # (self._swing_detector, used directly below in
        # _recompute_h4_layer) is untouched — H4 candle counts stay
        # small enough all year that it was never the bottleneck.
        # PHASE A (audit 3.1): two engines. "REFERENCE" = canonical, exact,
        # non-truncated semantics (slow). "FAST" = incremental, with the
        # disclosed performance cutoffs; it must never be treated as the
        # source of truth (see tests/test_reference_fast_equivalence.py).
        if m5_engine not in ("FAST", "REFERENCE"):
            raise ValueError("m5_engine must be 'FAST' or 'REFERENCE'")
        self._m5_engine = m5_engine
        if m5_engine == "REFERENCE":
            self._m5_layer = ReferenceM5Layer(
                swing_detector=swing_detector, liquidity_engine=liquidity_engine,
                displacement_engine=displacement_engine, zone_engine=zone_engine,
            )
        else:
            self._m5_layer = IncrementalM5Layer(
                swing_detector=swing_detector, liquidity_engine=liquidity_engine,
                displacement_engine=displacement_engine, zone_engine=zone_engine,
            )
        self._setup_admission_engine = setup_admission_engine
        self._confirmation_engine = confirmation_engine
        self._scoring_engine = scoring_engine
        self._risk_engine = risk_engine
        self._decision_generator = decision_generator
        self._trailing_engine = trailing_engine
        self._execution_adapter = execution_adapter

        self._atr_provider = atr_provider
        self._risk_snapshot_provider = risk_snapshot_provider
        self._tp_provider = tp_provider
        self._scoring_factor_overrides = scoring_factor_overrides or _default_scoring_overrides
        self._htf_context_provider = htf_context_provider or _default_htf_context
        self._gap_policy = gap_policy or _identity_gap_policy

        self._decision_setup_map: dict = {}  # internal bookkeeping only, not part of ReplayState
        self._setup_zone_created: dict = {}   # setup_id -> zone.creation_timestamp (audit ledger only)
        self._balance_before_trade: dict = {}  # decision_id -> ledger balance at entry (audit ledger only)
        # PERF-06: O(1) candle-by-id lookup — eliminates the O(N) reversed()
        # linear scan in _attempt_decision that grew with every new candle.
        # Updated incrementally (new candles only) in step().
        self._m5_candle_by_id: dict = {}

        self.state = ReplayState(
            h4_bias_state=H4BiasState(
                state_id=f"h4:{symbol}", symbol=symbol, current_state=initial_h4_bias,
                entered_at=genesis_at, triggering_event_id="genesis",
            )
        )

    # ------------------------------------------------------------
    # Public replay loop
    # ------------------------------------------------------------

    def run(self) -> Iterator[PipelineStepResult]:
        replay = ReplayEngine(self._candle_store, self._symbol, Timeframe.M5)
        for step in replay.run():
            yield self.step(step.at, step.candles)

    def step(self, at: datetime, m5_candles: list) -> PipelineStepResult:
        """Processes exactly one M5 replay step, per
        MILESTONE_9_HANDOFF.md §J's 11-step order. `m5_candles` must
        already be CandleStore.get_up_to()-sliced by the caller (or via
        run(), which does this itself) — never anything else."""

        events: list = []

        self._check_warning_expiry(at)

        m5_candles = list(self._gap_policy(m5_candles))
        prev_m5_count = self.state.last_seen_candle_count.get(Timeframe.M5, 0)
        if len(m5_candles) > prev_m5_count:
            self.state.gap_affected_m5_candle_ids |= _gap_affected_ids(m5_candles[prev_m5_count:])
            self.state.last_seen_candle_count[Timeframe.M5] = len(m5_candles)
            # PERF-06: Update the O(1) candle lookup with only the newly-arrived candles.
            for _c in m5_candles[prev_m5_count:]:
                self._m5_candle_by_id[_c.candle_id] = _c

        h4_candles_raw = self._candle_store.get_up_to(self._symbol, Timeframe.H4, at)
        prev_h4_count = self.state.last_seen_candle_count.get(Timeframe.H4, 0)
        if len(h4_candles_raw) > prev_h4_count:
            # "New candle arrived?" is decided from the RAW store output,
            # never the gap_policy-filtered view — an opt-in policy that
            # excludes candles must not be able to suppress structure
            # recomputation just because it also shrank the count. The
            # policy is applied only to what the H4 layer actually SEES,
            # after this decision is already made.
            h4_candles = list(self._gap_policy(h4_candles_raw))
            self.state.gap_affected_h4_candle_ids |= _gap_affected_ids(h4_candles_raw[prev_h4_count:])
            events.extend(self._recompute_h4_layer(at, h4_candles))
            self.state.last_seen_candle_count[Timeframe.H4] = len(h4_candles_raw)

        m5_atr = self._atr_provider(Timeframe.M5, m5_candles)

        equal_level_tolerance = Decimal("0")
        if m5_candles:
            latest_atr = m5_atr.get(m5_candles[-1].candle_id)
            if latest_atr is not None:
                equal_level_tolerance = max(
                    2 * self._run_config.tick_size,
                    self._config.strategy.equal_level_tolerance_atr.value * latest_atr,
                )

        m5_swings, pools, sweeps, displacements, mss_events, fvgs, obs = self._m5_layer.update(
            m5_candles, m5_atr, equal_level_tolerance,
            self._config.strategy.sweep_min_penetration_atr.value,
            self._config.strategy.sweep_max_penetration_atr.value,
            self._config.strategy.sweep_reversal_window.value,
            mss_max_sweep_age=self._mss_max_sweep_age(),
        )
        zones_by_id = {z.zone_id: z for z in list(fvgs) + list(obs)}

        events.extend(self._construct_setup_candidates(at, mss_events, zones_by_id, sweeps, pools, m5_atr))

        events.extend(self._update_setup_lifecycles(at, m5_candles))
        events.extend(self._process_touches(at, m5_candles, m5_atr, zones_by_id))

        position_before = self.state.open_position
        events.extend(self._attempt_decisions(at, m5_candles, pools, sweeps, zones_by_id, m5_atr))
        just_opened_this_step = position_before is None and self.state.open_position is not None

        # A position opened THIS step must never be exit-checked against
        # the very candle that produced its entry price — only candles
        # strictly after entry can determine an exit (same rule
        # backtest/engine.py's Milestone 8 already enforced via
        # `post_entry_candles = candles[entry_index + 1:]`).
        if just_opened_this_step:
            trade = None
        else:
            exit_events, trade = self._manage_open_position(at, m5_candles)
            events.extend(exit_events)

        if trade is not None:
            self.state.completed_trades.append(trade)
            if self.telemetry is not None:
                self.telemetry.on_trade_closed(trade)

        return PipelineStepResult(at=at, events=tuple(events), trade=trade)

    # ------------------------------------------------------------
    # Event-sourcing helper
    # ------------------------------------------------------------

    def _find_exit(self, position, stop, tp1, tp2, candle):
        """Exit detection for ONE candle: tick path if a provider is
        configured and has ticks for the candle, else OHLC (SL-first)."""
        targets = [t for t in (tp1, tp2) if t is not None]
        if ohlc_ambiguous(position.direction == Direction.BULLISH, stop, targets, candle):
            self.ohlc_ambiguous_candles += 1
        if self._tick_window_provider is not None:
            ticks = list(self._tick_window_provider(candle.timestamp_open, candle.timestamp_close))
            if ticks:
                self.tick_exit_resolutions += 1
                te = find_first_exit_ticks(
                    position.direction == Direction.BULLISH, stop, tp1, tp2, ticks,
                    slippage=self._execution_adapter.slippage,
                )
                if te is None:
                    return None
                return ExitCheck(reason=te.reason, exit_price=te.exit_price, candle_id=candle.candle_id)
            self.tick_fallback_to_ohlc += 1
        # PHASE A (cost hardening): exit-side spread/slippage from the
        # execution adapter are now applied on the OHLC path too. Previously
        # they were silently omitted (entry costs only). Zero under
        # FRICTIONLESS, so FRICTIONLESS results are unchanged.
        return find_first_exit(
            position.direction, stop, tp1, tp2, [candle],
            spread=self._execution_adapter.spread, slippage=self._execution_adapter.slippage,
        )

    def _mss_max_sweep_age(self) -> Optional[timedelta]:
        """PHASE A (audit 5.2): None (disabled) unless the configured
        maximum sweep->displacement age (in M5 candles) is > 0."""
        n = self._config.strategy.sweep_to_displacement_max_age_candles.value
        return timedelta(minutes=5 * n) if n and n > 0 else None

    def _emit(
        self, sink: list, domain_id: str, event_type: EventType, at: datetime,
        entity_id: str, previous_state, new_state: str, payload: Optional[dict] = None,
    ) -> None:
        if domain_id in self.state.emitted_event_ids:
            return
        self.state.emitted_event_ids.add(domain_id)
        sink.append(
            ExecutionEvent(
                event_id=domain_id, event_type=event_type, timestamp=at, entity_id=entity_id,
                previous_state=previous_state, new_state=new_state, source="backtest.pipeline",
                environment=Environment.BACKTEST, config_hash=self._config_hash, payload=payload or {},
            )
        )

    # ------------------------------------------------------------
    # H4 layer
    # ------------------------------------------------------------

    def _check_warning_expiry(self, at: datetime) -> None:
        current = self.state.h4_bias_state
        if current.current_state not in (H4BiasStateValue.BULLISH_WARNING, H4BiasStateValue.BEARISH_WARNING):
            return
        if current.warning_expires_at is None or at < current.warning_expires_at:
            return
        new_state, transition = self._h4_warning_engine.evaluate(
            current, new_choch=None, reversal_confirmation=None, reconfirming_bos=None, at=at,
        )
        if transition is not None:
            if self.telemetry is not None:
                self.telemetry.on_h4_silent(at, current.current_state, new_state.current_state, "WARNING_EXPIRY_RESTORE")
            self.state.h4_bias_state = new_state
            # No EventType for WARNING_EXPIRY_RESTORE — see module docstring.

    def _recompute_h4_layer(self, at: datetime, h4_candles: list) -> list:
        events: list = []
        h4_atr = self._atr_provider(Timeframe.H4, h4_candles)
        h4_swings = self._swing_detector.detect(h4_candles, atr_by_candle_id=h4_atr)

        while True:
            prevailing = _h4_prevailing_direction(self.state.h4_bias_state)
            structure_events = self._structure_detector.detect(h4_candles, h4_swings, prevailing)
            unseen = [e for e in structure_events if e.event_id not in self.state.emitted_event_ids]
            if not unseen:
                break
            unseen.sort(key=lambda e: (e.confirmed_at, e.event_id))
            structure_event = unseen[0]
            self.state.emitted_event_ids.add(structure_event.event_id)

            _h4_before = self.state.h4_bias_state
            new_state, transition, event_type = self._advance_h4_bias(structure_event, at)
            if self.telemetry is not None:
                self.telemetry.on_h4_structure_event(
                    at, structure_event, _h4_before.current_state,
                    (new_state.current_state if transition is not None else _h4_before.current_state), transition)
            if transition is not None:
                self.state.h4_bias_state = new_state
                if event_type is not None:
                    # Blocker-5 observability: records whether ANY H4
                    # candle currently known to be gap-affected has been
                    # part of this replay's H4 series so far — not a
                    # claim that THIS SPECIFIC transition was caused by
                    # the gap, only that the data window it was computed
                    # over is not guaranteed gap-free. Deliberately
                    # coarse rather than falsely precise: attributing a
                    # transition to one exact candle would require a
                    # per-swing/per-event provenance chain the frozen
                    # detectors don't expose, and inventing one is out of
                    # this task's scope.
                    self._emit(
                        events, transition.transition_id, event_type, transition.timestamp,
                        new_state.state_id, transition.from_state.value, transition.to_state.value,
                        payload={"gap_affected_h4_data_seen": bool(self.state.gap_affected_h4_candle_ids)},
                    )
        return events

    def _advance_h4_bias(self, event: StructureEvent, at: datetime):
        current = self.state.h4_bias_state

        if current.current_state in (H4BiasStateValue.BULLISH, H4BiasStateValue.BEARISH):
            new_state, transition = self._h4_warning_engine.evaluate(
                current,
                new_choch=(event if event.type == StructureEventType.CHOCH else None),
                reversal_confirmation=None, reconfirming_bos=None, at=at,
            )
            event_type = EventType.WARNING_ENTERED if transition is not None else None
            return new_state, transition, event_type

        original, new_direction = _h4_original_and_new_direction(current.current_state)
        reversal_confirmation = None
        reconfirming_bos = None
        if event.type == StructureEventType.CHOCH and event.direction == new_direction:
            reversal_confirmation = ReversalConfirmation(
                confirmation_id=f"rc:{event.event_id}", direction=new_direction,
                confirming_swing_id=event.broken_swing_id, confirmed_at=event.confirmed_at,
                warning_state_id=current.state_id,
            )
        elif event.type == StructureEventType.BOS and event.direction == original:
            reconfirming_bos = event

        new_state, transition = self._h4_warning_engine.evaluate(
            current, new_choch=None, reversal_confirmation=reversal_confirmation,
            reconfirming_bos=reconfirming_bos, at=at,
        )
        event_type = None
        if transition is not None:
            if transition.trigger_type == BiasTransitionTrigger.REVERSAL_CONFIRMATION:
                event_type = EventType.REVERSAL_CONFIRMED
            elif transition.trigger_type == BiasTransitionTrigger.WARNING_CANCELLED:
                event_type = EventType.WARNING_CANCELLED
        return new_state, transition, event_type

    def _htf_context_still_valid(self, setup: Setup) -> bool:
        state = self.state.h4_bias_state
        if state.current_state == H4BiasStateValue.BULLISH:
            return setup.direction == Direction.BULLISH
        if state.current_state == H4BiasStateValue.BEARISH:
            return setup.direction == Direction.BEARISH
        original, _ = _h4_original_and_new_direction(state.current_state)
        return setup.direction == original

    # ------------------------------------------------------------
    # Setup candidate construction (M5 step 5/6)
    # ------------------------------------------------------------

    def _construct_setup_candidates(
        self, at: datetime, mss_events, zones_by_id: dict, sweeps, pools, m5_atr: dict,
    ) -> list:
        events: list = []
        if not mss_events:
            return events

        # Index zones by their linkage for O(1) matching instead of O(N_mss * N_zones) nested scan
        fvg_by_disp: dict = {}
        ob_by_mss: dict = {}
        for zone in zones_by_id.values():
            if zone.zone_type == ZoneType.FVG and zone.linked_displacement_id:
                fvg_by_disp.setdefault(zone.linked_displacement_id, []).append(zone)
            elif zone.zone_type == ZoneType.ORDER_BLOCK and zone.linked_mss_id:
                ob_by_mss.setdefault(zone.linked_mss_id, []).append(zone)

        active_zone_ids = {s.originating_zone_id for s in self.state.active_setups.values()}

        for mss in mss_events:
            matching_zones = fvg_by_disp.get(mss.displacement_id, []) + ob_by_mss.get(mss.mss_id, [])
            for zone in matching_zones:
                if zone.direction != mss.direction:
                    continue  # data-integrity guard, never expected to trip

                candidate_marker = f"{zone.zone_id}:SETUP_CANDIDATE"
                if candidate_marker in self.state.emitted_event_ids:
                    continue
                self.state.emitted_event_ids.add(candidate_marker)

                if zone.zone_id in active_zone_ids:
                    if self.telemetry is not None:
                        self.telemetry.on_zone_considered(zone.zone_id, True)
                    continue
                if self.telemetry is not None:
                    self.telemetry.on_zone_considered(zone.zone_id, False)

                self._emit(events, zone.zone_id, EventType.ZONE_CREATED, zone.creation_timestamp,
                           zone.zone_id, None, zone.zone_type.value)
                self._emit(events, mss.mss_id, EventType.MSS_CONFIRMED, mss.confirmed_at,
                           mss.mss_id, None, "CONFIRMED")

                candidate = self._build_setup_candidate(zone, mss, sweeps, pools, m5_atr)
                admission = self._setup_admission_engine.evaluate(
                    list(self.state.active_setups.values()), candidate,
                )
                if self.telemetry is not None:
                    _sw, _pl, _atr = self._last_build_ctx if self._last_build_ctx is not None else (None, None, None)
                    self.telemetry.on_candidate(
                        at, candidate, zone, mss, _sw, _pl, _atr, self.state.h4_bias_state.current_state,
                        bool(admission.admitted), (admission.displaced.setup_id if admission.displaced is not None else None),
                        sum(1 for s_ in self.state.active_setups.values()
                            if s_.state in (SetupState.WAITING_FOR_RETEST, SetupState.WAITING_FOR_CONFIRMATION)),
                    )
                if not admission.admitted:
                    continue

                if admission.displaced is not None:
                    if self.telemetry is not None:
                        self.telemetry.on_terminal(at, admission.displaced.setup_id, "DISPLACED", "ADMISSION_CAP", by=candidate.setup_id)
                    displaced = apply_displacement(admission.displaced, candidate.setup_id)
                    self.state.active_setups[displaced.setup_id] = displaced
                    self._emit(events, f"{displaced.setup_id}:DISPLACED", EventType.SETUP_DISPLACED,
                               at, displaced.setup_id, None, SetupState.DISPLACED.value)

                self.state.active_setups[candidate.setup_id] = candidate
                active_zone_ids.add(candidate.originating_zone_id)
                self._emit(events, f"{candidate.setup_id}:CREATED", EventType.SETUP_CREATED, at,
                           candidate.setup_id, None, SetupState.WAITING_FOR_RETEST.value)
        return events

    def _build_setup_candidate(self, zone: Zone, mss: MSSEvent, sweeps, pools, m5_atr: dict) -> Setup:
        created_at = max(zone.creation_timestamp, mss.confirmed_at)
        expiry_minutes = 15 * self._config.strategy.setup_expiry_m15_candles.value

        # STOP-ANCHOR-01 (docs/DECISIONS.md): the structural invalidation
        # point is the SWEPT liquidity level itself (Specification §8.1:
        # "the relevant swept low/high"), not the FVG/OB zone boundary —
        # buffered by initial_sl_buffer_atr (§8.2: 0.10 x ATR(14) of the
        # execution timeframe, PROVISIONAL value, LOCKED algorithm).
        # Reached via the existing sweep_id -> SweepEvent.liquidity_pool_id
        # -> LiquidityPool.price chain already produced earlier this same
        # step (detect_mss(sweeps, displacements) is exactly what
        # produced mss.sweep_id in the first place) — no new detection
        # logic invented, this is the same sweeps/pools every other part
        # of this step already sees.
        sweep = next((s for s in sweeps if s.sweep_id == mss.sweep_id), None)
        pool = next((p for p in pools if sweep is not None and p.pool_id == sweep.liquidity_pool_id), None)
        atr_candle_id = Candle.build_id(self._symbol, Timeframe.M5, created_at)
        atr_at_setup = m5_atr.get(atr_candle_id)
        self._last_build_ctx = (sweep, pool, atr_at_setup)  # E0: read-only stash for telemetry

        if pool is not None and atr_at_setup is not None:
            buffer = self._config.strategy.initial_sl_buffer_atr.value * atr_at_setup
            invalidation_level = (
                pool.price - buffer if zone.direction == Direction.BULLISH else pool.price + buffer
            )
        else:
            # Defensive fallback only — mss.sweep_id is always produced
            # from THIS SAME sweeps list (detect_mss(sweeps, ...) is what
            # created mss_events), so this should never actually trigger
            # in practice. Falls back to the pre-STOP-ANCHOR-01 zone-edge
            # behavior rather than guessing at a structural level that
            # can't be found, or fabricating one.
            invalidation_level = zone.lower if zone.direction == Direction.BULLISH else zone.upper

        overrides = self._scoring_factor_overrides(zone, mss)

        # Confluence detection: evaluate real SMC factors when not explicitly overridden
        h4_direction = _h4_prevailing_direction(self.state.h4_bias_state)
        d1_h4_aligned = overrides.get("d1_h4_alignment")
        if d1_h4_aligned is None:
            d1_h4_aligned = (zone.direction == h4_direction)

        target_liq = overrides.get("target_liquidity")
        if target_liq is None:
            target_liq = any(
                (p.price > zone.upper if zone.direction == Direction.BULLISH else p.price < zone.lower)
                for p in pools if p.consumed_at is None
            )

        prem_disc = overrides.get("premium_discount")
        if prem_disc is None and pool is not None:
            eq = (pool.price + zone.upper) / Decimal("2") if zone.direction == Direction.BULLISH else (pool.price + zone.lower) / Decimal("2")
            prem_disc = (zone.lower <= eq if zone.direction == Direction.BULLISH else zone.upper >= eq)

        factors = ScoringFactors(
            liquidity_sweep=True, mss=True, displacement=True,
            fvg=(zone.zone_type == ZoneType.FVG), order_block=(zone.zone_type == ZoneType.ORDER_BLOCK),
            d1_h4_alignment=bool(d1_h4_aligned),
            h1_alignment=bool(overrides.get("h1_alignment", False)),
            premium_discount=bool(prem_disc if prem_disc is not None else False),
            ote=bool(overrides.get("ote", False)),
            target_liquidity=bool(target_liq),
        )
        score, snapshot = self._scoring_engine.score(factors)
        htf_context = self._htf_context_provider(created_at, self.state.h4_bias_state)
        self._setup_zone_created[f"setup:{zone.zone_id}"] = zone.creation_timestamp

        return Setup(
            setup_id=f"setup:{zone.zone_id}", originating_zone_id=zone.zone_id,
            originating_candle_id=zone.origin_candle_id, direction=zone.direction,
            created_at=created_at, state=SetupState.WAITING_FOR_RETEST, score=score,
            score_snapshot=snapshot, htf_context=htf_context,
            expiry_at=created_at + timedelta(minutes=expiry_minutes),
            invalidation_level=invalidation_level,
        )

    # ------------------------------------------------------------
    # Setup lifecycle + touch confirmation (M5 step 7)
    # ------------------------------------------------------------

    def _update_setup_lifecycles(self, at: datetime, m5_candles: list) -> list:
        events: list = []
        if not m5_candles:
            return events
        latest_close = m5_candles[-1].close

        for setup_id, setup in list(self.state.active_setups.items()):
            if setup.state not in (SetupState.WAITING_FOR_RETEST, SetupState.WAITING_FOR_CONFIRMATION):
                continue
            updated = evaluate_setup_lifecycle(setup, at=at, latest_close=latest_close)
            if updated.state != setup.state:
                self.state.active_setups[setup_id] = updated
                event_type = (
                    EventType.SETUP_EXPIRED if updated.state == SetupState.EXPIRED
                    else EventType.SETUP_INVALIDATED
                )
                self._emit(events, f"{setup_id}:{updated.state.value}", event_type, at,
                           setup_id, setup.state.value, updated.state.value)
                if self.telemetry is not None:
                    self.telemetry.on_terminal(at, setup_id, updated.state.value, "LIFECYCLE")
        return events

    def _process_touches(self, at: datetime, m5_candles: list, m5_atr: dict, zones_by_id: dict) -> list:
        events: list = []
        for setup_id, setup in list(self.state.active_setups.items()):
            if setup.state not in (SetupState.WAITING_FOR_RETEST, SetupState.WAITING_FOR_CONFIRMATION):
                continue
            zone = zones_by_id.get(setup.originating_zone_id)
            if zone is None:
                continue

            touch = self._confirmation_engine.process_touch(
                touch_id=f"touch:{setup_id}:1", setup_id=setup_id, zone=zone,
                direction=setup.direction, candles=m5_candles, atr_by_candle_id=m5_atr,
                available_at=setup.created_at,  # PHASE-A P0.1: causality boundary
            )
            if touch is None:
                continue
            if self.telemetry is not None:
                self.telemetry.on_touch_seen(at, setup_id, touch)

            # PHASE A (audit 5.1): optional touch-confirmation timeout.
            # 0 (default, Phase A baseline) = disabled = original behaviour:
            # a touched setup may wait until the broad setup expiry.
            timeout_min = self._config.strategy.touch_confirmation_timeout_minutes.value
            if (
                timeout_min and timeout_min > 0 and touch.outcome != TouchOutcome.CONFIRMED
                and at >= touch.started_at + timedelta(minutes=timeout_min)
            ):
                if setup.state == SetupState.WAITING_FOR_RETEST:
                    validate_setup_transition(setup.state, SetupState.WAITING_FOR_CONFIRMATION)
                    setup = setup.model_copy(update={"state": SetupState.WAITING_FOR_CONFIRMATION})
                validate_setup_transition(setup.state, SetupState.EXPIRED)
                expired = setup.model_copy(update={"state": SetupState.EXPIRED})
                self.state.active_setups[setup_id] = expired
                self._emit(events, f"{setup_id}:TOUCH_TIMEOUT", EventType.SETUP_EXPIRED, at,
                           setup_id, setup.state.value, SetupState.EXPIRED.value,
                           payload={"reason_code": "TOUCH_CONFIRMATION_TIMEOUT",
                                    "touch_started_at": touch.started_at.isoformat(),
                                    "timeout_minutes": timeout_min})
                if self.telemetry is not None:
                    self.telemetry.on_terminal(at, setup_id, "EXPIRED", "TOUCH_CONFIRMATION_TIMEOUT")
                continue

            if setup.state == SetupState.WAITING_FOR_RETEST:
                validate_setup_transition(setup.state, SetupState.WAITING_FOR_CONFIRMATION)
                setup = setup.model_copy(update={"state": SetupState.WAITING_FOR_CONFIRMATION})
                self.state.active_setups[setup_id] = setup

            for ms in touch.micro_swings:
                if ms.qualification_status == MicroSwingQualification.QUALIFIED:
                    self._emit(events, ms.micro_swing_id, EventType.MICRO_SWING_CONFIRMED,
                               ms.confirmed_at, ms.micro_swing_id, None, ms.qualification_status.value)

            if touch.outcome == TouchOutcome.CONFIRMED:
                self._emit(events, f"{touch.touch_id}:CONFIRMED", EventType.TOUCH_EVENT,
                           touch.ended_at, touch.touch_id, None, TouchOutcome.CONFIRMED.value)

                setup = setup.model_copy(update={"touch_events": setup.touch_events + (touch,)})
                validate_setup_transition(setup.state, SetupState.CONFIRMED)
                setup = setup.model_copy(
                    update={"state": SetupState.CONFIRMED, "confirmation_event_id": touch.touch_id}
                )
                self.state.active_setups[setup_id] = setup
                self._emit(events, f"{setup_id}:CONFIRMED", EventType.SETUP_CONFIRMED, at,
                           setup_id, SetupState.WAITING_FOR_CONFIRMATION.value, SetupState.CONFIRMED.value)
                if self.telemetry is not None:
                    _sel = next((m for m in touch.micro_swings if m.micro_swing_id == touch.selected_micro_swing_id), None)
                    self.telemetry.on_confirmed(at, setup_id, touch, self._m5_candle_by_id.get(touch.break_candle_id), _sel)
        return events

    # ------------------------------------------------------------
    # Decision generation (M5 step 8/9) — one-open-position enforced here
    # ------------------------------------------------------------

    def _attempt_decisions(self, at: datetime, m5_candles: list, pools, sweeps, zones_by_id: dict, m5_atr: dict) -> list:
        events: list = []
        confirmed = sorted(
            (s for s in self.state.active_setups.values() if s.state == SetupState.CONFIRMED),
            key=lambda s: (s.created_at, s.setup_id),
        )
        for _idx, setup in enumerate(confirmed):
            if self.state.open_position is not None:
                if self.telemetry is not None:
                    for _parked in confirmed[_idx:]:
                        self.telemetry.on_parked(at, _parked.setup_id)
                break  # one-open-position rule, enforced at the orchestrator level
            setup, decision_events = self._attempt_decision(at, m5_candles, setup, pools, sweeps, zones_by_id, m5_atr)
            events.extend(decision_events)
        return events

    def _attempt_decision(self, at: datetime, m5_candles: list, setup: Setup, pools, sweeps, zones_by_id: dict, m5_atr: dict):
        events: list = []
        if not setup.touch_events or setup.touch_events[-1].break_candle_id is None:
            return setup, events
        break_candle_id = setup.touch_events[-1].break_candle_id
        # PERF-06: O(1) dict lookup replaces the O(N) reversed() genexpr
        # (pipeline.py:774 was the 8th-ranked hotspot in profiling, growing
        # linearly with every new candle added to the replay history).
        break_candle = self._m5_candle_by_id.get(break_candle_id)
        if break_candle is None:
            return setup, events

        if self._trading_start is not None and at < self._trading_start:
            validate_setup_transition(setup.state, SetupState.REJECTED)
            setup = setup.model_copy(update={"state": SetupState.REJECTED})
            self.state.active_setups[setup.setup_id] = setup
            self._emit(events, f"{setup.setup_id}:REJECTED", EventType.TRADE_REJECTED, at,
                       setup.setup_id, SetupState.CONFIRMED.value, SetupState.REJECTED.value,
                       payload={"reason_code": "WARMUP_PERIOD",
                                "trading_start": self._trading_start.isoformat()})
            if self.telemetry is not None:
                self.telemetry.on_warmup_reject(at, setup.setup_id)
            return setup, events

        entry_price = break_candle.close
        stop_loss = setup.invalidation_level
        atr_at_decision = m5_atr.get(break_candle.candle_id)

        # Guard: stop must be on the correct side of entry — if not, this setup's
        # structural anchor ended up above the break-candle close (e.g. because the
        # swept pool price is above current price for a bullish setup after a fast
        # continuation). Reject silently — the setup will be re-evaluated next step
        # or expire naturally. This is a structural integrity check, not a strategy gate.
        if (setup.direction.value == "BULLISH" and stop_loss >= entry_price) or (
            setup.direction.value == "BEARISH" and stop_loss <= entry_price
        ):
            if self.telemetry is not None:
                self.telemetry.on_skip(at, setup.setup_id, "STOP_ON_WRONG_SIDE_OF_ENTRY", {
                    "entry": entry_price, "stop": stop_loss, "break_candle_close": break_candle.timestamp_close})
            return setup, events

        selection = None  # E0: defined for telemetry on every path (no behavioral effect)
        consumed_pool_ids = frozenset()
        if self._tp_provider is not None:
            tp = self._tp_provider(setup, entry_price, stop_loss)
        else:
            # Real, deterministic TP1/TP2 target selection — TP1/TP2
            # Resolution Gate, approved. Reuses this SAME step's already
            # -computed pools/sweeps rather than recomputing them.
            consumed_pool_ids = frozenset(s.liquidity_pool_id for s in sweeps if s.valid)
            # ATR at the break candle drives the noise-pool filter: pools
            # less than min_target_atr * ATR from entry are excluded as
            # micro-noise (fix-zero-trade, Spec §9).
            selection = select_targets(
                setup.direction, entry_price, pools, consumed_pool_ids, at,
                atr=atr_at_decision,
                min_target_atr_mult=self._config.strategy.min_target_atr.value,
            )
            tp = (selection.take_profit_1, selection.take_profit_2) if selection is not None else None
        if tp is None:
            if self.telemetry is not None:
                self.telemetry.on_skip(at, setup.setup_id, "NO_TARGET_PAIR_YET", {
                    "entry": entry_price, "break_candle_close": break_candle.timestamp_close,
                    "targets": _telemetry_describe_targets(
                        setup.direction.value, entry_price, pools, consumed_pool_ids, at, atr_at_decision,
                        self._config.strategy.min_target_atr.value, None)})
            return setup, events  # cannot evaluate yet — retried next step
        take_profit_1, take_profit_2 = tp

        htf_context_still_valid = self._htf_context_still_valid(setup)
        risk_snapshot = self._build_risk_snapshot(at)
        # PHASE A (P0.3): size from `sizing_equity` (FIXED_RISK => initial
        # balance; COMPOUNDING => current equity); the daily-loss gate in
        # evaluate_gates still uses the real mark-to-market account_equity.
        sizing_equity = risk_snapshot.sizing_equity if risk_snapshot.sizing_equity is not None else risk_snapshot.account_equity
        sizing = self._risk_engine.compute_position_size(
            account_equity=sizing_equity, entry=entry_price, stop=stop_loss,
            tick_size=self._run_config.tick_size, tick_value=self._run_config.tick_value,
            volume_step=self._run_config.volume_step, min_volume=self._run_config.min_volume,
            max_volume=self._run_config.max_volume,
        )
        risk_decision = self._risk_engine.evaluate_gates(risk_snapshot, sizing)

        decision_result = self._decision_generator.generate(
            setup=setup, symbol=self._symbol, entry=entry_price, stop_loss=stop_loss,
            take_profit_1=take_profit_1, take_profit_2=take_profit_2,
            tp1_allocation_pct=self._config.strategy.tp1_allocation_pct.value,
            htf_context_still_valid=htf_context_still_valid, risk_decision=risk_decision,
            position_size=sizing.rounded_volume, risk_amount=sizing.risk_amount,
            risk_percentage=sizing.risk_percentage_actual, decision_timestamp=break_candle.timestamp_close,
            environment=Environment.BACKTEST,
            atr_value=atr_at_decision,
        )

        # DIAGNOSTIC-ONLY (see docs/DECISIONS.md DIAG-01): read-only
        # observation of the zone/ATR context behind this decision, for
        # BOTH outcomes — never fed back into any gate, threshold, or
        # the decision_result computed above, which already happened.
        zone = zones_by_id.get(setup.originating_zone_id)
        diagnostics: dict = {}
        if zone is not None:
            zone_width = abs(zone.upper - zone.lower)
            diagnostics["zone_upper"] = str(zone.upper)
            diagnostics["zone_lower"] = str(zone.lower)
            diagnostics["zone_width"] = str(zone_width)
            diagnostics["zone_type"] = zone.zone_type.value
            if zone_width != 0:
                # stop_loss IS the zone edge invalidation was anchored to
                # (setup.invalidation_level, set at setup creation) --
                # distance from entry to THAT edge, as a fraction of the
                # zone's own width: 0.0 = entry sits at the stop-side
                # edge, 1.0 = entry sits at the far edge. Direction-
                # agnostic by construction (abs()), so this is correct
                # for both BULLISH (stop=zone.lower) and BEARISH
                # (stop=zone.upper) setups without a separate branch.
                diagnostics["entry_position_in_zone"] = str(abs(entry_price - stop_loss) / zone_width)
        if atr_at_decision is not None:
            diagnostics["atr_at_decision"] = str(atr_at_decision)
            if atr_at_decision != 0:
                stop_distance = abs(entry_price - stop_loss)
                reward_tp1 = abs(take_profit_1 - entry_price)
                reward_tp2 = abs(take_profit_2 - entry_price)
                diagnostics["stop_distance_atr_mult"] = str(stop_distance / atr_at_decision)
                diagnostics["reward_tp1_atr_mult"] = str(reward_tp1 / atr_at_decision)
                diagnostics["reward_tp2_atr_mult"] = str(reward_tp2 / atr_at_decision)

        if self.telemetry is not None:
            self.telemetry.on_decision({
                "at": at, "setup": setup, "zone": zone, "break_candle": break_candle,
                "break_candle_close": break_candle.timestamp_close, "entry": entry_price, "stop": stop_loss,
                "atr": atr_at_decision, "tp1": take_profit_1, "tp2": take_profit_2, "selection": selection,
                "pools": pools, "consumed": consumed_pool_ids, "min_target_atr": self._config.strategy.min_target_atr.value,
                "htf_valid": htf_context_still_valid, "h4_state": self.state.h4_bias_state.current_state,
                "risk_snapshot": risk_snapshot, "sizing": sizing, "risk_decision": risk_decision, "result": decision_result,
                "generator": self._decision_generator, "tp1_allocation_pct": self._config.strategy.tp1_allocation_pct.value,
            })

        if isinstance(decision_result, TradeDecisionRejection):
            validate_setup_transition(setup.state, SetupState.REJECTED)
            setup = setup.model_copy(update={"state": SetupState.REJECTED})
            self.state.active_setups[setup.setup_id] = setup
            self._emit(events, f"{setup.setup_id}:REJECTED", EventType.TRADE_REJECTED, at,
                       setup.setup_id, SetupState.CONFIRMED.value, SetupState.REJECTED.value,
                       payload={"reason_code": decision_result.reason_code, **decision_result.details, **diagnostics})
            return setup, events

        decision = decision_result
        validate_setup_transition(setup.state, SetupState.TRADE_APPROVED)
        setup = setup.model_copy(
            update={"state": SetupState.TRADE_APPROVED, "trade_decision_id": decision.decision_id}
        )
        self.state.active_setups[setup.setup_id] = setup
        self._decision_setup_map[decision.decision_id] = setup.setup_id
        self._emit(events, decision.decision_id, EventType.TRADE_APPROVED, at, setup.setup_id,
                   SetupState.CONFIRMED.value, SetupState.TRADE_APPROVED.value, payload=diagnostics)

        order = self._execution_adapter.submit_market_order(decision)
        self._emit(events, order.order_id, EventType.ORDER_FILLED, at, order.order_id,
                   None, order.state.value)

        position = Position(
            position_id=f"position:{decision.decision_id}", decision_id=decision.decision_id,
            symbol=decision.symbol, direction=decision.direction, volume=decision.position_size,
            entry_price=order.avg_fill_price, stop_loss=decision.stop_loss,
            take_profit_1=decision.take_profit_1, take_profit_2=decision.take_profit_2,
            protection_state=ProtectionState.PROTECTED,
            lifecycle_state=PositionLifecycleState.POSITION_ACTIVE, opened_at=decision.decision_timestamp,
        )
        self._emit(events, f"{position.position_id}:PROTECTED", EventType.PROTECTED, at,
                   position.position_id, None, ProtectionState.PROTECTED.value)

        self.state.open_position = position
        self.state.open_position_phase = "PRE_TP1"
        self.state.trailing_current_sl = None
        self.state.original_volume = position.volume
        self.state.tp1_leg = None
        self.state.tp1_split = None
        self.state.tp1_exit_timestamp = None
        if self._ledger is not None:
            self._ledger.on_trade_opened(at)
            self._balance_before_trade[position.decision_id] = self._ledger.balance
        return setup, events

    def _build_risk_snapshot(self, at: datetime) -> RiskSnapshot:
        """Risk state for the next decision. Ledger-backed when a ledger
        is supplied (PHASE A), legacy provider otherwise."""
        if self._ledger is None:
            return self._risk_snapshot_provider(at)
        ls = self._ledger.snapshot(at)  # rolls the UTC day if a boundary was crossed
        return RiskSnapshot(
            snapshot_id=f"r:{at.isoformat()}", timestamp=at,
            account_equity=ls.account_equity, daily_starting_equity=ls.daily_starting_equity,
            daily_realized_pnl=ls.daily_realized_pnl,
            consecutive_losses_count=ls.consecutive_losses_count,
            trades_today_count=ls.trades_today_count, sizing_equity=ls.sizing_equity,
        )

    # ------------------------------------------------------------
    # Open-position management (M5 step 10) — exit sees ONLY this
    # step's new candle, trailing uses ONLY swings confirmed by T
    # ------------------------------------------------------------

    def _manage_open_position(self, at: datetime, m5_candles: list):
        events: list = []
        position = self.state.open_position
        if position is None or not m5_candles:
            return events, None

        current_candle = m5_candles[-1]
        phase = self.state.open_position_phase

        tp1 = position.take_profit_1 if phase == "PRE_TP1" else None
        current_sl = self.state.trailing_current_sl if phase == "POST_TP1" else position.stop_loss

        exit_check = self._find_exit(position, current_sl, tp1, position.take_profit_2, current_candle)

        full_close_at_tp1 = False
        if exit_check is not None and exit_check.reason == "TP1_HIT" and phase == "PRE_TP1":
            # PHASE A (P0.2): TP1 genuinely closes tp1_allocation_pct of the
            # position. Quantity is explicit; the remainder is managed
            # independently (trailing / TP2) with its own volume.
            tick_size = self._run_config.tick_size
            tick_value = self._run_config.tick_value
            split = None
            if position.volume is not None and position.volume > Decimal("0"):
                split = split_tp1_volume(
                    position.volume, self._config.strategy.tp1_allocation_pct.value,
                    self._run_config.volume_step, self._run_config.min_volume,
                )
                if split.executed:
                    leg = leg_pnl(
                        position.direction == Direction.BULLISH, position.entry_price, exit_check.exit_price,
                        split.tp1_volume, tick_size, tick_value, self._execution_adapter.commission_per_lot,
                    )
                    self.state.tp1_leg = leg
                    self.state.tp1_exit_timestamp = current_candle.timestamp_close
                    if self._ledger is not None:
                        self._ledger.on_realized(at, leg.net_pnl)
                    if split.remaining_volume > Decimal("0"):
                        position = position.model_copy(update={"volume": split.remaining_volume})
                    else:
                        full_close_at_tp1 = True  # 100% allocation: TP1 ends the trade
            self.state.tp1_split = split

            if full_close_at_tp1:
                self._emit(events, f"{position.position_id}:TP1", EventType.TP1_EXECUTED, at,
                           position.position_id, None, "TP1_EXECUTED")
                # exit_check stays TP1_HIT and is finalized below.
            else:
                self.state.open_position_phase = "POST_TP1"
                self.state.trailing_current_sl = position.stop_loss
                position = position.model_copy(
                    update={"lifecycle_state": PositionLifecycleState.POSITION_PARTIALLY_CLOSED}
                )
                self.state.open_position = position
                self._emit(events, f"{position.position_id}:TP1", EventType.TP1_EXECUTED, at,
                           position.position_id, None, "TP1_EXECUTED")
                phase = "POST_TP1"
                exit_check = None  # TP1 alone never closes the position (unless 100% allocation)

        if phase == "POST_TP1":
            current_atr_map = self._atr_provider(Timeframe.M5, m5_candles)
            current_atr = current_atr_map.get(current_candle.candle_id)
            if current_atr is not None:
                # TRAIL-01 (docs/DECISIONS.md): bound the swing-detection
                # window to recent candles rather than the full replay-to-
                # date history. See TRAILING_SWING_LOOKBACK_MARGIN's
                # docstring above for the full rationale. The correctness
                # guarantee itself lives inside compute_new_stop (it rejects
                # any candidate the live candle would already breach,
                # regardless of what window it's given); this bound is a
                # secondary measure that also removes a full-history
                # rescan cost on every POST_TP1 step.
                entry_idx = bisect.bisect_left(
                    m5_candles, position.opened_at, key=lambda c: c.timestamp_close
                )
                window_start = max(0, entry_idx - TRAILING_SWING_LOOKBACK_MARGIN)
                candles_for_trailing = m5_candles[window_start:]
                new_sl = self._trailing_engine.compute_new_stop(
                    position.direction, self.state.trailing_current_sl,
                    candles_for_trailing, current_atr_map, current_atr,
                )
                if new_sl is not None:
                    if position.direction == Direction.BULLISH:
                        assert new_sl >= self.state.trailing_current_sl, "trailing stop must never loosen (long)"
                    else:
                        assert new_sl <= self.state.trailing_current_sl, "trailing stop must never loosen (short)"
                    self.state.trailing_current_sl = new_sl
                    self._emit(events, f"{position.position_id}:trail:{current_candle.candle_id}",
                               EventType.TRAILING_STOP_UPDATED, at, position.position_id, None, str(new_sl))

            if exit_check is None:
                exit_check = self._find_exit(
                    position, self.state.trailing_current_sl, None,
                    position.take_profit_2, current_candle,
                )

        if exit_check is None:
            return events, None

        reason = "TRAILING_STOP_HIT" if (phase == "POST_TP1" and exit_check.reason == "SL_HIT") else exit_check.reason

        # PHASE A (P0.2/P0.3) final accounting: every closed leg is realized
        # on the volume it actually closed; R keeps the ORIGINAL full-position
        # risk denominator, so a 30/70 trade's R is the true weighted result.
        tick_size = self._run_config.tick_size
        tick_value = self._run_config.tick_value
        commission_per_lot = self._execution_adapter.commission_per_lot
        is_long = position.direction == Direction.BULLISH
        original_volume = self.state.original_volume if self.state.original_volume is not None else position.volume

        commission_total = Decimal("0")
        gross_pnl = None
        net_pnl = None
        dollar_risk = None
        realized_r = None

        legs: list = []
        if self.state.tp1_leg is not None:
            legs.append(self.state.tp1_leg)
        final_leg = None
        if not full_close_at_tp1 and position.volume is not None and position.volume > Decimal("0"):
            final_leg = leg_pnl(
                is_long, position.entry_price, exit_check.exit_price, position.volume,
                tick_size, tick_value, commission_per_lot,
            )
            legs.append(final_leg)

        if legs and original_volume is not None and original_volume > Decimal("0") and tick_size > Decimal("0"):
            dollar_risk = initial_dollar_risk(
                position.entry_price, position.stop_loss, original_volume, tick_size, tick_value,
            )
            combined = combine_legs(legs, dollar_risk)
            commission_total = combined.commission
            gross_pnl = combined.gross_pnl
            net_pnl = combined.net_pnl
            if dollar_risk > Decimal("0"):
                realized_r = combined.realized_r
            if self._ledger is not None:
                if final_leg is not None:
                    self._ledger.on_realized(at, final_leg.net_pnl)
                self._ledger.on_trade_closed(at, combined.net_pnl)

        if realized_r is None:
            # No usable volume/risk (sizing unavailable): price-only R, as before.
            realized_r = compute_realized_r(
                position.direction, position.entry_price, position.stop_loss, exit_check.exit_price,
            )
            if self._ledger is not None and net_pnl is None:
                self._ledger.on_trade_closed(at, realized_r)

        self._emit(events, f"{position.position_id}:CLOSED", EventType.POSITION_CLOSED, at,
                   position.position_id, position.lifecycle_state.value, PositionLifecycleState.POSITION_CLOSED.value)

        split = self.state.tp1_split
        _su = self.state.active_setups.get(self._decision_setup_map.get(position.decision_id, ""))
        trade = TradeRecord(
            trade_id=f"trade:{position.decision_id}", decision_id=position.decision_id,
            setup_id=self._decision_setup_map.get(position.decision_id, ""),
            config_hash=self._config_hash, direction=position.direction,
            entry_timestamp=position.opened_at, entry_price=position.entry_price, stop_loss=position.stop_loss,
            take_profit_1=position.take_profit_1, take_profit_2=position.take_profit_2,
            exit_timestamp=current_candle.timestamp_close, exit_price=exit_check.exit_price,
            exit_reason=reason, realized_r=realized_r,
            cost_model=self._execution_adapter.cost_model,
            position_size=original_volume,
            dollar_risk=dollar_risk,
            gross_pnl=gross_pnl,
            commission=commission_total if commission_total > Decimal("0") else None,
            net_pnl=net_pnl,
            tp1_executed=(split.executed if split is not None else None),
            tp1_exit_price=(self.state.tp1_leg.exit_price if self.state.tp1_leg is not None else None),
            tp1_exit_timestamp=self.state.tp1_exit_timestamp,
            tp1_volume=(self.state.tp1_leg.volume if self.state.tp1_leg is not None else None),
            runner_volume=(final_leg.volume if final_leg is not None else None),
            tp1_split_reason=(split.reason if split is not None else None),
            engine=self._m5_engine,
            zone_creation_timestamp=self._setup_zone_created.get(self._decision_setup_map.get(position.decision_id, "")),
            setup_created_at=(_su.created_at if _su is not None else None),
            setup_expiry_at=(_su.expiry_at if _su is not None else None),
            setup_score=(_su.score if _su is not None else None),
            touch_started_at=(_su.touch_events[-1].started_at if _su is not None and _su.touch_events else None),
            confirmation_timestamp=(_su.touch_events[-1].ended_at if _su is not None and _su.touch_events else None),
            balance_before=self._balance_before_trade.get(position.decision_id),
            balance_after=(self._ledger.balance if self._ledger is not None else None),
            spread_assumption=self._execution_adapter.spread,
            slippage_assumption=self._execution_adapter.slippage,
            commission_per_lot_assumption=self._execution_adapter.commission_per_lot,
            entry_reason="SETUP_CONFIRMED",
        )

        self.state.original_volume = None
        self.state.tp1_leg = None
        self.state.tp1_split = None
        self.state.tp1_exit_timestamp = None
        self.state.open_position = None
        self.state.open_position_phase = None
        self.state.trailing_current_sl = None
        return events, trade
