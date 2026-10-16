"""Tests for backtest/tick_execution.py and the pipeline's optional tick
exit path. ALL ticks here are SYNTHETIC: these are software/logic tests
ONLY and are NOT execution validation (that requires real Tickstory ticks)."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace as NS

from backtest.tick_execution import fill_entry_tick, find_first_exit_ticks, ohlc_ambiguous

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def tk(sec, bid, ask=None):
    return NS(timestamp=T0 + timedelta(seconds=sec), bid=D(str(bid)), ask=D(str(ask if ask is not None else bid + 0.3)))


class TestLongExits(unittest.TestCase):
    def test_target_first_then_stop_resolves_to_target_where_ohlc_said_stop(self):
        # ONE candle's range covers both 2395 (stop) and 2415 (TP1). OHLC adapter
        # would say SL first; the real path touched TP1 first.
        ticks = [tk(0, 2400), tk(10, 2416), tk(20, 2390)]
        r = find_first_exit_ticks(True, D("2395"), D("2415"), D("2500"), ticks)
        self.assertEqual((r.reason, r.exit_price, r.tick_index), ("TP1_HIT", D("2415"), 1))

    def test_stop_first_then_target(self):
        r = find_first_exit_ticks(True, D("2395"), D("2415"), None, [tk(0, 2400), tk(5, 2394), tk(9, 2416)])
        self.assertEqual((r.reason, r.exit_price), ("SL_HIT", D("2394")))  # gap: worse of tick/stop

    def test_stop_fill_at_stop_price_when_tick_lands_exactly_on_it(self):
        r = find_first_exit_ticks(True, D("2395"), D("2415"), None, [tk(0, 2395)])
        self.assertEqual(r.exit_price, D("2395"))

    def test_slippage_is_adverse(self):
        r = find_first_exit_ticks(True, D("2395"), D("2415"), None, [tk(0, 2394.5)], slippage=D("0.2"))
        self.assertEqual(r.exit_price, D("2394.3"))

    def test_long_uses_bid_not_ask(self):
        # ask reaches the target but bid does not -> no exit for a LONG
        self.assertIsNone(find_first_exit_ticks(True, D("2395"), D("2415"), None, [tk(0, 2414.8, 2415.1)]))

    def test_same_tick_stop_and_target_stop_wins(self):
        r = find_first_exit_ticks(True, D("2395"), D("2395"), None, [tk(0, 2395)])
        self.assertEqual(r.reason, "SL_HIT")

    def test_tp2_checked_when_tp1_absent_post_tp1_phase(self):
        r = find_first_exit_ticks(True, D("2405"), None, D("2500"), [tk(0, 2450), tk(1, 2500)])
        self.assertEqual((r.reason, r.exit_price), ("TP2_HIT", D("2500")))

    def test_no_exit(self):
        self.assertIsNone(find_first_exit_ticks(True, D("2395"), D("2415"), D("2500"), [tk(0, 2400), tk(1, 2410)]))


class TestShortExits(unittest.TestCase):
    def test_short_uses_ask(self):
        # bid below the target but ask not -> no target for a SHORT
        self.assertIsNone(find_first_exit_ticks(False, D("2410"), D("2390"), None, [tk(0, 2389.8, 2390.1)]))
        r = find_first_exit_ticks(False, D("2410"), D("2390"), None, [tk(0, 2389.5, 2389.9)])
        self.assertEqual((r.reason, r.exit_price), ("TP1_HIT", D("2390")))

    def test_short_stop_gap_and_slippage(self):
        r = find_first_exit_ticks(False, D("2410"), D("2390"), None, [tk(0, 2411.0, 2411.3)], slippage=D("0.1"))
        self.assertEqual((r.reason, r.exit_price), ("SL_HIT", D("2411.4")))


class TestOrderingAndEntry(unittest.TestCase):
    def test_backwards_ticks_rejected(self):
        with self.assertRaises(ValueError):
            find_first_exit_ticks(True, D("1"), D("99999"), None, [tk(5, 2400), tk(1, 2400)])

    def test_entry_is_first_tick_strictly_after_decision_at_ask_for_long(self):
        e = fill_entry_tick(True, [tk(0, 2400, 2400.3), tk(1, 2401, 2401.3), tk(2, 2402, 2402.3)], T0 + timedelta(seconds=0))
        self.assertEqual((e.price, e.tick_index), (D("2401.3"), 1))

    def test_entry_short_at_bid_with_slippage(self):
        e = fill_entry_tick(False, [tk(1, 2401, 2401.3)], T0, slippage=D("0.1"))
        self.assertEqual(e.price, D("2400.9"))

    def test_entry_none_if_no_later_tick(self):
        self.assertIsNone(fill_entry_tick(True, [tk(0, 1)], T0))

    def test_ohlc_ambiguity_detector(self):
        c = NS(high=D("2420"), low=D("2390"))
        self.assertTrue(ohlc_ambiguous(True, D("2395"), [D("2415")], c))
        self.assertFalse(ohlc_ambiguous(True, D("2385"), [D("2415")], c))


class TestPipelineTickPath(unittest.TestCase):
    """Wiring tests via the pipeline fixture. SYNTHETIC ticks: non-validation."""

    def _ticks_for(self, candle):
        step = (candle.timestamp_close - candle.timestamp_open) / 4
        path = [candle.open, candle.high, candle.low, candle.close]
        return [NS(timestamp=candle.timestamp_open + step * i, bid=p, ask=p + D("0.3")) for i, p in enumerate(path)]

    def _run(self, provider):
        import tests.test_backtest_pipeline as T
        import tests.test_phase_a_correctness as P
        p = P._setup_pipeline(min_rr="1")
        p._tick_window_provider = provider
        T._run_through(p, T.CANDLES, len(T.CANDLES))
        return p

    def test_tick_provider_is_used_and_counted(self):
        import tests.test_backtest_pipeline as T
        by_open = {c.timestamp_open: c for c in T.CANDLES}
        p = self._run(lambda a, b: self._ticks_for(by_open[a]))
        self.assertGreater(p.tick_exit_resolutions, 0)
        self.assertEqual(p.tick_fallback_to_ohlc, 0)
        self.assertEqual(len(p.state.completed_trades), 1)

    def test_candles_without_ticks_fall_back_to_ohlc_and_are_counted(self):
        p = self._run(lambda a, b: [])
        self.assertGreater(p.tick_fallback_to_ohlc, 0)
        self.assertEqual(p.tick_exit_resolutions, 0)
        self.assertEqual(len(p.state.completed_trades), 1)  # same trade as the OHLC path

    def test_no_provider_means_pure_ohlc_behaviour(self):
        p = self._run(None)
        self.assertEqual((p.tick_exit_resolutions, p.tick_fallback_to_ohlc), (0, 0))


if __name__ == "__main__":
    unittest.main()
