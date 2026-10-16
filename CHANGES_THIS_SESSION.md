# Changes made this session

Full line-level diff against the uploaded `smc-trading-engine-milestone1.zip`
is in `full_diff.patch` at the root of this package. This file is the
narrative summary. Everything here is also recorded formally in
`docs/DECISIONS.md` under **TRAIL-01** and **RR-02** — read those entries
for full rationale; this is the short version.

Verification: full test suite passes clean — **506/506** (up from the
baseline 499; 7 new tests added, 3 existing tests updated for an
intentional, documented config change, zero tests weakened).

---

## 1. TRAIL-01 — Trailing-stop stale-swing bug (the real cause of
   walk_forward_6m_v3's 11 impossible-R trades)

**Root cause:** `TrailingEngineImpl.compute_new_stop`
(`strategy/trailing/trailing_engine.py`) picked trailing-stop candidates
by comparing raw swing prices against the current stop, with no check
that a candidate was still consistent with where the market actually is.
`backtest/pipeline.py` fed it the *entire* replay-to-date candle history
on every step, so a swing from a much earlier, unrelated price regime
could outrank a legitimate nearby one purely on price, producing a
trailing stop literally above live market price — which the very next
candle trivially breached, firing a meaningless "TRAILING_STOP_HIT" at
the stale level. This is confirmed, not theorized: directly reproduced
with a minimal synthetic scenario before any fix, and reproduced again
at a scale matching the real 6-month run (20,000+ intervening candles).

**Fix (two layers):**
- **The actual correctness guarantee**, inside `compute_new_stop` itself:
  a candidate is now rejected outright if the most recent candle would
  already breach it. This holds for any caller, regardless of cause.
- **A secondary bound**, in `pipeline.py`'s `_manage_open_position`: the
  candle window passed to `compute_new_stop` is now limited to ~20
  candles before the position's entry onward (`TRAILING_SWING_LOOKBACK_MARGIN`),
  instead of the full history. This also removes a real O(full-history)
  rescan cost paid on every `POST_TP1` step — a genuine, separate
  performance problem on long runs.

**Files touched:** `strategy/trailing/trailing_engine.py`,
`backtest/pipeline.py`, new `tests/test_trailing_stale_swing_regression.py`.

**Retraction from the previous session:** an earlier manual cross-check
suggested a second bug — HTF regime/direction mismatches in ~19 of 61
trades. Direct, exhaustive unit testing of the actual gate function this
session (all 8 regime/direction combinations) found it airtight. That
finding was wrong; it came from a flawed ad-hoc reconstruction script,
not a real engine bug. No code changed for this — it's a correction to
the record, not a fix.

## 2. Standing plausibility guard (new, runs on every future backtest)

`backtest/statistics.py` gained `check_plausibility()`: flags any closed
trade whose `realized_r` magnitude exceeds 20R (a real, evidence-based
threshold — it clears every genuine trade in `walk_forward_6m_v3` except
the 11 now-explained bugged ones). `scripts/run_backtest.py` now runs
this automatically on every run, writes `plausibility_report.json`
alongside `statistics_final.json`, and prints a loud warning to the
console/run log if anything is flagged. This is advisory only — it never
excludes a trade from the real statistics, it just makes sure a future
bug of any kind can't hide behind good-looking numbers without a human
noticing, which is what happened three times running before this.

**Files touched:** `backtest/statistics.py`, `scripts/run_backtest.py`,
new tests in `tests/test_backtest_statistics.py`.

## 3. RR-02 — `minimum_rr` raised from 2.0 to 3.8

The agreed first tuning pass, applied only after both TRAIL-01 fixes
were in and verified — so this is tuning against a run no longer
corrupted by either bug. Target is 3.8; **3.5 is the agreed fallback**
if 3.8 leaves too few trades (rough floor: ~15-20) over a 6-month window
to mean anything statistically. Re-run and check trade count before
treating 3.8 as final.

**Expected tradeoff, stated before seeing the result:** fewer trades,
larger average win, likely a *lower* win rate (high-RR setups are
structurally harder to close as winners before a stop triggers). This is
a deliberate quality-over-volume choice, not a free improvement — if the
actual result doesn't match this shape, that's worth a closer look.

**Files touched:** `core/config/registry.py`. Two existing tests needed
updates because they hardcoded the old `2.0` default or depended on a
fixture whose RR no longer cleared the new gate — in both cases the test
was adjusted to still test what it was meant to test, not weakened to
dodge the new threshold (see inline comments at each change for exactly
why). `tests/test_config.py`, `tests/test_backtest_pipeline.py`.

---

## What to do next

1. Run `pytest` yourself to confirm 506/506 on your machine too (should
   match exactly, nothing environment-specific was touched).
2. Re-run `walk_forward_6m` as a clean **v4**, same window as
   `walk_forward_6m_v3`, on this fixed + retuned code.
3. Check `plausibility_report.json` from that run first — it should be
   empty. If anything is flagged, stop and investigate before trusting
   any other number from the run.
4. Compare v4's trade count against the ~15-20 floor — if it's thinner
   than that, that's the signal to fall back to `minimum_rr = 3.5` per
   RR-02's documented plan, not a reason to declare the strategy broken.
5. Once v4 looks clean, that's the real, trustworthy baseline to start
   instrument-expansion testing from (the 6-instrument plan from
   earlier in this conversation).
