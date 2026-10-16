"""Deterministic TP1/TP2 target selection.

Implements the three decisions locked at the TP1/TP2 Resolution Gate
(see project chat log) — nothing here was invented independently of
that approval:

  DECISION 1 (consumption): a pool is consumed once a CONFIRMED
  SweepEvent exists against it — "confirmed" means the sweep engine's
  own `valid` field is True. This reuses the existing sweep detector
  and its existing domain ID (`liquidity_pool_id`); no second
  consumption mechanism is introduced.

  DECISION 2 (swing classification): SWING_HIGH/SWING_LOW pools are
  EXTERNAL when they came from a major swing, INTERNAL when minor,
  using LiquidityPool.is_major_swing — additive information the
  swing/structure system already produced (SwingPoint.major_flag) and
  now simply preserves. The frozen swing-detection algorithm itself is
  untouched.

  DECISION 3 (TP2 = "primary"): defined as NEAREST valid external
  opposing liquidity (approved Option 4) — explicitly NOT the existing
  LiquidityPool.score, and NOT a new weighted hierarchy.

TP1 = nearest valid INTERNAL opposing liquidity pool, ahead of entry.
TP2 = nearest valid EXTERNAL opposing liquidity pool, ahead of entry.
Both measured as `abs(pool.price - entry)`, exactly as approved.

ROUND_NUMBER pools are excluded from target eligibility entirely —
Discovery §3: "Round numbers may be logged as contextual information
but are not mandatory liquidity targets." SESSION_HIGH/SESSION_LOW
classify as INTERNAL per Discovery §3, but currently never appear in
practice (strategy/liquidity/liquidity_engine.py's map_session_pools()
is a disclosed, separate, pre-existing stub) — classifying them here is
forward-looking and has no effect until that gap is resolved elsewhere.

A pool whose swing major/minor status is somehow unknown (never
expected — the liquidity engine always sets it for swing pools) is
excluded rather than guessed at either category.

DISCOVERED EDGE CASE, NOT FIXED (not authorized by the approved
decisions): TP1 (nearest internal) and TP2 (nearest external) are
selected independently. It is mathematically possible, for an unusual
pool configuration, for TP2 to end up nearer to entry than TP1. No
cross-category ordering constraint was approved, so none is applied.

Tie-breaking: if two eligible pools sit at the exact same distance from
entry, the one with the lexicographically smaller pool_id is chosen.
This is NOT a trading rule — it exists solely so identical input always
produces identical output (Phase 2 §1: "deterministic outputs") — and
only matters in the literal case of an exact tie.

NOISE POOL FILTERING (fix-zero-trade): pools that are less than
`min_target_atr_mult * atr` distance from entry are now excluded as
noise. On M5 XAUUSD with ATR ≈ 8 pts, the default 1.0x ATR threshold
means only pools ≥ 8 pts from entry qualify as TP candidates.

Diagnostic runs showed that without this filter, select_targets()
picked equal-highs just $0.50 away (0.06x ATR) as TP1, producing
blended R:R of 0.07:1 against a $18 structural stop — guaranteeing
100% rejection at the RR gate. This filter implements the spec's
intent: "meaningful opposing liquidity" (Spec §9, §9.1, §9.2) rather
than the mechanically nearest tick-level noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import FrozenSet, Optional, Sequence

from core.domain.common import Direction
from core.domain.liquidity import LiquidityPool, LiquidityPoolType

INTERNAL = "INTERNAL"
EXTERNAL = "EXTERNAL"

_EXTERNAL_TYPES = frozenset({
    LiquidityPoolType.PDH, LiquidityPoolType.PDL,
    LiquidityPoolType.PWH, LiquidityPoolType.PWL,
})
_INTERNAL_TYPES = frozenset({
    LiquidityPoolType.EQUAL_HIGH, LiquidityPoolType.EQUAL_LOW,
    LiquidityPoolType.SESSION_HIGH, LiquidityPoolType.SESSION_LOW,
})
_SWING_TYPES = frozenset({LiquidityPoolType.SWING_HIGH, LiquidityPoolType.SWING_LOW})
_HIGH_TYPES = frozenset({
    LiquidityPoolType.PDH, LiquidityPoolType.PWH, LiquidityPoolType.SWING_HIGH,
    LiquidityPoolType.EQUAL_HIGH, LiquidityPoolType.SESSION_HIGH,
})
_LOW_TYPES = frozenset({
    LiquidityPoolType.PDL, LiquidityPoolType.PWL, LiquidityPoolType.SWING_LOW,
    LiquidityPoolType.EQUAL_LOW, LiquidityPoolType.SESSION_LOW,
})


def classify(pool: LiquidityPool) -> Optional[str]:
    """Returns INTERNAL, EXTERNAL, or None (not target-eligible at all)."""
    if pool.type in _EXTERNAL_TYPES:
        return EXTERNAL
    if pool.type in _INTERNAL_TYPES:
        return INTERNAL
    if pool.type in _SWING_TYPES:
        if pool.is_major_swing is True:
            return EXTERNAL
        if pool.is_major_swing is False:
            return INTERNAL
        return None  # major/minor unknown -- exclude rather than guess
    return None  # ROUND_NUMBER or any other non-target type


@dataclass(frozen=True)
class TargetSelectionResult:
    take_profit_1: Decimal
    take_profit_2: Decimal
    tp1_pool_id: str
    tp2_pool_id: str


def select_targets(
    direction: Direction,
    entry: Decimal,
    pools: Sequence[LiquidityPool],
    consumed_pool_ids: FrozenSet[str],
    at: datetime,
    atr: Optional[Decimal] = None,
    min_target_atr_mult: Decimal = Decimal("0"),
) -> Optional[TargetSelectionResult]:
    """Returns None if no eligible INTERNAL pool or no eligible
    EXTERNAL pool exists — the caller treats None exactly as an
    unresolved target provider: the setup cannot be evaluated yet and
    is retried the next step, never forced to a fallback value.

    `atr` and `min_target_atr_mult` enable noise-pool filtering:
    pools closer than `min_target_atr_mult * atr` to entry are excluded
    as they represent intraday tick noise rather than meaningful opposing
    liquidity (Spec §9: "meaningful opposing liquidity"). When atr is
    None or min_target_atr_mult is zero, no filter is applied
    (backwards-compatible with all existing call sites and tests).
    """

    side_types = _HIGH_TYPES if direction == Direction.BULLISH else _LOW_TYPES

    # Compute minimum distance threshold if ATR is provided
    min_distance: Optional[Decimal] = None
    if atr is not None and atr > 0 and min_target_atr_mult > 0:
        min_distance = min_target_atr_mult * atr

    internal_candidates: list[LiquidityPool] = []
    external_candidates: list[LiquidityPool] = []

    for pool in pools:
        if pool.type not in side_types:
            continue  # directionally inappropriate
        if pool.pool_id in consumed_pool_ids:
            continue  # Decision 1 — already consumed
        if pool.created_at > at:
            continue  # defensive: must already be excluded upstream, never trusted silently
        ahead = pool.price > entry if direction == Direction.BULLISH else pool.price < entry
        if not ahead:
            continue  # must lie ahead of entry in the trade direction

        # Noise-pool filter: exclude pools too close to entry to represent
        # meaningful opposing liquidity (fix-zero-trade, Spec §9).
        if min_distance is not None:
            distance = abs(pool.price - entry)
            if distance < min_distance:
                continue  # micro-noise — skip rather than target

        category = classify(pool)
        if category == INTERNAL:
            internal_candidates.append(pool)
        elif category == EXTERNAL:
            external_candidates.append(pool)

    tp1_pool = _nearest(internal_candidates, entry)
    tp2_pool = _nearest(external_candidates, entry)
    if tp1_pool is None or tp2_pool is None:
        return None

    return TargetSelectionResult(
        take_profit_1=tp1_pool.price, take_profit_2=tp2_pool.price,
        tp1_pool_id=tp1_pool.pool_id, tp2_pool_id=tp2_pool.pool_id,
    )


def _nearest(candidates: Sequence[LiquidityPool], entry: Decimal) -> Optional[LiquidityPool]:
    if not candidates:
        return None
    return min(candidates, key=lambda p: (abs(p.price - entry), p.pool_id))
