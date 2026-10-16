from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Environment, Timeframe
from core.domain.decision import EntryType, TradeDecision
from core.domain.execution import OrderState
from core.domain.market_data import Candle, DataSource
from backtest.execution_adapter import BacktestExecutionAdapter, find_first_exit


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


class TestFindFirstExitBullish:
    def test_sl_hit_detected(self):
        candles = [make_candle(0, 2402, 2394, 2398)]  # low breaches SL=2395
        result = find_first_exit(Direction.BULLISH, Decimal("2395"), Decimal("2410"), Decimal("2420"), candles)
        assert result.reason == "SL_HIT"
        assert result.exit_price == Decimal("2395")

    def test_tp1_hit_detected(self):
        candles = [make_candle(0, 2412, 2398, 2408)]  # high reaches TP1=2410
        result = find_first_exit(Direction.BULLISH, Decimal("2395"), Decimal("2410"), Decimal("2420"), candles)
        assert result.reason == "TP1_HIT"

    def test_no_exit_when_neither_level_reached(self):
        candles = [make_candle(0, 2405, 2400, 2402)]
        result = find_first_exit(Direction.BULLISH, Decimal("2395"), Decimal("2410"), Decimal("2420"), candles)
        assert result is None

    def test_sl_takes_priority_when_both_hit_same_candle(self):
        # ADVERSARIAL / documented assumption: a candle whose range
        # crosses BOTH SL and TP1 must resolve to SL_HIT, per the
        # flagged conservative-ordering assumption — never silently
        # assume the favorable outcome.
        candles = [make_candle(0, 2415, 2390, 2400)]  # crosses both 2395 (SL) and 2410 (TP1)
        result = find_first_exit(Direction.BULLISH, Decimal("2395"), Decimal("2410"), Decimal("2420"), candles)
        assert result.reason == "SL_HIT"

    def test_sequential_scan_never_uses_a_later_candle_before_an_earlier_one_qualifies(self):
        # Adversarial: candle 0 doesn't hit anything, candle 1 hits SL,
        # candle 2 would hit TP1 — must report candle 1's SL_HIT, never
        # jump ahead to candle 2's outcome.
        candles = [
            make_candle(0, 2405, 2400, 2402),          # no exit
            make_candle(1, 2408, 2394, 2400),           # SL hit here
            make_candle(2, 2425, 2412, 2422),           # would ALSO hit TP1/TP2 — must never be reached
        ]
        result = find_first_exit(Direction.BULLISH, Decimal("2395"), Decimal("2410"), Decimal("2420"), candles)
        assert result.reason == "SL_HIT"
        assert result.candle_id == "c1"

    def test_empty_candle_list_returns_none(self):
        assert find_first_exit(Direction.BULLISH, Decimal("2395"), Decimal("2410"), Decimal("2420"), []) is None

    def test_no_tp_levels_still_detects_sl(self):
        # Post-TP1 trailing phase has no TP1 (already hit) — verify the
        # function handles None gracefully.
        candles = [make_candle(0, 2402, 2394, 2398)]
        result = find_first_exit(Direction.BULLISH, Decimal("2395"), None, Decimal("2420"), candles)
        assert result.reason == "SL_HIT"


class TestFindFirstExitBearish:
    def test_sl_hit_detected(self):
        candles = [make_candle(0, 2406, 2398, 2402)]  # high breaches SL=2405
        result = find_first_exit(Direction.BEARISH, Decimal("2405"), Decimal("2390"), Decimal("2380"), candles)
        assert result.reason == "SL_HIT"

    def test_tp1_hit_detected(self):
        candles = [make_candle(0, 2400, 2388, 2395)]  # low reaches TP1=2390
        result = find_first_exit(Direction.BEARISH, Decimal("2405"), Decimal("2390"), Decimal("2380"), candles)
        assert result.reason == "TP1_HIT"


class TestBacktestExecutionAdapterFrictionless:
    def test_market_order_fills_at_intended_price_with_zero_slippage(self):
        decision = TradeDecision(
            decision_id="d1", strategy_version="v1", config_hash="h1", setup_id="s1",
            symbol="XAUUSD", direction=Direction.BULLISH, entry_type=EntryType.MARKET,
            intended_entry_price=Decimal("2400"), execution_price_policy=Decimal("0.10"),
            stop_loss=Decimal("2395"), take_profit_1=Decimal("2410"), take_profit_2=Decimal("2420"),
            tp_allocation=(Decimal("30"), Decimal("70")), position_size=Decimal("0.10"),
            risk_amount=Decimal("50"), risk_percentage=Decimal("0.5"),
            decision_timestamp=_dt(0), decision_expiry=_dt(0) + timedelta(seconds=5),
            originating_event_ids=("s1",), score=Decimal("78"), environment=Environment.BACKTEST,
            idempotency_key="d1",
        )
        adapter = BacktestExecutionAdapter()
        order = adapter.submit_market_order(decision)

        assert order.state == OrderState.ORDER_FILLED
        assert order.avg_fill_price == Decimal("2400")  # no slippage
        assert order.filled_volume == decision.position_size  # full fill, frictionless
