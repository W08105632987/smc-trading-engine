"""End-to-end integration tests for backtest/engine.py. Uses the SAME
verified touch/pivot/confirm/break sequence as Milestone 4's
confirmation-engine tests (hand-simulated there and re-verified here for
the extended fixture) rather than inventing new, unverified candle data
for this already-complex integration surface."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.market_data import Candle, DataSource
from core.domain.risk import RiskSnapshot
from core.domain.setups import HTFContextSnapshot, Setup, SetupState
from core.domain.zones import Zone, ZoneType
from core.config.registry import ScoringWeights

from backtest.engine import BacktestEngine, BacktestRunConfig
from backtest.execution_adapter import BacktestExecutionAdapter
from decision.trade_decision_generator import TradeDecisionGeneratorImpl
from strategy.confirmation.confirmation_engine import ConfirmationEngineImpl
from strategy.risk.risk_engine import RiskEngineImpl
from strategy.scoring.scoring_engine import ScoringEngineImpl, ScoringFactors
from strategy.trailing.trailing_engine import TrailingEngineImpl


def _dt(index: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=index * 5 + 5)


def make_candle(index: int, high, low, close, open_=None) -> Candle:
    open_ = Decimal(str(open_ if open_ is not None else close))
    return Candle(
        candle_id=f"c{index}", symbol="XAUUSD", timeframe=Timeframe.M5,
        open=open_, high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        timestamp_open=_dt(index) - timedelta(minutes=5), timestamp_close=_dt(index),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


# Verified sequence (identical to Milestone 4's confirmation-engine
# fixture): touch at c0, pivot high=2401 at c2, confirms at c3, breaks
# at c5 (close=2401.5).
BASE_CANDLES = [
    make_candle(0, 2399.5, 2398.5, 2399.0),
    make_candle(1, 2399.0, 2397.0, 2397.5),
    make_candle(2, 2401.0, 2398.0, 2400.5),
    make_candle(3, 2400.0, 2398.5, 2399.0),
    make_candle(4, 2400.5, 2399.0, 2400.0),
    make_candle(5, 2402.0, 2400.0, 2401.5),
]
BASE_ATR = {c.candle_id: Decimal("1.0") for c in BASE_CANDLES}


def make_zone() -> Zone:
    return Zone(
        zone_id="z1", zone_type=ZoneType.FVG, direction=Direction.BULLISH,
        upper=Decimal("2400"), lower=Decimal("2398"), origin_candle_id="origin",
        creation_timestamp=datetime(2026, 1, 4, tzinfo=timezone.utc),
    )


def make_setup(expiry_index: int = 100, invalidation=Decimal("2395")) -> Setup:
    return Setup(
        setup_id="s1", originating_zone_id="z1", originating_candle_id="c0",
        direction=Direction.BULLISH, created_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
        state=SetupState.WAITING_FOR_RETEST, score=Decimal("78"),
        score_snapshot={"total": Decimal("78")},
        htf_context=HTFContextSnapshot(d1_state="BULLISH", h4_state="BULLISH", h1_state="BULLISH"),
        expiry_at=_dt(expiry_index), invalidation_level=invalidation,
    )


def make_engine() -> BacktestEngine:
    return BacktestEngine(
        confirmation_engine=ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.1")),
        scoring_engine=ScoringEngineImpl(weights=ScoringWeights()),
        risk_engine=RiskEngineImpl(
            risk_per_trade_pct=Decimal("0.5"), daily_loss_limit_pct=Decimal("2.0"),
            max_consecutive_losses=3, max_trades_per_day=3,
        ),
        decision_generator=TradeDecisionGeneratorImpl(
            strategy_version="v1", config_hash="h1", minimum_rr=Decimal("2.0"),
            minimum_score=70, decision_expiry_seconds=5,
        ),
        trailing_engine=TrailingEngineImpl(
            trailing_sl_buffer_atr=Decimal("0.1"), major_swing_atr_multiple=Decimal("1.0")
        ),
        adapter=BacktestExecutionAdapter(),
        run_config=BacktestRunConfig(
            symbol="XAUUSD", tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
            volume_step=Decimal("0.01"), min_volume=Decimal("0.01"), max_volume=Decimal("100"),
            minimum_stop_atr=Decimal("0.25"),
            tp1_allocation_pct=Decimal("30"),  # matches StrategyConfig.tp1_allocation_pct default
        ),
    )


def make_risk_snapshot() -> RiskSnapshot:
    return RiskSnapshot(
        snapshot_id="r1", timestamp=_dt(0), account_equity=Decimal("10000"),
        daily_starting_equity=Decimal("10000"), daily_realized_pnl=Decimal("0"),
        consecutive_losses_count=0, trades_today_count=0,
    )


def make_scoring_factors() -> ScoringFactors:
    return ScoringFactors(
        d1_h4_alignment=True, h1_alignment=True, premium_discount=True,
        liquidity_sweep=True, mss=True, displacement=False, fvg=False,
        order_block=False, ote=False, target_liquidity=False,
    )


class TestHappyPathToSLExit:
    def test_confirmed_setup_produces_trade_with_sl_exit(self):
        # TP1=2415/TP2=2425 give RR = (2415-2401.5)/(2401.5-2395) ≈ 2.08, above the 2.0 minimum.
        candles = BASE_CANDLES + [make_candle(6, 2405, 2394, 2400)]  # SL breach at c6
        atr = {**BASE_ATR, "c6": Decimal("1.0")}

        engine = make_engine()
        record = engine.run_setup(
            setup=make_setup(), zone=make_zone(), candles=candles, atr_by_candle_id=atr,
            take_profit_1=Decimal("2415"), take_profit_2=Decimal("2425"),
            scoring_factors=make_scoring_factors(), risk_snapshot=make_risk_snapshot(),
            htf_context_still_valid=True,
        )

        assert record is not None
        assert record.exit_reason == "SL_HIT"
        assert record.entry_price == Decimal("2401.5")
        assert record.realized_r is not None
        assert record.realized_r < 0  # a loss, correctly signed


class TestHappyPathToTP1ThenOpen:
    def test_tp1_hit_with_no_further_candles_stays_open(self):
        candles = BASE_CANDLES + [make_candle(6, 2416, 2400, 2415.5)]  # TP1 breach at c6
        atr = {**BASE_ATR, "c6": Decimal("1.0")}

        engine = make_engine()
        record = engine.run_setup(
            setup=make_setup(), zone=make_zone(), candles=candles, atr_by_candle_id=atr,
            take_profit_1=Decimal("2415"), take_profit_2=Decimal("2425"),
            scoring_factors=make_scoring_factors(), risk_snapshot=make_risk_snapshot(),
            htf_context_still_valid=True,
        )

        assert record is not None
        assert record.exit_reason == "OPEN"
        assert record.realized_r is None


class TestInvalidationBeforeConfirmation:
    def test_invalidated_setup_never_produces_a_trade(self):
        # Truncate BEFORE the break candle (c5), and have the last
        # visible candle close below the invalidation level.
        low_close_candle = make_candle(4, 2400.5, 2391.0, 2393.0)  # closes well below invalidation=2395
        candles = BASE_CANDLES[:4] + [low_close_candle]
        atr = {c.candle_id: Decimal("1.0") for c in candles}

        engine = make_engine()
        record = engine.run_setup(
            setup=make_setup(invalidation=Decimal("2395")), zone=make_zone(),
            candles=candles, atr_by_candle_id=atr,
            take_profit_1=Decimal("2415"), take_profit_2=Decimal("2425"),
            scoring_factors=make_scoring_factors(), risk_snapshot=make_risk_snapshot(),
            htf_context_still_valid=True,
        )
        assert record is None


class TestExpiryBeforeConfirmation:
    def test_expired_setup_never_produces_a_trade(self):
        truncated = BASE_CANDLES[:5]  # not yet confirmed (no break candle)
        engine = make_engine()
        record = engine.run_setup(
            setup=make_setup(expiry_index=3),  # expires before c4's timestamp
            zone=make_zone(), candles=truncated,
            atr_by_candle_id={c.candle_id: Decimal("1.0") for c in truncated},
            take_profit_1=Decimal("2415"), take_profit_2=Decimal("2425"),
            scoring_factors=make_scoring_factors(), risk_snapshot=make_risk_snapshot(),
            htf_context_still_valid=True,
        )
        assert record is None


class TestDeterminism:
    def test_identical_inputs_produce_identical_trade_record(self):
        candles = BASE_CANDLES + [make_candle(6, 2405, 2394, 2400)]
        atr = {**BASE_ATR, "c6": Decimal("1.0")}
        engine = make_engine()
        kwargs = dict(
            setup=make_setup(), zone=make_zone(), candles=candles, atr_by_candle_id=atr,
            take_profit_1=Decimal("2415"), take_profit_2=Decimal("2425"),
            scoring_factors=make_scoring_factors(), risk_snapshot=make_risk_snapshot(),
            htf_context_still_valid=True,
        )
        r1 = engine.run_setup(**kwargs)
        r2 = engine.run_setup(**kwargs)
        assert r1 == r2


class TestFutureCandleTruncationAdversarial:
    def test_result_unaffected_by_removing_candles_after_the_exit_point(self):
        # THE required adversarial property: a dramatically different
        # future (huge bullish move) placed AFTER the SL candle must
        # never change the outcome — the exit was already determined at
        # c6, so c7's content is irrelevant.
        exit_candle = make_candle(6, 2405, 2394, 2400)  # SL breach
        dramatic_future_candle = make_candle(7, 2500, 2450, 2490)  # would have been a huge win if reachable

        engine = make_engine()
        atr_short = {**BASE_ATR, "c6": Decimal("1.0")}
        atr_long = {**BASE_ATR, "c6": Decimal("1.0"), "c7": Decimal("1.0")}

        record_without_future = engine.run_setup(
            setup=make_setup(), zone=make_zone(), candles=BASE_CANDLES + [exit_candle],
            atr_by_candle_id=atr_short, take_profit_1=Decimal("2415"), take_profit_2=Decimal("2425"),
            scoring_factors=make_scoring_factors(), risk_snapshot=make_risk_snapshot(),
            htf_context_still_valid=True,
        )
        record_with_future = engine.run_setup(
            setup=make_setup(), zone=make_zone(),
            candles=BASE_CANDLES + [exit_candle, dramatic_future_candle],
            atr_by_candle_id=atr_long, take_profit_1=Decimal("2415"), take_profit_2=Decimal("2425"),
            scoring_factors=make_scoring_factors(), risk_snapshot=make_risk_snapshot(),
            htf_context_still_valid=True,
        )

        assert record_without_future.exit_reason == record_with_future.exit_reason == "SL_HIT"
        assert record_without_future.realized_r == record_with_future.realized_r
