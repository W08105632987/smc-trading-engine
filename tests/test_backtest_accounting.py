"""PHASE A tests for backtest/accounting.py (P0.2 partial close, P0.3 ledger).

Written as plain unittest.TestCase classes so they run under pytest AND
under ``python -m unittest`` (accounting.py has no third-party deps)."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from backtest.accounting import (
    AccountLedger, SizingMode, combine_legs, initial_dollar_risk, leg_pnl,
    split_tp1_volume, utc_day,
)

TICK, TICK_VALUE = D("0.01"), D("1.00")  # XAUUSD-style: $1 per 0.01 move per lot


def t(day, hour=12, minute=0):
    return datetime(2026, 1, day, hour, minute, tzinfo=timezone.utc)


class TestTP1Split(unittest.TestCase):
    def test_30_70(self):
        s = split_tp1_volume(D("1.00"), D("30"), D("0.01"), D("0.01"))
        self.assertTrue(s.executed)
        self.assertEqual((s.tp1_volume, s.remaining_volume), (D("0.30"), D("0.70")))

    def test_50_50(self):
        s = split_tp1_volume(D("1.00"), D("50"), D("0.01"), D("0.01"))
        self.assertEqual((s.tp1_volume, s.remaining_volume), (D("0.50"), D("0.50")))

    def test_100_0_closes_everything(self):
        s = split_tp1_volume(D("0.40"), D("100"), D("0.01"), D("0.01"))
        self.assertTrue(s.executed)
        self.assertEqual((s.tp1_volume, s.remaining_volume), (D("0.40"), D("0")))

    def test_0_100_closes_nothing(self):
        s = split_tp1_volume(D("0.40"), D("0"), D("0.01"), D("0.01"))
        self.assertFalse(s.executed)
        self.assertEqual((s.tp1_volume, s.remaining_volume), (D("0"), D("0.40")))

    def test_floors_never_rounds_up(self):
        s = split_tp1_volume(D("0.19"), D("30"), D("0.01"), D("0.01"))  # 0.057 -> 0.05
        self.assertEqual(s.tp1_volume, D("0.05"))
        self.assertEqual(s.tp1_volume + s.remaining_volume, D("0.19"))

    def test_split_below_min_volume_is_disclosed_not_silent(self):
        s = split_tp1_volume(D("0.03"), D("30"), D("0.01"), D("0.01"))  # 0.009 -> 0.00 < min
        self.assertFalse(s.executed)
        self.assertEqual(s.reason, "SPLIT_BELOW_MIN_VOLUME")
        self.assertEqual(s.remaining_volume, D("0.03"))

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            split_tp1_volume(D("0"), D("30"), D("0.01"), D("0.01"))
        with self.assertRaises(ValueError):
            split_tp1_volume(D("1"), D("130"), D("0.01"), D("0.01"))


class TestLegAccounting(unittest.TestCase):
    def test_weighted_realized_r_30_70_long(self):
        # entry 2000, stop 1990 (risk 10.00), TP1 2010 (+1R), exit of runner 2030 (+3R)
        entry, stop = D("2000.00"), D("1990.00")
        vol = D("1.00")
        risk = initial_dollar_risk(entry, stop, vol, TICK, TICK_VALUE)
        self.assertEqual(risk, D("1000.00"))
        split = split_tp1_volume(vol, D("30"), D("0.01"), D("0.01"))
        l1 = leg_pnl(True, entry, D("2010.00"), split.tp1_volume, TICK, TICK_VALUE)
        l2 = leg_pnl(True, entry, D("2030.00"), split.remaining_volume, TICK, TICK_VALUE)
        c = combine_legs([l1, l2], risk)
        # 0.3 * 1R + 0.7 * 3R = 2.4R  (NOT 3R, which the old full-volume accounting gave)
        self.assertEqual(c.realized_r, D("2.4"))
        self.assertEqual(c.total_volume, vol)

    def test_weighted_realized_r_short_with_stop_after_tp1(self):
        entry, stop = D("2000.00"), D("2010.00")
        vol = D("1.00")
        risk = initial_dollar_risk(entry, stop, vol, TICK, TICK_VALUE)
        split = split_tp1_volume(vol, D("30"), D("0.01"), D("0.01"))
        l1 = leg_pnl(False, entry, D("1990.00"), split.tp1_volume, TICK, TICK_VALUE)   # +1R on 30%
        l2 = leg_pnl(False, entry, D("2000.00"), split.remaining_volume, TICK, TICK_VALUE)  # BE on 70%
        c = combine_legs([l1, l2], risk)
        self.assertEqual(c.realized_r, D("0.3"))

    def test_full_stop_is_minus_one_r(self):
        entry, stop, vol = D("2000.00"), D("1990.00"), D("0.50")
        risk = initial_dollar_risk(entry, stop, vol, TICK, TICK_VALUE)
        c = combine_legs([leg_pnl(True, entry, stop, vol, TICK, TICK_VALUE)], risk)
        self.assertEqual(c.realized_r, D("-1"))

    def test_commission_charged_round_turn_per_leg_and_sums_to_old_total(self):
        entry, vol = D("2000.00"), D("1.00")
        split = split_tp1_volume(vol, D("30"), D("0.01"), D("0.01"))
        l1 = leg_pnl(True, entry, entry, split.tp1_volume, TICK, TICK_VALUE, D("3.50"))
        l2 = leg_pnl(True, entry, entry, split.remaining_volume, TICK, TICK_VALUE, D("3.50"))
        c = combine_legs([l1, l2], D("1000"))
        self.assertEqual(c.commission, D("3.50") * vol * 2)  # identical to old single-exit total
        self.assertEqual(c.net_pnl, -c.commission)
        self.assertEqual(c.realized_r, D("-0.007"))

    def test_combine_requires_legs(self):
        with self.assertRaises(ValueError):
            combine_legs([], D("1"))


class TestLedgerSizingModes(unittest.TestCase):
    def test_fixed_risk_ignores_growth(self):
        led = AccountLedger(D("10000"), SizingMode.FIXED_RISK)
        led.on_realized(t(1), D("500"))
        self.assertEqual(led.balance, D("10500"))
        self.assertEqual(led.sizing_equity(), D("10000"))

    def test_compounding_follows_equity_incl_floating(self):
        led = AccountLedger(D("10000"), SizingMode.COMPOUNDING)
        led.on_realized(t(1), D("-200"))
        self.assertEqual(led.sizing_equity(), D("9800"))
        self.assertEqual(led.sizing_equity(D("-50")), D("9750"))

    def test_default_mode_is_fixed_risk(self):
        self.assertEqual(AccountLedger(D("1")).sizing_mode, SizingMode.FIXED_RISK)

    def test_nonpositive_balance_rejected(self):
        with self.assertRaises(ValueError):
            AccountLedger(D("0"))


class TestLedgerDailyLossAndReset(unittest.TestCase):
    def test_equity_and_daily_pnl_evolve(self):
        led = AccountLedger(D("10000"))
        led.roll_day(t(1))
        led.on_trade_opened(t(1, 9))
        led.on_realized(t(1, 10), D("-120"))   # partial close
        led.on_realized(t(1, 11), D("-30"))    # final leg
        led.on_trade_closed(t(1, 11), D("-150"))
        s = led.snapshot(t(1, 12))
        self.assertEqual(s.account_equity, D("9850"))
        self.assertEqual(s.daily_starting_equity, D("10000"))
        self.assertEqual(s.daily_realized_pnl, D("-150"))
        self.assertEqual(s.trades_today_count, 1)
        self.assertEqual(s.consecutive_losses_count, 1)
        # the gate input the risk engine uses (starting - equity) is now non-zero:
        self.assertEqual(s.daily_starting_equity - s.account_equity, D("150"))

    def test_daily_loss_can_reach_two_percent_limit(self):
        led = AccountLedger(D("10000"))
        led.roll_day(t(2))
        for h in (9, 11):
            led.on_trade_opened(t(2, h))
            led.on_realized(t(2, h + 1), D("-100"))
            led.on_trade_closed(t(2, h + 1), D("-100"))
        s = led.snapshot(t(2, 14))
        limit = s.daily_starting_equity * D("2") / D("100")
        self.assertFalse((s.daily_starting_equity - s.account_equity) < limit)  # gate would trip

    def test_utc_midnight_resets_daily_counters_and_loss_lock(self):
        led = AccountLedger(D("10000"))
        for h in (8, 9, 10):
            led.on_trade_opened(t(3, h))
            led.on_realized(t(3, h), D("-10"))
            led.on_trade_closed(t(3, h), D("-10"))
        self.assertEqual(led.consecutive_losses, 3)
        self.assertEqual(led.trades_today, 3)
        # 23:59:59 UTC same day: no reset
        self.assertFalse(led.roll_day(datetime(2026, 1, 3, 23, 59, 59, tzinfo=timezone.utc)))
        self.assertEqual(led.consecutive_losses, 3)
        # 00:00:00 UTC next day: reset
        self.assertTrue(led.roll_day(datetime(2026, 1, 4, 0, 0, 0, tzinfo=timezone.utc)))
        self.assertEqual((led.consecutive_losses, led.trades_today, led.daily_realized_pnl), (0, 0, D("0")))
        self.assertEqual(led.daily_starting_equity, D("9970"))  # new day starts from real balance

    def test_loss_lock_reset_is_configurable(self):
        led = AccountLedger(D("10000"), reset_consecutive_losses_at_utc_midnight=False)
        led.on_trade_opened(t(3)); led.on_realized(t(3), D("-1")); led.on_trade_closed(t(3), D("-1"))
        led.roll_day(t(4))
        self.assertEqual(led.consecutive_losses, 1)
        self.assertEqual(led.trades_today, 0)

    def test_non_utc_aware_datetime_uses_utc_day(self):
        tz_plus3 = timezone(timedelta(hours=3))
        local = datetime(2026, 1, 5, 2, 0, tzinfo=tz_plus3)  # == 2026-01-04 23:00 UTC
        self.assertEqual(utc_day(local).isoformat(), "2026-01-04")

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            utc_day(datetime(2026, 1, 1, 0, 0))

    def test_time_cannot_go_backwards_across_days(self):
        led = AccountLedger(D("10000"))
        led.roll_day(t(5))
        with self.assertRaises(ValueError):
            led.roll_day(t(4))

    def test_win_resets_consecutive_losses_within_day(self):
        led = AccountLedger(D("10000"))
        for pnl in (D("-1"), D("-1"), D("5")):
            led.on_trade_opened(t(6)); led.on_realized(t(6), pnl); led.on_trade_closed(t(6), pnl)
        self.assertEqual(led.consecutive_losses, 0)

    def test_breakeven_counts_as_loss(self):
        led = AccountLedger(D("10000"))
        led.on_trade_closed(t(6), D("0"))
        self.assertEqual(led.consecutive_losses, 1)

    def test_drawdown_tracking(self):
        led = AccountLedger(D("10000"))
        led.on_realized(t(7, 1), D("1000"))   # peak 11000
        led.on_realized(t(7, 2), D("-550"))   # equity 10450, dd 550 = 5%
        self.assertEqual(led.peak_equity, D("11000"))
        self.assertEqual(led.max_drawdown_abs, D("550"))
        self.assertEqual(led.max_drawdown_pct, D("5"))
        led.mark_to_market(D("-1100"))        # floating dip: 10450-1100 = 9350, dd 1650
        self.assertEqual(led.max_drawdown_abs, D("1650"))


if __name__ == "__main__":
    unittest.main()
