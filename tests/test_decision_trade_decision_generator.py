from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Environment
from core.domain.decision import TradeDecision
from core.domain.risk import RiskDecision
from core.domain.setups import HTFContextSnapshot, Setup, SetupState
from decision.trade_decision_generator import TradeDecisionGeneratorImpl, TradeDecisionRejection


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_setup(score=Decimal("78"), direction=Direction.BULLISH) -> Setup:
    return Setup(
        setup_id="s1", originating_zone_id="fvg:bullish:c1", originating_candle_id="c1",
        direction=direction, created_at=_dt(0), state=SetupState.CONFIRMED, score=score,
        score_snapshot={"total": score},
        htf_context=HTFContextSnapshot(d1_state="BULLISH", h4_state="BULLISH", h1_state="BULLISH"),
        expiry_at=_dt(1000), invalidation_level=Decimal("2395"),
    )


def make_approved_risk_decision() -> RiskDecision:
    return RiskDecision(
        daily_loss_limit_ok=True, consecutive_loss_ok=True, trade_count_ok=True,
        sizing_ok=True, approved=True, reason_code="OK",
    )


def make_generator(**overrides) -> TradeDecisionGeneratorImpl:
    defaults = dict(
        strategy_version="strategy-v1", config_hash="hash123",
        minimum_rr=Decimal("2.0"), minimum_score=70, decision_expiry_seconds=5,
    )
    defaults.update(overrides)
    return TradeDecisionGeneratorImpl(**defaults)


class TestSuccessfulGeneration:
    def test_valid_inputs_produce_a_trade_decision(self):
        generator = make_generator()
        setup = make_setup()
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=True,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert isinstance(result, TradeDecision)
        assert result.setup_id == "s1"
        assert result.symbol == "XAUUSD"

    def test_decision_expiry_is_exactly_5_seconds_after_timestamp(self):
        generator = make_generator(decision_expiry_seconds=5)
        setup = make_setup()
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=True,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert result.decision_expiry == result.decision_timestamp + timedelta(seconds=5)

    def test_rr_of_exactly_2_is_a_pass_since_gate_is_not_strict(self):
        # entry=2400, stop=2395 (risk=5); tp1=2410 (RR=2.0) and
        # tp2=2410 (RR=2.0) -- blended RR is exactly 2.0 regardless of
        # the tp1/tp2 weighting, since both legs already equal the
        # threshold (RR-01 LOCKED: gate is blended TP1/TP2, not TP1
        # alone — see docs/DECISIONS.md).
        generator = make_generator(minimum_rr=Decimal("2.0"))
        setup = make_setup()
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2410"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=True,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert isinstance(result, TradeDecision)

    def test_originating_event_ids_captures_the_known_chain_not_just_setup_id(self):
        # Milestone 5 review finding, verified fixed: this must trace
        # setup_id, originating_zone_id, and originating_candle_id at
        # minimum — not collapse to a single reference.
        generator = make_generator()
        setup = make_setup()
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=True,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert setup.setup_id in result.originating_event_ids
        assert setup.originating_zone_id in result.originating_event_ids
        assert setup.originating_candle_id in result.originating_event_ids
        assert len(result.originating_event_ids) >= 3


