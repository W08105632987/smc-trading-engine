"""Regression test for TRAIL-01 (docs/DECISIONS.md).

Found via the real walk_forward_6m_v3 run: 11 of 61 trades closed with
economically impossible R-multiples (up to 614R), all via
TRAILING_STOP_HIT. Root cause: TrailingEngineImpl.compute_new_stop
scanned whatever candle sequence it was given for "major" swings with
no check that a candidate's price was actually still consistent with
where the market currently is. backtest/pipeline.py's
_manage_open_position used to pass the FULL replay-to-date candle
history on every POST_TP1 step, so a major swing from a wholly
unrelated, much-earlier price regime (e.g. before a later pullback)
could surface as "more protective" purely because its raw price
exceeded current_sl -- with nothing checking whether that price was
already behind live market action at all. That stale candidate could
then be returned as the new trailing stop even though it sat ABOVE
current market price, which the very next candle trivially breached,
producing an immediate, meaningless TRAILING_STOP_HIT at the stale
level.

The fix is a direct invariant inside TrailingEngineImpl.compute_new_stop
itself (not just at the pipeline.py call site, so it holds for ANY
caller): a candidate is rejected if the current (most recent) candle in
the supplied sequence would already breach it. This is unconditional --
it does not depend on how far back the offending swing originated, only
on whether the resulting stop makes sense against live price right now.
pipeline.py additionally bounds the candle window it passes
(TRAILING_SWING_LOOKBACK_MARGIN) as a secondary, performance-motivated
measure, but the invariant below is what actually guarantees
correctness regardless of caller behavior.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.market_data import Candle, DataSource
from strategy.trailing.trailing_engine import TrailingEngineImpl


def _mk(i: int, o, h, l, c) -> Candle:
    t0 = datetime(2025, 9, 1, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
    return Candle(
        candle_id=f"c{i}", symbol="XAUUSD", timeframe=Timeframe.M5,
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)), close=Decimal(str(c)),
        timestamp_open=t0, timestamp_close=t0 + timedelta(minutes=5),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


def _build_stale_swing_scenario(filler_candle_count: int) -> list[Candle]:
    """Constructs: (1) a genuine MAJOR swing low at a high raw price,
    early in the sequence; (2) a block of unrelated flat filler candles,
    simulating intervening replay history of any length; (3) a sharp
    pullback down to a much lower level; (4) a position recovering
    partway afterward, with live price now well below the old swing's
    price. This is exactly the walk_forward_6m_v3 shape: an earlier
    swing numerically above where the market currently trades."""
    candles: list[Candle] = []
    i = 0
    for lo in [105, 102, 100, 103, 106]:
        candles.append(_mk(i, lo + 1, lo + 2, lo, lo + 1)); i += 1
    for c in [108, 112, 118, 125, 135, 150, 170, 190]:
        candles.append(_mk(i, c - 2, c + 1, c - 3, c)); i += 1
    for _ in range(filler_candle_count):
        candles.append(_mk(i, 150, 151, 149, 150)); i += 1
    for c in [150, 130, 110, 95, 85, 80]:
        candles.append(_mk(i, c + 3, c + 4, c - 2, c)); i += 1
    for c in [82, 85, 87, 90, 92]:
        candles.append(_mk(i, c - 1, c + 1, c - 2, c)); i += 1
    return candles


def test_compute_new_stop_never_returns_a_level_already_breached_by_live_price():
    """The core TRAIL-01 invariant, at the actual fix location. Covers
    zero, small, and large amounts of intervening history -- the
    guarantee must not depend on how much history separates the stale
    swing from the live position; it depends only on whether the
    candidate makes sense against where price currently is."""
    for filler_count in (0, 20, 500, 5000, 20000):
        candles = _build_stale_swing_scenario(filler_candle_count=filler_count)
        atr = {c.candle_id: Decimal("1.0") for c in candles}
        trailing = TrailingEngineImpl(trailing_sl_buffer_atr=Decimal("0.1"), major_swing_atr_multiple=Decimal("1.0"))
        current_sl = Decimal("80")

        new_sl = trailing.compute_new_stop(Direction.BULLISH, current_sl, candles, atr, Decimal("1.0"))

        current_price_low = candles[-1].low
        assert new_sl is None or new_sl < current_price_low, (
            f"TRAIL-01 invariant violated with filler_count={filler_count}: "
            f"compute_new_stop returned new_sl={new_sl}, which the current "
            f"candle (low={current_price_low}) would already breach"
        )


def test_happy_path_trailing_still_works_when_candidate_is_genuinely_behind_price():
    """Sanity check in the other direction: the invariant must not
    reject a LEGITIMATE trailing candidate that sits safely behind live
    price. Reuses test_backtest_pipeline.py's own hand-verified CANDLES
    fixture (pivot low at price=2410 at c9, confirmed at c11, qualifying
    MAJOR once c12 is visible) and confirms the expected 2409.900 stop
    is still produced -- this is the exact scenario the fix must not
    break. Uses the full pipeline integration test rather than a
    hand-rolled candle set, since constructing a fresh fractal/ATR-
    displacement-qualifying swing by hand is easy to get subtly wrong
    (naive first attempts at this produced no detectable swing at all)."""
    import sys
    sys.path.insert(0, "tests")
    import importlib
    tbp = importlib.import_module("test_backtest_pipeline")

    pipeline = tbp._make_pipeline()
    pipeline.state.active_setups[tbp._make_setup().setup_id] = tbp._make_setup()
    results = tbp._run_through(pipeline, tbp.CANDLES, len(tbp.CANDLES))

    trailing_updates = [
        [e.new_state for e in r.events if e.event_type.value == "TRAILING_STOP_UPDATED"]
        for r in results
    ]
    # Index 15 == CANDLES[:16], i.e. once c12 (the MAJOR-qualifying candle) is visible.
    assert trailing_updates[15] == ["2409.900"], (
        f"expected the legitimate, price-consistent candidate 2409.900 at step 15, "
        f"got {trailing_updates[15]} -- the TRAIL-01 invariant must not reject "
        "candidates that are genuinely behind live price"
    )
