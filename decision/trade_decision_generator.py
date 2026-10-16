"""Trade Decision generator — the single seam between Strategy and
Execution (Execution FROZEN v1.0 §4/§8, Technical Architecture v1.0).

HONEST SCOPE GAP, FLAGGED NOT HIDDEN: Strategy FROZEN v1.0 §9 defines
TP1/TP2 as 'meaningful opposing liquidity that is structurally relevant,
reachable, and not already consumed' — this requires ranking and
selecting among LiquidityPool objects at decision time, which is its
own piece of logic that hasn't been built in any milestone so far (it
wasn't explicitly scoped into Milestones 1-5 by name). Rather than
invent a target-selection algorithm here to fill the gap silently, this
generator accepts entry/stop/TP1/TP2 as ALREADY-COMPUTED inputs — the
caller (a future target-selection component, or a test) is responsible
for supplying them. This keeps the module honest about what it does
(gate-checking and TradeDecision assembly) versus what it doesn't yet
do (target selection).

Hard gates checked here (Phase 2 §16, re-verified per T12 at approval
time, not just at setup creation):
  - STOP_TOO_WIDE (Spec §8.3): if structural SL distance > max_stop_atr * ATR,
    skip the trade. Oversized stops indicate a mismatched structural
    anchor or an inefficient zone entry and are not worth the unfavourable
    risk geometry. Gate fires before RR check (fast-reject).
  - minimum_rr — RR-01 LOCKED (docs/DECISIONS.md): a BLENDED R:R across
    TP1 and TP2, weighted by tp1_allocation_pct, not stop-to-TP1 alone.
    This module's own docstring already flagged "stop-to-TP1 or blended
    R:R, per PROVISIONAL config" as the two candidate interpretations
    before this was decided — RR-01 resolves that to blended.
  - risk engine approval (RiskDecision.approved)
  - score >= minimum_score
  - HTF continuation context still valid (caller-supplied, since this
    generator has no independent access to live H4 bias state)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from core.domain.common import Direction, Environment, ensure_utc
from core.domain.decision import EntryType, TradeDecision
from core.domain.risk import RiskDecision
from core.domain.setups import Setup


class TradeDecisionRejection:
    """Not a domain contract — a lightweight local result type so the
    generator can report WHY it declined without constructing a
    TradeDecision it would then have to discard. Callers translate this
    into the SETUP -> REJECTED transition and appropriate reason code.

    `details`, when given, carries whatever numeric context is useful
    for understanding WHY a specific gate failed (e.g. the actual
    computed R:R vs the threshold) — added after a real backtest run
    showed a reason_code alone (e.g. "RR_BELOW_MINIMUM") gives no way
    to tell a marginal near-miss from a wildly-failing setup without
    re-deriving the numbers by hand. Purely observational — never read
    back into any gate logic."""

    def __init__(self, reason_code: str, details: dict | None = None) -> None:
        self.reason_code = reason_code
        self.details = details or {}


class TradeDecisionGeneratorImpl:
    """Concrete implementation of core.contracts.detectors.TradeDecisionGenerator
    (Protocol signature will need extending, same pattern as the
    Milestone 2 H4WarningEngine fix — flagging for the Milestone 5
    review gate rather than silently diverging)."""

    def __init__(
        self, strategy_version: str, config_hash: str, minimum_rr: Decimal,
        minimum_score: int, decision_expiry_seconds: int,
        max_stop_atr: Optional[Decimal] = None,
        min_stop_atr: Optional[Decimal] = None,
    ) -> None:
        self._strategy_version = strategy_version
        self._config_hash = config_hash
        self._minimum_rr = minimum_rr
        self._minimum_score = minimum_score
        self._decision_expiry_seconds = decision_expiry_seconds
        self._max_stop_atr = max_stop_atr
        self._min_stop_atr = min_stop_atr

    def generate(
        self,
        setup: Setup,
        symbol: str,
        entry: Decimal,
        stop_loss: Decimal,
        take_profit_1: Decimal,
        take_profit_2: Decimal,
        tp1_allocation_pct: Decimal,
        htf_context_still_valid: bool,
        risk_decision: RiskDecision,
        position_size: Decimal,
        risk_amount: Decimal,
        risk_percentage: Decimal,
        decision_timestamp: datetime,
        environment: Environment,
        atr_value: Optional[Decimal] = None,
    ) -> TradeDecision | TradeDecisionRejection:
        """`symbol` is an explicit parameter rather than derived from
        `setup` — Setup (core/domain/setups.py) has no symbol field, and
        zone_id's actual format (see strategy/zones/zone_engine.py,
        e.g. 'fvg:bullish:{candle_id}') doesn't encode it either. Caller
        supplies it directly rather than this generator guessing at a
        string format that doesn't actually hold the data."""
        decision_timestamp = ensure_utc(decision_timestamp)

        # Hard gates, checked in a fixed order so the FIRST failing gate
        # is always the reported reason — never an arbitrary one.
        if not htf_context_still_valid:
            return TradeDecisionRejection("HTF_CONTEXT_INVALID")

        # STOP_BELOW_MINIMUM gate (PHASE A, owner decision 2026-10-03): the
        # configured minimum stop (0.25 ATR) is ENFORCED by REJECTING the
        # setup. The stop is never clamped/widened: changing the stop after
        # the strategy produced it would change the intended risk structure.
        if self._min_stop_atr is not None and atr_value is not None and atr_value > 0:
            stop_distance = abs(entry - stop_loss)
            min_stop_distance = self._min_stop_atr * atr_value
            if stop_distance < min_stop_distance:
                return TradeDecisionRejection("STOP_BELOW_MINIMUM", details={
                    "stop_distance": str(stop_distance),
                    "min_stop_distance": str(min_stop_distance),
                    "min_stop_atr": str(self._min_stop_atr),
                    "atr_value": str(atr_value),
                    "entry": str(entry), "stop_loss": str(stop_loss),
                })

        # STOP_TOO_WIDE gate (Spec §8.3): reject before RR check to fast-fail
        # setups with an oversized stop distance (mismatched zone anchor).
        if self._max_stop_atr is not None and atr_value is not None and atr_value > 0:
            stop_distance = abs(entry - stop_loss)
            max_stop_distance = self._max_stop_atr * atr_value
            if stop_distance > max_stop_distance:
                return TradeDecisionRejection("STOP_TOO_WIDE", details={
                    "stop_distance": str(stop_distance),
                    "max_stop_distance": str(max_stop_distance),
                    "max_stop_atr": str(self._max_stop_atr),
                    "atr_value": str(atr_value),
                    "entry": str(entry), "stop_loss": str(stop_loss),
                })

        # TP2_NOT_BEYOND_TP1 guard (PHASE A, audit 5.7 / owner decision 9):
        # REJECTION ONLY -- target selection itself is NOT redesigned in
        # Phase A (selecting "the next valid external pool" is deferred).
        # TP2 must not be CLOSER to entry than TP1 (owner's Phase A wording:
        # "TP2 must not be closer than TP1"). TP2 == TP1 is therefore allowed
        # (OPEN OWNER DECISION: an earlier note asked for strictly beyond;
        # equality is permitted here because it is the narrower change and
        # existing fixtures use it -- see CHANGELOG_PHASE_A.md).
        tp2_closer = (take_profit_2 < take_profit_1) if setup.direction == Direction.BULLISH else (take_profit_2 > take_profit_1)
        if tp2_closer:
            return TradeDecisionRejection("TP2_NOT_BEYOND_TP1", details={
                "take_profit_1": str(take_profit_1), "take_profit_2": str(take_profit_2),
                "direction": setup.direction.value,
            })

        rr_tp1 = self._compute_rr(setup.direction, entry, stop_loss, take_profit_1)
        rr_tp2 = self._compute_rr(setup.direction, entry, stop_loss, take_profit_2)
        rr = self._compute_blended_rr(
            setup.direction, entry, stop_loss, take_profit_1, take_profit_2, tp1_allocation_pct,
        )
        if rr < self._minimum_rr:
            return TradeDecisionRejection("RR_BELOW_MINIMUM", details={
                "blended_rr": str(rr), "rr_tp1": str(rr_tp1), "rr_tp2": str(rr_tp2),
                "minimum_rr": str(self._minimum_rr), "tp1_allocation_pct": str(tp1_allocation_pct),
                "entry": str(entry), "stop_loss": str(stop_loss),
                "take_profit_1": str(take_profit_1), "take_profit_2": str(take_profit_2),
            })

        if setup.score < self._minimum_score:
            return TradeDecisionRejection("SCORE_BELOW_THRESHOLD")

        if not risk_decision.approved:
            return TradeDecisionRejection(risk_decision.reason_code)

        # Milestone 5 review finding, fixed: previously only setup_id was
        # captured here. originating_event_ids should trace the chain of
        # events actually known to produce this decision — everything the
        # Setup itself directly references. Full transitive traceability
        # (zone -> MSS -> sweep/displacement) still requires walking those
        # links via Zone.linked_mss_id etc., since Setup doesn't flatten
        # the entire upstream chain into its own fields — that's a
        # normalized-reference design choice, not a gap, but this
        # generator should still record everything Setup DOES carry.
        originating_ids = tuple(
            x for x in (
                setup.setup_id, setup.originating_zone_id,
                setup.originating_candle_id, setup.confirmation_event_id,
            ) if x is not None
        )

        decision_id = f"decision:{setup.setup_id}:{decision_timestamp.isoformat()}"
        return TradeDecision(
            decision_id=decision_id,
            strategy_version=self._strategy_version,
            config_hash=self._config_hash,
            setup_id=setup.setup_id,
            symbol=symbol,
            direction=setup.direction,
            entry_type=EntryType.MARKET,
            intended_entry_price=entry,
            execution_price_policy=Decimal("0.10"),  # max_acceptable_slippage_atr, OD4-locked default
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            tp_allocation=(tp1_allocation_pct, Decimal("100") - tp1_allocation_pct),
            position_size=position_size,
            risk_amount=risk_amount,
            risk_percentage=risk_percentage,
            decision_timestamp=decision_timestamp,
            decision_expiry=decision_timestamp + timedelta(seconds=self._decision_expiry_seconds),
            originating_event_ids=originating_ids,
            score=setup.score,
            environment=environment,
            idempotency_key=decision_id,
        )

    @staticmethod
    def _compute_rr(direction: Direction, entry: Decimal, stop: Decimal, tp1: Decimal) -> Decimal:
        risk = abs(entry - stop)
        if risk == 0:
            return Decimal("0")
        reward = abs(tp1 - entry)
        return reward / risk

    @classmethod
    def _compute_blended_rr(
        cls, direction: Direction, entry: Decimal, stop: Decimal,
        take_profit_1: Decimal, take_profit_2: Decimal, tp1_allocation_pct: Decimal,
    ) -> Decimal:
        """RR-01 LOCKED (docs/DECISIONS.md): TP1 is deliberately the
        NEAREST internal liquidity pool (strategy/targets/target_selector.py)
        — close by design, which structurally caps its own R:R around
        1:1-1:1.5 regardless of setup quality (external SMC/ICT research
        cross-checked at decision time, cited in docs/DECISIONS.md
        RR-01). Gating the whole trade on that number alone means
        requiring a "big" result from a target chosen specifically for
        being close, not for offering a big reward — asking it to do a
        job it was never designed for. Blending with TP2 (the further,
        external-liquidity target, where larger R:R multiples actually
        come from) weighted by the SAME tp1_allocation_pct split already
        used for position sizing reflects the trade's REAL expected
        outcome as an integrated whole, rather than only its most
        conservative leg."""
        rr_tp1 = cls._compute_rr(direction, entry, stop, take_profit_1)
        rr_tp2 = cls._compute_rr(direction, entry, stop, take_profit_2)
        tp1_weight = tp1_allocation_pct / Decimal("100")
        tp2_weight = Decimal("1") - tp1_weight
        return (tp1_weight * rr_tp1) + (tp2_weight * rr_tp2)