class TestIdempotency:
    def test_identical_inputs_produce_identical_decision_id(self):
        # Decision-side idempotency contract: same setup + same
        # decision_timestamp must always produce the same decision_id,
        # so a caller re-evaluating the same moment never accidentally
        # creates two "different" decisions for the same event.
        generator = make_generator()
        setup = make_setup()
        kwargs = dict(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=True,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        result1 = generator.generate(**kwargs)
        result2 = generator.generate(**kwargs)
        assert result1.decision_id == result2.decision_id
        assert result1.idempotency_key == result2.idempotency_key

    def test_different_decision_timestamp_produces_different_decision_id(self):
        # A genuinely new evaluation (different moment) must NOT collide
        # with a prior one — this is what "new decision from a fresh
        # canonical evaluation" (Execution FROZEN v1.0 OD3) requires.
        generator = make_generator()
        setup = make_setup()
        base_kwargs = dict(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=True,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            environment=Environment.BACKTEST,
        )
        result1 = generator.generate(decision_timestamp=_dt(100), **base_kwargs)
        result2 = generator.generate(decision_timestamp=_dt(200), **base_kwargs)
        assert result1.decision_id != result2.decision_id



    def test_htf_context_invalid_rejects_first(self):
        # Checked FIRST regardless of what else is wrong (T12: HTF gate
        # re-verified at approval time takes priority).
        generator = make_generator()
        setup = make_setup(score=Decimal("10"))  # also fails score, but HTF checked first
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=False,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert isinstance(result, TradeDecisionRejection)
        assert result.reason_code == "HTF_CONTEXT_INVALID"

    def test_rr_below_minimum_rejects(self):
        # RR-01 LOCKED (docs/DECISIONS.md): the gate is now a BLENDED
        # R:R across TP1 and TP2 (weighted by tp1_allocation_pct), not
        # TP1 alone — both legs must be weak enough to fail the gate.
        generator = make_generator(minimum_rr=Decimal("2.0"))
        setup = make_setup()
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2405"),  # RR=1.0
            take_profit_2=Decimal("2406"),  # RR=1.2 -- also weak, so the BLEND is below minimum too
            tp1_allocation_pct=Decimal("30"),
            htf_context_still_valid=True, risk_decision=make_approved_risk_decision(),
            position_size=Decimal("0.10"), risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert isinstance(result, TradeDecisionRejection)
        assert result.reason_code == "RR_BELOW_MINIMUM"

    def test_weak_tp1_with_strong_tp2_now_passes_the_blended_gate(self):
        # RR-01's actual intent, verified directly: a setup whose near
        # target (TP1, deliberately the nearest internal liquidity —
        # structurally capped around 1:1-1:1.5, see docs/DECISIONS.md)
        # is modest but whose far target (TP2, external liquidity) is
        # strong should NOT be rejected outright just because TP1 alone
        # looks weak — this is the exact scenario the old TP1-only gate
        # got wrong.
        generator = make_generator(minimum_rr=Decimal("2.0"))
        setup = make_setup()
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2405"),  # RR=1.0 -- would have failed the OLD TP1-only gate
            take_profit_2=Decimal("2420"),  # RR=4.0
            tp1_allocation_pct=Decimal("30"),  # blended: 0.3*1.0 + 0.7*4.0 = 3.1, passes 2.0
            htf_context_still_valid=True, risk_decision=make_approved_risk_decision(),
            position_size=Decimal("0.10"), risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert not isinstance(result, TradeDecisionRejection)

    def test_score_below_threshold_rejects(self):
        generator = make_generator(minimum_score=70)
        setup = make_setup(score=Decimal("65"))  # below threshold
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=True,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert isinstance(result, TradeDecisionRejection)
        assert result.reason_code == "SCORE_BELOW_THRESHOLD"

    def test_risk_engine_rejection_propagates(self):
        generator = make_generator()
        setup = make_setup()
        rejected_risk = RiskDecision(
            daily_loss_limit_ok=False, consecutive_loss_ok=True, trade_count_ok=True,
            sizing_ok=True, approved=False, reason_code="DAILY_LOSS_LIMIT",
        )
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=True,
            risk_decision=rejected_risk, position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert isinstance(result, TradeDecisionRejection)
        assert result.reason_code == "DAILY_LOSS_LIMIT"


class TestBlendedRRCalculation:
    """RR-01 LOCKED (docs/DECISIONS.md): direct unit coverage of the
    blend formula itself, isolated from the rest of generate()'s gates."""

    def test_blend_weights_by_tp1_allocation_pct(self):
        # risk=5; tp1 reward=5 (RR=1.0); tp2 reward=20 (RR=4.0)
        # 30/70 split: 0.3*1.0 + 0.7*4.0 = 3.1
        rr = TradeDecisionGeneratorImpl._compute_blended_rr(
            Direction.BULLISH, Decimal("2400"), Decimal("2395"),
            Decimal("2405"), Decimal("2420"), Decimal("30"),
        )
        assert rr == Decimal("3.1")

    def test_equal_tp1_tp2_rr_blends_to_the_same_value_regardless_of_weighting(self):
        rr_30_70 = TradeDecisionGeneratorImpl._compute_blended_rr(
            Direction.BULLISH, Decimal("2400"), Decimal("2395"),
            Decimal("2410"), Decimal("2410"), Decimal("30"),
        )
        rr_50_50 = TradeDecisionGeneratorImpl._compute_blended_rr(
            Direction.BULLISH, Decimal("2400"), Decimal("2395"),
            Decimal("2410"), Decimal("2410"), Decimal("50"),
        )
        assert rr_30_70 == rr_50_50 == Decimal("2.0")

    def test_bearish_direction_computes_correctly(self):
        # entry=2400, stop=2405 (risk=5), tp1=2395 (reward=5, RR=1.0),
        # tp2=2380 (reward=20, RR=4.0)
        rr = TradeDecisionGeneratorImpl._compute_blended_rr(
            Direction.BEARISH, Decimal("2400"), Decimal("2405"),
            Decimal("2395"), Decimal("2380"), Decimal("30"),
        )
        assert rr == Decimal("3.1")


class TestRejectionDetails:
    """Diagnostic details added after a real backtest run showed a bare
    reason_code gives no way to tell a marginal near-miss from a
    wildly-failing setup without re-deriving the numbers by hand."""

    def test_rr_rejection_carries_the_actual_computed_values(self):
        generator = make_generator(minimum_rr=Decimal("2.0"))
        setup = make_setup()
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2405"), take_profit_2=Decimal("2406"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=True,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert isinstance(result, TradeDecisionRejection)
        assert result.details["rr_tp1"] == "1"
        assert result.details["rr_tp2"] == "1.2"
        assert Decimal(result.details["blended_rr"]) == Decimal("1.14")
        assert result.details["minimum_rr"] == "2.0"

    def test_non_rr_rejections_carry_no_spurious_details(self):
        generator = make_generator(minimum_rr=Decimal("2.0"))
        setup = make_setup()
        result = generator.generate(
            setup=setup, symbol="XAUUSD", entry=Decimal("2400"), stop_loss=Decimal("2395"),
            take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp1_allocation_pct=Decimal("30"), htf_context_still_valid=False,
            risk_decision=make_approved_risk_decision(), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(100), environment=Environment.BACKTEST,
        )
        assert isinstance(result, TradeDecisionRejection)
        assert result.reason_code == "HTF_CONTEXT_INVALID"
        assert result.details == {}
