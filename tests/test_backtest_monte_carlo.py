import random
from decimal import Decimal

import pytest

from backtest.validation.monte_carlo import run_monte_carlo

RS = [Decimal("2"), Decimal("-1"), Decimal("-1"), Decimal("3"), Decimal("-2")]


def test_same_seed_produces_identical_result():
    r1 = run_monte_carlo(RS, iterations=500, seed=42)
    r2 = run_monte_carlo(RS, iterations=500, seed=42)
    assert r1.final_r_percentiles == r2.final_r_percentiles
    assert r1.max_drawdown_r_percentiles == r2.max_drawdown_r_percentiles
    assert r1.mean_final_r == r2.mean_final_r
    assert r1.mean_max_drawdown_r == r2.mean_max_drawdown_r


def test_different_seed_produces_a_different_result():
    r1 = run_monte_carlo(RS, iterations=500, seed=42)
    r2 = run_monte_carlo(RS, iterations=500, seed=43)
    assert r1.mean_final_r != r2.mean_final_r or r1.final_r_percentiles != r2.final_r_percentiles


def test_global_random_state_is_never_touched():
    """The local random.Random(seed) instance must never read from or
    perturb the global `random` module state — verified here by seeding
    the global generator, running Monte Carlo, and confirming the next
    global draw is exactly what it would have been regardless."""
    random.seed(1234)
    expected_next = random.random()

    random.seed(1234)
    run_monte_carlo(RS, iterations=2000, seed=999)
    actual_next = random.random()

    assert expected_next == actual_next


def test_mean_final_r_converges_to_n_times_mean_r():
    n = len(RS)
    true_mean = sum(RS) / len(RS)
    expected = Decimal(n) * true_mean  # == 1.0 for this fixture

    result = run_monte_carlo(RS, iterations=20000, seed=7)
    assert abs(result.mean_final_r - expected) < Decimal("0.3")


def test_percentiles_are_non_decreasing():
    result = run_monte_carlo(RS, iterations=1000, seed=1, percentiles=(5, 25, 50, 75, 95))
    values = [result.final_r_percentiles[p] for p in (5, 25, 50, 75, 95)]
    assert values == sorted(values)


def test_empty_trade_series_raises():
    with pytest.raises(ValueError, match="empty"):
        run_monte_carlo([], iterations=100, seed=1)


def test_non_positive_iterations_raises():
    with pytest.raises(ValueError, match="positive"):
        run_monte_carlo(RS, iterations=0, seed=1)


def test_out_of_range_percentile_raises():
    with pytest.raises(ValueError, match="out of range"):
        run_monte_carlo(RS, iterations=100, seed=1, percentiles=(5, 150))


def test_single_trade_series_is_handled():
    result = run_monte_carlo([Decimal("2")], iterations=50, seed=1)
    # every resample is just [2] repeated -> final_r is always exactly 2
    assert result.mean_final_r == Decimal("2")
    assert all(v == Decimal("2") for v in result.final_r_percentiles.values())
