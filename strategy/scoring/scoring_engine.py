"""Confluence scoring engine. Phase 2 §17's 100-point model.

Milestone 5 review finding, fixed: weights now come exclusively from
core.config.registry.ScoringWeights — they are NOT duplicated here.
Every weight is PROVISIONAL/testable except session_alignment, which is
DEFERRED (session boundaries still undecided — see liquidity_engine.py).

Score is PROVISIONAL end to end (weights AND the minimum_score threshold)
per Implementation Spec v1.0 §5's locked decision: computed once at
setup creation, never recomputed, and never treated as validated until
Phase 3 ablation testing says otherwise."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.config.registry import ScoringWeights


@dataclass(frozen=True)
class ScoringFactors:
    """Boolean presence of each confluence factor for one setup
    evaluation. Constructing this is the caller's job — the scoring
    engine only sums weights, it never decides whether a factor is
    present (that's structure/liquidity/zone engine territory)."""

    d1_h4_alignment: bool
    h1_alignment: bool
    premium_discount: bool
    liquidity_sweep: bool
    mss: bool
    displacement: bool
    fvg: bool
    order_block: bool
    ote: bool
    target_liquidity: bool
    session_alignment: bool = False  # always False — session boundaries undecided


class ScoringEngineImpl:
    """Concrete implementation of core.contracts.detectors.ScoringEngine."""

    def __init__(self, weights: ScoringWeights) -> None:
        self._weights = weights

    def score(self, factors: ScoringFactors) -> tuple[Decimal, dict[str, Decimal]]:
        """Returns (total_score, score_snapshot) — the snapshot is what
        Setup.score_snapshot stores, per the LOCKED decision that score
        is computed once and the breakdown preserved for later Phase 3
        ablation analysis (Implementation Spec v1.0 §5)."""

        field_names = (
            "d1_h4_alignment", "h1_alignment", "premium_discount", "liquidity_sweep",
            "mss", "displacement", "fvg", "order_block", "ote", "session_alignment",
            "target_liquidity",
        )
        snapshot: dict[str, Decimal] = {}
        total = Decimal("0")
        for field_name in field_names:
            present = getattr(factors, field_name)
            weight = getattr(self._weights, field_name).value
            awarded = weight if present else Decimal("0")
            snapshot[field_name] = awarded
            total += awarded
        return total, snapshot
