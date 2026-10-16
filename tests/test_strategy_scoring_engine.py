from decimal import Decimal

from core.config.registry import ScoringWeights
from strategy.scoring.scoring_engine import ScoringEngineImpl, ScoringFactors


class TestScoringEngine:
    def test_all_factors_present_scores_95_since_session_always_excluded(self):
        # Session alignment is structurally always False (deferred) —
        # max achievable score right now is 95, not 100.
        factors = ScoringFactors(
            d1_h4_alignment=True, h1_alignment=True, premium_discount=True,
            liquidity_sweep=True, mss=True, displacement=True, fvg=True,
            order_block=True, ote=True, target_liquidity=True,
        )
        engine = ScoringEngineImpl(weights=ScoringWeights())
        total, snapshot = engine.score(factors)
        assert total == Decimal("95")
        assert snapshot["session_alignment"] == Decimal("0")

    def test_no_factors_present_scores_zero(self):
        factors = ScoringFactors(
            d1_h4_alignment=False, h1_alignment=False, premium_discount=False,
            liquidity_sweep=False, mss=False, displacement=False, fvg=False,
            order_block=False, ote=False, target_liquidity=False,
        )
        engine = ScoringEngineImpl(weights=ScoringWeights())
        total, snapshot = engine.score(factors)
        assert total == Decimal("0")

    def test_default_weights_sum_to_100(self):
        # Sanity check on the config-sourced weight table: all eleven
        # factor weights (including the deferred session one) still sum
        # to exactly 100, matching Phase 2 §17's frozen 100-point model.
        w = ScoringWeights()
        total = (
            w.d1_h4_alignment.value + w.h1_alignment.value + w.premium_discount.value
            + w.liquidity_sweep.value + w.mss.value + w.displacement.value
            + w.fvg.value + w.order_block.value + w.ote.value
            + w.session_alignment.value + w.target_liquidity.value
        )
        assert total == Decimal("100")

    def test_only_hard_confluence_factors_present(self):
        factors = ScoringFactors(
            d1_h4_alignment=True, h1_alignment=False, premium_discount=False,
            liquidity_sweep=True, mss=True, displacement=False, fvg=False,
            order_block=False, ote=False, target_liquidity=False,
        )
        engine = ScoringEngineImpl(weights=ScoringWeights())
        total, snapshot = engine.score(factors)
        assert total == Decimal("45")  # 15 + 15 + 15
        assert snapshot["d1_h4_alignment"] == Decimal("15")
        assert snapshot["liquidity_sweep"] == Decimal("15")
        assert snapshot["mss"] == Decimal("15")
        assert snapshot["h1_alignment"] == Decimal("0")

    def test_session_alignment_field_cannot_be_forced_true_by_accident(self):
        factors = ScoringFactors(
            d1_h4_alignment=False, h1_alignment=False, premium_discount=False,
            liquidity_sweep=False, mss=False, displacement=False, fvg=False,
            order_block=False, ote=False, target_liquidity=False,
        )
        assert factors.session_alignment is False

    def test_weights_are_config_driven_not_duplicated_in_engine(self):
        # Milestone 5 review finding, verified fixed: changing the
        # config actually changes scoring output — proving the engine
        # reads from config rather than a hardcoded duplicate.
        custom_weights = ScoringWeights()
        custom_weights.mss.value = Decimal("50")  # drastically different from the default 15
        factors = ScoringFactors(
            d1_h4_alignment=False, h1_alignment=False, premium_discount=False,
            liquidity_sweep=False, mss=True, displacement=False, fvg=False,
            order_block=False, ote=False, target_liquidity=False,
        )
        engine = ScoringEngineImpl(weights=custom_weights)
        total, snapshot = engine.score(factors)
        assert total == Decimal("50")
