# PHASE A CHANGE LOG — correctness / causality / reproducibility rebuild

Scope per owner decisions (2026-10-03): make the EXISTING intended strategy
causal, correct, reproducible and auditable. **No optimisation, no parameter
tuning, no loosened filters.** Phase B items are deferred (see §8).

> **READ FIRST — validation status.** This sandbox had no network, so real
> `pydantic`/`pytest`/`hypothesis` could not be installed. All tests were run
> under a minimal pydantic/pytest stand-in written for this session
> (NOT shipped). Calibration: on the ORIGINAL unmodified zip it reproduces
> 505/506 passes; the one miss is a wall-clock test
> (`test_candle_store_scaling ... elapsed < 5.0`) that is slow in this
> sandbox. **You must run `pytest` yourself with real dependencies before
> trusting any of this.** Section 7 lists exactly what was and was not run.

---

## 1. "Changes that alter strategy behavior"
(These change WHICH trades exist or their economics. They are the reason Version A
trades will not match any pre-Phase-A run. Each is an owner-approved decision or an
audit-mandated correction, not a tuning.)

| # | Change | Effect |
|---|--------|--------|
| S1 | **Causal touch/confirmation start (P0.1).** Touch search begins at the first M5 candle that OPENS at/after `max(zone.creation_timestamp, setup.created_at)`, no longer at the zone's origin candle. | Setups that previously "confirmed" using pre-zone candles now confirm later or never. Fewer/different trades, earlier-than-possible entries removed. |
| S2 | **Minimum stop enforced by rejection (owner decision 4).** Setups with stop distance < 0.25 ATR(M5) are rejected `STOP_BELOW_MINIMUM`; stop is never clamped. Registry value 0.25 unchanged. | New rejection reason; previously the setting was dead. |
| S3 | **Real TP1 partial close (P0.2).** TP1 closes `tp1_allocation_pct` (30%) of the volume; runner managed with remaining volume. | Realized R/P&L of every trade that reaches TP1 changes (old R overstated: whole original volume was credited at the runner exit price). |
| S4 | **Real account ledger gates (P0.3).** Daily-loss (2%), trades/day (3) and consecutive-loss lock (3) now evolve from real realized P&L. Consecutive-loss lock and daily counters reset at 00:00 UTC. | Gates that previously could never trip now can; may block trades. |
| S5 | **RR 3.8 is the single source (P0.5).** A run with no flags uses registry 3.8, not the old CLI default 1.5. | Runs launched "without flags" previously tested 1.5. |
| S6 | **Warm-up before the window (3.2).** No trade can open before `--window-start`; H4 bias is advanced chronologically through a warm-up (default 30 days) instead of voted from the first 10 candles of the window. | Initial HTF context at window start changes. |
| S7 | **FIXED_RISK default sizing** (0.5% of the initial/reference balance; COMPOUNDING selectable). | Position sizes no longer drift with equity in research runs; previously size was always from the static initial equity as well, so for FIXED_RISK the *sizing* is unchanged, but it is now explicit. |

## 2. "Correctness/implementation fixes that should not intentionally alter strategy behavior"

