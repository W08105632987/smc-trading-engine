"""VALIDITY DEFECT V1 -- H4 structure events re-applied out of chronological order after a reversal.

STATUS: these tests assert the DESIRED behaviour and are EXPECTED TO FAIL on the Phase-A-v2/E0 code.
(Run:  pytest tests_validity -q   -- deliberately outside the default `tests/` path.)

CLASSIFICATION
  PROVEN CORRECTNESS DEFECT (not a strategy-policy choice):
    * StructureDetectorImpl.detect() takes ONE `prevailing_direction` for the WHOLE candle list and classifies every
      historical break as BOS or CHoCH relative to it (strategy/structure/bos_choch_detector.py:30-72). The event id
      embeds that type.
    * ReplayPipeline._recompute_h4_layer (backtest/pipeline.py ~586-621) passes the CURRENT prevailing direction every
      step and feeds every id not yet in `emitted_event_ids` to the H4 state machine. After a bias flip the same
      historical breaks are reclassified (new ids) and applied AGAIN, now, to the current state.
    * Consequence: the state machine consumes events dated BEFORE ones it already consumed -- state is no longer a
      chronological function of market structure. This is independent of what the intended policy for flips is.
  NOT a policy question: which bias a flip should produce is policy; applying a fact twice, out of order, is not.

EVIDENCE
  * Frozen October 2025 run: 3 declared reversals to BEARISH (Oct 21, 27, 28) each followed in the SAME step by
    September-dated transitions ending BULLISH (events.jsonl lines 39226-39231, 46292-46297, 47351-47357).
  * The 42-candle series below reproduces the pattern deterministically (synthetic data, software test only).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import tests.test_backtest_pipeline as TP
from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataSource
from strategy.indicators.atr import atr_provider_from_period, DEFAULT_ATR_PERIOD

# 42 H4 candles (open, high, low, close); first candle opens HARMFUL_START
HARMFUL_START = datetime.fromisoformat("2026-01-10T08:00:00+00:00")
HARMFUL = [
    ("2431.48", "2432.61", "2422.10", "2424.64"),
    ("2424.64", "2434.70", "2422.66", "2434.19"),
    ("2434.19", "2445.95", "2431.26", "2444.83"),
    ("2444.83", "2455.15", "2442.76", "2452.30"),
    ("2452.30", "2463.97", "2450.58", "2462.75"),
    ("2462.75", "2465.10", "2455.04", "2457.79"),
    ("2457.79", "2460.45", "2450.53", "2452.79"),
    ("2452.79", "2453.86", "2444.51", "2446.66"),
    ("2446.66", "2447.42", "2438.29", "2439.91"),
    ("2439.91", "2440.72", "2433.42", "2435.38"),
    ("2435.38", "2442.57", "2434.03", "2439.89"),
    ("2439.89", "2447.05", "2437.75", "2444.12"),
    ("2444.12", "2453.01", "2441.95", "2450.11"),
    ("2450.11", "2455.14", "2449.19", "2454.32"),
    ("2454.32", "2460.40", "2451.52", "2459.32"),
    ("2459.32", "2465.92", "2458.20", "2465.34"),
    ("2465.34", "2466.41", "2457.86", "2460.33"),
    ("2460.33", "2461.73", "2455.29", "2457.50"),
    ("2457.50", "2459.88", "2452.41", "2453.28"),
    ("2453.28", "2454.18", "2447.71", "2449.49"),
    ("2449.49", "2450.25", "2443.96", "2444.51"),
    ("2444.51", "2445.27", "2441.62", "2442.18"),
    ("2442.18", "2448.82", "2439.97", "2447.37"),
    ("2447.37", "2454.48", "2444.85", "2453.45"),
    ("2453.45", "2458.75", "2451.47", "2458.20"),
    ("2458.20", "2467.59", "2456.24", "2464.76"),
    ("2464.76", "2473.65", "2463.07", "2471.49"),
    ("2471.49", "2477.51", "2470.69", "2474.61"),
    ("2474.61", "2476.04", "2470.83", "2471.38"),
    ("2471.38", "2472.13", "2465.69", "2466.96"),
    ("2466.96", "2469.14", "2462.62", "2463.16"),
    ("2463.16", "2464.68", "2456.08", "2457.80"),
    ("2457.80", "2459.77", "2450.78", "2451.47"),
    ("2451.47", "2456.73", "2450.42", "2454.42"),
    ("2454.42", "2458.90", "2452.99", "2458.11"),
    ("2458.11", "2463.56", "2456.75", "2462.71"),
    ("2462.71", "2469.12", "2461.90", "2467.99"),
    ("2467.99", "2473.52", "2466.51", "2472.19"),
    ("2472.19", "2474.33", "2458.67", "2461.11"),
    ("2461.11", "2462.85", "2451.43", "2452.83"),
    ("2452.83", "2455.32", "2442.31", "2443.48"),
    ("2443.48", "2445.17", "2431.52", "2434.41"),
]

# 22 H4 candles in which ONE break is applied twice (CHoCH, then re-classified as BOS)
DOUBLE_START = datetime.fromisoformat("2026-01-13T16:00:00+00:00")
DOUBLE = [
    ("2449.49", "2450.25", "2443.96", "2444.51"),
    ("2444.51", "2445.27", "2441.62", "2442.18"),
    ("2442.18", "2448.82", "2439.97", "2447.37"),
    ("2447.37", "2454.48", "2444.85", "2453.45"),
    ("2453.45", "2458.75", "2451.47", "2458.20"),
    ("2458.20", "2467.59", "2456.24", "2464.76"),
    ("2464.76", "2473.65", "2463.07", "2471.49"),
    ("2471.49", "2477.51", "2470.69", "2474.61"),
    ("2474.61", "2476.04", "2470.83", "2471.38"),
    ("2471.38", "2472.13", "2465.69", "2466.96"),
    ("2466.96", "2469.14", "2462.62", "2463.16"),
    ("2463.16", "2464.68", "2456.08", "2457.80"),
    ("2457.80", "2459.77", "2450.78", "2451.47"),
    ("2451.47", "2456.73", "2450.42", "2454.42"),
    ("2454.42", "2458.90", "2452.99", "2458.11"),
    ("2458.11", "2463.56", "2456.75", "2462.71"),
    ("2462.71", "2469.12", "2461.90", "2467.99"),
    ("2467.99", "2473.52", "2466.51", "2472.19"),
    ("2472.19", "2474.33", "2458.67", "2461.11"),
    ("2461.11", "2462.85", "2451.43", "2452.83"),
    ("2452.83", "2455.32", "2442.31", "2443.48"),
    ("2443.48", "2445.17", "2431.52", "2434.41"),
]


def _candles(start, rows):
    out = []
    for i, (o, h, l, c) in enumerate(rows):
        ts = start + timedelta(hours=4 * i)
        out.append(Candle(candle_id=f"h4-{i}", symbol="XAUUSD", timeframe=Timeframe.H4, open=D(o), high=D(h), low=D(l), close=D(c),
                          timestamp_open=ts, timestamp_close=ts + timedelta(hours=4), source=DataSource.BROKER_NATIVE, confirmed=True))
    return out


def _drive(candles, first=12):
    """Replay H4 candles one at a time through the REAL H4 layer; spy on every structure event applied."""
    p = TP._make_pipeline()
    p._atr_provider = atr_provider_from_period(DEFAULT_ATR_PERIOD)   # real Wilder ATR (the fixture default is a stub)
    applied, end_states = [], []
    orig = p._advance_h4_bias

    def spy(ev, at):
        applied.append({"step": at, "id": ev.event_id, "confirmed_at": ev.confirmed_at, "state_before": p.state.h4_bias_state.current_state.value})
        return orig(ev, at)

    p._advance_h4_bias = spy
    for k in range(first, len(candles) + 1):
        sub = candles[:k]
        n0 = len(applied)
        p._recompute_h4_layer(sub[-1].timestamp_close, sub)
        end_states.append({"step": sub[-1].timestamp_close, "state": p.state.h4_bias_state.current_state.value, "applied": applied[n0:]})
    return p, applied, end_states


class TestH4EventsAreAppliedChronologically:
    def test_structure_events_are_applied_in_non_decreasing_time_order(self):
        _, applied, _ = _drive(_candles(HARMFUL_START, HARMFUL))
        times = [a["confirmed_at"] for a in applied]
        regress = [(i, times[i - 1], times[i]) for i in range(1, len(times)) if times[i] < times[i - 1]]
        assert regress == [], f"events applied out of time order (index, previous, this): {regress}"

    def test_a_confirmed_reversal_is_not_undone_by_replayed_history_in_the_same_step(self):
        _, _, steps = _drive(_candles(HARMFUL_START, HARMFUL))
        undone = [s for s in steps
                  if any(a["state_before"] == "BEARISH_WARNING" for a in s["applied"]) and s["state"] != "BEARISH"
                  and len(s["applied"]) >= 3]
        # the reversal step itself must end BEARISH
        reversal_steps = [s for s in steps if s["applied"] and s["applied"][0]["state_before"] == "BEARISH_WARNING"]
        assert reversal_steps, "fixture must contain a reversal step"
        assert all(s["state"] == "BEARISH" for s in reversal_steps), [(str(s["step"]), s["state"]) for s in reversal_steps]

    def test_one_market_break_is_applied_once_not_as_choch_then_again_as_bos(self):
        _, applied, _ = _drive(_candles(DOUBLE_START, DOUBLE))
        # event ids look like '<TYPE>:<swing_id>:<break_candle_id>' -> the underlying break is the (swing, candle) pair
        keys = [tuple(a["id"].split(":", 1)[1:]) for a in applied]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        assert dupes == [], f"the same break was applied more than once under different classifications: {dupes}"


class TestControls:
    """These PASS today: they show the tests above are discriminating, not vacuous."""

    def test_control_a_run_with_no_flip_applies_events_in_order(self):
        # first 24 candles of HARMFUL contain no reversal yet
        _, applied, _ = _drive(_candles(HARMFUL_START, HARMFUL[:24]))
        times = [a["confirmed_at"] for a in applied]
        assert times == sorted(times)

    def test_control_the_harmful_series_does_contain_a_reversal_to_bearish(self):
        _, applied, steps = _drive(_candles(HARMFUL_START, HARMFUL))
        assert any(a["state_before"] == "BEARISH_WARNING" for a in applied)
