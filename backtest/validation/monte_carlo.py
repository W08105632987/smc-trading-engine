"""Seeded Monte Carlo resampling of realized trade R-multiples.
Milestone 9, Section E.

This is a standard bootstrap (resample-with-replacement) over a
FIXED, ALREADY-REALIZED set of trade outcomes — it answers "how much
does the ORDER of these same trades matter for drawdown," not "what
would the strategy have done differently." No trading rule, threshold,
or parameter is touched. It cannot manufacture a result the real
backtest didn't already produce: mean_final_r across enough iterations
converges to `len(trade_r_multiples) * mean(trade_r_multiples)`,
exactly what the trades already summed to.

DETERMINISM / GLOBAL STATE: uses a LOCAL `random.Random(seed)`
instance. It never calls the `random` module's free functions, so it
never reads or perturbs Python's global random state — a caller
running other randomized code (or another Monte Carlo run) elsewhere
in the same process is completely unaffected, and the same seed always
reproduces the same result.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class MonteCarloResult:
    iterations: int
    seed: int
    trade_count: int
    final_r_percentiles: dict  # dict[int, Decimal]
    max_drawdown_r_percentiles: dict  # dict[int, Decimal]
    mean_final_r: Decimal
    mean_max_drawdown_r: Decimal


def run_monte_carlo(
    trade_r_multiples: Sequence[Decimal], iterations: int, seed: int,
    percentiles: Sequence[int] = (5, 25, 50, 75, 95),
) -> MonteCarloResult:
    if not trade_r_multiples:
        raise ValueError("cannot run Monte Carlo on an empty trade series")
    if iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    for p in percentiles:
        if not (0 <= p <= 100):
            raise ValueError(f"percentile {p} out of range [0, 100]")

    rng = random.Random(seed)  # local instance — never touches global random state
    n = len(trade_r_multiples)

    final_rs: list = []
    max_dds: list = []
    for _ in range(iterations):
        cumulative = Decimal("0")
        peak = Decimal("0")
        max_dd = Decimal("0")
        for _ in range(n):
            r = trade_r_multiples[rng.randrange(n)]
            cumulative += r
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown
        final_rs.append(cumulative)
        max_dds.append(max_dd)

    final_rs.sort()
    max_dds.sort()

    def _percentile(sorted_values: list, p: int) -> Decimal:
        if len(sorted_values) == 1:
            return sorted_values[0]
        idx = min(len(sorted_values) - 1, (p * (len(sorted_values) - 1)) // 100)
        return sorted_values[idx]

    final_r_percentiles = {p: _percentile(final_rs, p) for p in percentiles}
    max_dd_percentiles = {p: _percentile(max_dds, p) for p in percentiles}

    mean_final_r = sum(final_rs, Decimal("0")) / iterations
    mean_max_dd = sum(max_dds, Decimal("0")) / iterations

    return MonteCarloResult(
        iterations=iterations, seed=seed, trade_count=n,
        final_r_percentiles=final_r_percentiles, max_drawdown_r_percentiles=max_dd_percentiles,
        mean_final_r=mean_final_r, mean_max_drawdown_r=mean_max_dd,
    )