| # | Fix |
|---|-----|
| C1 | `AccountLedger` (balance, equity, daily start equity, daily realized P&L, trades-today, consecutive losses, peak equity, account drawdown %), `backtest/accounting.py`. |
| C2 | Per-leg commission (round-turn on each leg's volume; sums to the previous total). |
| C3 | `TradeRecord` extended (`tp1_executed`, `tp1_exit_price/timestamp`, `tp1_volume`, `runner_volume`, `tp1_split_reason`); `position_size` = ORIGINAL volume. If a 30/70 split would be below `min_volume` the split is skipped and DISCLOSED (`SPLIT_BELOW_MIN_VOLUME`), never silently rounded. |
| C4 | Reference engine (`market_data/replay/reference_m5_layer.py`) + `ReplayPipeline(m5_engine="REFERENCE"|"FAST")`; `--engine` CLI flag. |
| C5 | Tick execution infrastructure (`backtest/tick_execution.py`) + optional `tick_window_provider` in the pipeline; counters for tick resolutions / OHLC fallbacks / OHLC-ambiguous candles. |
| C6 | Runner rewrite: CLI owns no trading defaults; overrides explicit, printed, recorded; `run_metadata.json` with full config snapshot + hash; ledger-backed risk; old `_risk_state_tracker` removed. `--risk-pct` is now documented in percent (0.5 = 0.5%); the old help text ("0.01 for 1%") was wrong. |
| C7 | Config: `touch_confirmation_timeout_minutes` and `sweep_to_displacement_max_age_candles` added, **0 = disabled** (Phase A baseline == current behavior); `risk.sizing_mode`, `risk.reset_consecutive_losses_at_utc_midnight`. |
| C8 | `RiskSnapshot.sizing_equity` (optional) so sizing equity can differ from mark-to-market equity used by the daily-loss gate. |
| C9 | `detect_mss(..., max_sweep_to_displacement_age=None)` optional arg (disabled by default). |

## 3. Mapping to every PDF item

| PDF item | Status |
|----------|--------|
| P0.1 zone confirmation causality | DONE (S1). Detector-level + replay-level causality tests. |
| P0.2 TP1 partial close | DONE (S3, C2, C3). 30/70, 50/50, 100/0, 0/100 tested. |
| P0.3 account ledger / daily loss | DONE (S4, C1). UTC reset explicit + tested (23:59:59 vs 00:00:00). |
| P0.4 tick-faithful execution | INFRASTRUCTURE DONE (C5); **NOT validated** (no real ticks). Entry-at-first-tick helper exists but is intentionally NOT wired (would change entry prices). Trailing updates remain candle-close based. |
| P0.5 CLI vs registry drift | DONE (S5, C6). |
| 3.1 reference vs FAST engine | DONE structurally (C4); equivalence PASSES on synthetic data with the cutoff non-binding; see §6 for the important cutoff finding. Long-window real-data equivalence PENDING. |
| 3.2 initial H4 warm-up | DONE (S6). |
| 3.3 split config | DONE (C6): one `EngineConfig`; runner/pipeline read it. `RunConfig` retains only tick/volume plumbing (tick_size 0.01, tick_value 1.00, step/min/max volume) — still hard-coded in the runner, see §9. |
| Phase A items 8 (loss-reset semantics), 9 (min stop) | DONE (S4, S2). |
| 5.1 touch timeout, 5.2 sweep→displacement age | Configurable, DISABLED (C7, C9), tested on and off. |
| 5.3–5.7, Phase B list, Phase C instrumentation | **DEFERRED** per owner (see §8). |

## 4. Test change log (existing tests that were changed)
No test was deleted. Only `tests/test_backtest_pipeline.py` changed. Each change
was derived by hand from the corrected causal rule BEFORE being run (derivation is
in the file header comment); none was edited merely to go green.

| Test | Old behaviour | New behaviour | Reason | Type |
|------|---------------|---------------|--------|------|
| `TestReplayPipelineHappyPath::test_confirmation_and_entry_at_expected_candle` | confirm+entry at step index 5 (c2), entry 2400.5 | step index 8 (c5), entry 2401.5; also asserts no `SETUP_CONFIRMED` earlier | Old run used the FVG's own forming candle (c-1) as part of the touch (origin-candle start). Corrected sequence = same touch/pivot/break as the Milestone-8 standalone fixture (c0/c2/c3/c5). | Bug fix (P0.1) |
| `...::test_tp1_then_trailing_then_trailing_stop_exit` | `realized_r = (2409.9-entry)/(entry-stop)` = whole original volume at runner exit | weighted: 0.02 lots @TP1 2415 + 0.05 @2409.9 over original 0.07-lot risk ⇒ R≈1.5165, net $69; asserts the new split fields | Old R credited the runner exit price to 100% of volume although 30% was supposed to close at TP1. | Bug fix (P0.2) (+ entry from P0.1) |
| `TestEntryCandleExcludedFromExitCheck::*`, `TestOneOpenPositionRule::*`, `TestDuplicateStepIdempotency::*`, `TestReplayStatePersistsAcrossSteps::*`, `TestExitEvaluationOnlySeesCurrentCandle::*` | ran 6 steps to reach entry | run `ENTRY_STEPS` (=9) steps; the "next step" in two of them is now c6 (TP1 candle) so the assertion became "TP1 partial close leaves the position open" | Consequence of S1 only; assertions' intent unchanged. | Bug fix (P0.1) |
| `TestDiagnosticLoggingDoesNotAffectDecisions::*` (2) | 6 steps | `ENTRY_STEPS` | same | Bug fix (P0.1) |
| `TestRealTargetSelectorWiredByDefault::*` | micro-swing pivot was the FVG-forming candle (base+2, high 2420) | fixture restructured so the pivot (2420), its confirmation and the break (entry 2420.5) come from post-zone candles; TP1 2437.01 / TP2 2470 / stop 2414 unchanged | The original fixture depended on the pre-zone pivot. | Bug fix (P0.1, fixture) |

New tests: `test_backtest_accounting.py` (26), `test_phase_a_correctness.py` (44),
`test_reference_fast_equivalence.py` (5), `test_tick_execution.py` (18),
`test_run_backtest_runner.py` (9).
Mutation checks (re-introducing the old bugs makes the new tests fail): old
origin-candle start → 21 failures; no TP1 leg → 7; static equity → 4.

Areas you asked to see explicitly: origin-candle timing (§4 rows 1,3–5); TP1
quantity (row 2 + `TestPartialCloseThroughPipeline`); account/equity ledger and daily
loss (`TestLedgerDrivenRisk`, `test_backtest_accounting`); consecutive-loss reset
(`test_utc_midnight_resets_*`, `test_consecutive_loss_lock_resets_at_utc_midnight`);
minimum stop (`TestMinimumStopEnforcement`); RR config (`TestSingleConfigAuthority`,
`TestConfigDefaults`); reference/FAST (`test_reference_fast_equivalence.py`).

## 5. Effective configuration (Phase A defaults = registry)
minimum_rr **3.8**, minimum_score 70, tp1_allocation_pct 30, minimum_stop_atr 0.25 (now enforced),
max_stop_atr 3.5, min_target_atr 1.0, risk_per_trade_pct 0.5 (% of equity/reference balance),
daily_loss_limit_pct 2.0, max_consecutive_losses 3, max_trades_per_day 3,
sizing_mode FIXED_RISK, reset at 00:00 UTC True, touch_confirmation_timeout 0 (off),
sweep_to_displacement_max_age 0 (off), setup_expiry 48 M15, max_pending_setups 3.
Not changed: every other registry value. **The authoritative config hash is the one a real run
writes to `run_metadata.json`** (the hash printed under my pydantic stand-in is not the real
pydantic hash and is deliberately not quoted).

## 6. Reference vs FAST — results and an important finding
* With the FAST equal-level lookback set ≥ history length: swings, pools, sweeps, displacement,
  MSS, FVG and order-block outputs are IDENTICAL on full attributes at 5 checkpoints over two
  synthetic series (700 and 500 candles), and the pipeline event stream, setup timestamps,
  touch/confirmation, decision, stop/target and TradeRecord are identical on the fixture.
* **The default FAST cutoff is NOT behaviour-neutral.** With the default-style lookback of 100
  candles on a 700-candle synthetic series FAST found 46 equal-level pools vs 299 in REFERENCE,
  163 vs 319 sweeps, 82 vs 153 MSS events. A random-walk series has far more equal levels than
  real gold, so the real-data magnitude is UNKNOWN — but it means FAST results must not be
  accepted until a long-window comparison on your data is run.
* The 5,000-candle unswept-pool and 3,000-candle pending-major-swing cutoffs are not reachable by
  test-sized series and are NOT validated.

## 7. What was and was not executed
Executed (under the pydantic/pytest stand-in): full suite 607 pass / 1 fail (the wall-clock test
that also fails on the pristine zip here) = 608 tests vs 506 originally; the new accounting
tests also pass under plain `python -m unittest`; a synthetic-tick end-to-end run of the new
runner (FAST engine, 2 days, with and without gate overrides).
**Not executed:** real `pydantic` v2 / `pytest` / `hypothesis`; the REFERENCE engine through the
runner; anything on real market data; any tick-execution validation; long-window equivalence.
**Required from you:** run `pytest` with real dependencies (expect the stand-in could differ on
pydantic validation details); supply real Tickstory data (a representative 1–2 month slice
including a volatile month is enough to begin; one 30-day warm-up is automatic).

## 8. Deferred (Phase B / owner-confirmed)
H4-alignment rename, removal of H1/OTE/session weights (and the explicit pre/post score
composition report), premium/discount dealing-range redesign, TP2-beyond-TP1 hierarchy,
D1 detector, rejection-funnel/MAE-MFE instrumentation (Phase C), session/news.
Current score composition is UNCHANGED: d1_h4 15, h1 10, premium_discount 10, sweep 15, mss 15,
displacement 10, fvg 5, ob 5, ote 5, session 5 (deferred, 0 available), target_liquidity 10.

## 9. Unresolved issues / things I found but did NOT change
1. **Data range:** the zip has no market data. Per your run logs the file is
   `XAUUSD_ticks_2025-09-01_2026-08-31.csv` (code comments say ~84.47M rows, 6.68 GB — I earlier
   said ~45M; that was wrong). Exact M5 range/candle count cannot be reported until data is supplied.
2. **Real Tickstory data in the project/environment: NO.** Only the reader exists.
3. In REALISTIC cost mode the OHLC exit path (`find_first_exit`) is called without
   spread/slippage (pre-existing): entry costs are applied, exit spread/slippage are not.
   Left untouched to avoid an unannounced behaviour change; the tick path does apply slippage.
4. The legacy single-setup `backtest/engine.py` (Milestone 8) only received the causality
   argument; it still uses single-exit R accounting. The research runner uses `ReplayPipeline`.
5. `RunConfig` (tick_size 0.01, tick_value 1.00, volume step/min/max) is still hard-coded in the
   runner; these are broker-contract plumbing, not strategy rules, but they should be moved to
   config/broker profile before live use.
6. If a 30/70 split is impossible at `min_volume`, the position is NOT partially closed (disclosed
   per trade). With 0.5% risk and small stops this can be common — check `tp1_split_reason`.
7. Trailing-stop updates remain computed at candle close; tick mode resolves exits against the
   stop in force at the start of each candle.
8. Intentional causality side-effect: a candle that straddles the availability instant is
   excluded entirely (conservative).
9. This is a research baseline; the PDF's own conclusion stands: not ready for real capital.

---
## ADDENDUM (final hardening pass, 2026-10-04) — supersedes the earlier numbers above
See **PHASE_A_COMPLETION_REPORT.md** (authoritative). Changes since the first Phase A delivery:
* §9 item 3 is RESOLVED: exit spread/slippage are now applied on the OHLC path (FRICTIONLESS unchanged).
* Added `TP2_NOT_BEYOND_TP1` rejection guard (rejects TP2 strictly closer than TP1; equal allowed - open decision).
* Runner default engine is now REFERENCE; FAST runs are labelled NON-CANONICAL; non-zero costs under FRICTIONLESS are refused.
* TradeRecord gained audit fields (engine, zone creation, setup created/expiry, touch, confirmation, balances, cost assumptions).
* New `scripts/baseline_report.py` and `scripts/compare_runs.py`.
* Test count (stand-in, NOT real pytest): 623 collected, 622 passed, 1 failed (pre-existing wall-clock test).
