# PHASE A COMPLETION REPORT (final hardening pass)

Date: 2026-10-04. Version label: **Phase-A-v2 (hardened, NOT yet baseline-run)**.

## 1. PHASE A STATUS
**NOT COMPLETE.**
The software hardening that could be done without real dependencies or real data is done and tested. Four of
your completion gates cannot be met in this environment, and per your instruction I stopped there instead of
fabricating results:
1. the **real** pytest/pydantic/hypothesis suite has not been run (no network, packages not installable);
2. there is **no real market data** in the project or environment, so the baseline backtest, the real-data
   REFERENCE-vs-FAST comparison, real-data manual trade inspection and real-data reproducibility were **not run**;
3. real-tick execution validation is pending (no ticks);
4. the report/ledger tooling is tested only on tiny SYNTHETIC runs that produced zero trades.

**No baseline backtest was run. No performance number in this package is a strategy result.**
Status can become COMPLETE WITH LIMITATIONS once items 1-2 are done (commands in §16).

## 2. REQUIREMENTS CHECKLIST
| Req | Status | Notes |
|-----|--------|-------|
| A Causality (zone availability boundary) | PASS (software, fixtures) | detector + replay tests; mutation-verified. Not yet shown on real data. |
| B risk defaults 0.5% / FIXED_RISK / 2% / 3 / 3 / UTC | PASS (software) | registry + ledger; compounding selectable, not default. |
| B real ledger (balance, equity, daily P&L, counts, peak, DD) | PASS (software) | `backtest/accounting.py`; floating P&L is NOT marked-to-market in the pipeline (see §11-L4). |
| C min stop 0.25 ATR reject / max 3.5 / structural stop preserved | PASS | rejection, never clamp. |
| D TP1 30% real partial close, split reason, TP1 price/vol/P&L recorded | PASS | 30/70, 50/50, 100/0, 0/100, below-min split. |
| D "TP2 not closer than TP1" | PASS (rejection guard only) | **OPEN CHOICE**: equal TP1==TP2 is allowed (your wording says "closer"; your earlier note said strictly beyond). Target selection NOT redesigned. |
| E RR 3.8 single source | PASS | CLI has no trading defaults; 1.5 gone. |
| F H4 warm-up 30d, no pre-window trades | PASS (software) | gate + metadata. H4 seed is still a 10-candle vote of the first loaded H4 bars (then advanced chronologically). |
| G tick infrastructure available | PASS (software) | synthetic ticks = software tests only. |
| G real-tick execution validation | **PENDING** | no real ticks. |
| G trailing candle-close documented | PASS | §11-L3, run metadata, report. |
| H REFERENCE canonical; FAST non-canonical | PASS | runner default is now REFERENCE; FAST runs are labelled NON-CANONICAL in log/metadata/report. |
| H real-data equivalence | **PENDING** | only synthetic comparison done (§6). |
| I timing controls configurable, disabled | PASS | tested on and off; baseline event stream unchanged when 0. |
| J research path = ReplayPipeline, engine identified | PASS | `research_path` + `engine` in metadata and in every TradeRecord. Legacy `backtest/engine.py` is not used by the runner. |
| Transaction costs: inspect, implement exit costs | PASS (software) | see §7. Real spread/slippage calibration PENDING (no data). |
| Real dependency validation | **FAIL/PENDING** | not possible here. |
| Reference-engine validation list (§4 of brief) | PARTIAL | see §5. |
| Real-data baseline backtest + report | **NOT RUN** | tooling ready. |
| Reproducibility on baseline | **NOT RUN** (software-level determinism PASS on fixture) | |

## 3. TEST RESULTS
* **Real pytest: NOT RUN.** Total/passed/failed/skipped/errors under real deps: **unknown**.
* Stand-in run (a minimal pydantic/pytest look-alike written for this sandbox, NOT shipped, NOT equivalent to real
  validation): **623 tests collected: 622 passed, 1 failed, 0 skipped, 0 errors.** The 1 failure
  (`test_candle_store_scaling ... elapsed < 5.0`) is a wall-clock performance test that also fails on the
  UNMODIFIED zip in this sandbox; I did not touch it. Calibration of the stand-in on the original zip: 505/506.
  Original suite was 506 (real pytest, per your audit); the stand-in's count is not directly comparable.
* Mutation/regression (re-introduce the old bug -> tests must fail):
  origin-candle touch start 21 failures; no TP1 partial leg 7; static equity 4; exit costs not passed 1;
  min-stop gate off 1; TP2 guard off 2; UTC loss-lock reset removed 2; warm-up gate off 1;
  FIXED_RISK sizing ignored 2. All detected.
* Deterministic tests: REFERENCE fixture run twice -> identical events, TradeRecord and ledger.
  Runner REFERENCE run twice on a tiny synthetic CSV -> `compare_runs` IDENTICAL, but that run has **0 trades**
  (weak); the fixture test (1 trade) is the meaningful one.
* Test-file changes: only `test_backtest_pipeline.py` changed (documented row by row in CHANGELOG_PHASE_A.md §4).
  New this pass: TP2-guard, exit-cost, audit-field, determinism, runner/report/compare tests.
  The new `engine` field made the FAST-vs-REFERENCE TradeRecord comparison exclude that one field (by design).

## 4. DATA VALIDATION
**No real market data exists in the project zip or in this environment.** The zip holds only prior run outputs.
Not available / not verifiable: start/end, row counts, size, true-Tickstory provenance, bid/ask, spread, timezone, gaps.
What the repo says (unverified claims): `XAUUSD_ticks_2025-09-01_2026-08-31.csv` in
`C:\Users\hp\Downloads\xauusd_working\` on your machine; code comments say ~84.47M rows, 6.68 GB; the reader expects
Tickstory columns Timestamp,Bid price,Ask price,Bid volume,Ask volume (bid/ask present => spread derivable per tick).
The reader treats timestamps as UTC; broker-offset has not been independently verified. Gap statistics are not
computed by any tool yet (limitation L6).

## 5. REFERENCE ENGINE VALIDATION
Validated on deterministic fixtures: causal ordering (no event precedes prerequisites), state transitions
(setup lifecycle, TP1 -> POST_TP1, trailing, close), ledger behaviour, TP1 partial accounting, daily-loss gate,
consecutive-loss lock and UTC reset (23:59:59 vs 00:00:00), sizing (FIXED_RISK vs COMPOUNDING), RR enforcement,
min-stop rejection, warm-up and trade-window boundary, commission per leg, audit metadata, determinism.
**Manual inspection of the fixture trade** (hand arithmetic matches the pipeline exactly):
zone created 00:00 -> touch starts 00:05 (first candle opening >= availability) -> micro-swing confirmed 00:20 ->
break/entry 00:30 @2401.5, stop 2395, TP1 2415, TP2 2500, 0.07 lots (floor(10000x0.5%/(650 ticks x $1)))
-> TP1 00:35: 0.02 lots @2415 (+$27.00) -> stop trails 2396.9 then 2409.9 -> runner 0.05 lots exits 01:10 @2409.9
(+$42.00). Net $69.00; dollar risk $45.50 (0.07 x 650); R = 69/45.5 = 1.5165. Balance 10,000 -> 10,069;
daily P&L +69; trades-today 1; consecutive losses 0. (Files: `fixture_trade_audit.json`.)
Fixture caveat: its hand-built Setup has `created_at` (23:50) earlier than its zone's creation (00:00);
this is a fixture artifact (real `_build_setup` uses the later of zone/MSS times), harmless because the
causality boundary takes the max, but it means this fixture does not exercise organic setup construction.
**Not validated:** REFERENCE on real data; organic zone-creation-time population beyond the fixture.

## 6. FAST ENGINE COMPARISON
Only synthetic comparison exists (random-walk series, 500-700 candles). FAST with its equal-level lookback set
>= history: identical on swings, pools, sweeps, displacement, MSS, FVG, OB (full attributes) and identical
pipeline event stream/trade (fixture). With a binding 100-candle lookback: **materially different** - 46 vs 299
equal-level pools, 163 vs 319 sweeps, 82 vs 153 MSS; stable (daily/weekly/swing) pools and swings identical. The
cause is the equal-level lookback cutoff. The 5,000-candle unswept and 3,000-candle pending-major cutoffs are not
reachable at test size and are untested. **Conclusion: FAST is NON-EQUIVALENT / unvalidated; REFERENCE is canonical.
No real-data comparison was possible.** (Comparison harness: `tests/test_reference_fast_equivalence.py`,
`scripts/compare_runs.py` for run-vs-run divergence by trade/event.)

## 7. EXECUTION / COST VALIDATION
What was modelled BEFORE this pass: entry spread+slippage (REALISTIC); per-leg commission (Phase A); exit
spread/slippage implemented in `find_first_exit` but **never passed** by the pipeline -> not applied.
What is modelled NOW (FRICTIONLESS results are unchanged):
* entry: long fills at break-candle close + spread + adverse slippage; short at close - slippage (candle data treated as BID);
* exits (OHLC path): long stop fills at stop - slippage; short exits use ask = bid + spread (stop trigger when
  bid >= stop - spread, fill stop + slippage; TP trigger likewise); targets fill at the target price (no price improvement);
* commission: per-lot one-way, charged round-turn on each leg's own volume;
* tick path (when ticks are supplied): bid/ask path, spread in the data, adverse slippage on stops, gaps fill at the worse price;
* the runner REFUSES non-zero costs under FRICTIONLESS (no silent ignoring).
**Not modelled / limits:** a constant spread (one number) rather than the time-varying spread; no
news/rollover widening; slippage is a fixed assumption; entry is at candle close, not first tick (helper exists, not
wired); trailing updates are applied at candle close; swap/financing not modelled; candle data is BID-only so the
OHLC spread treatment is an approximation. **Defensible-assumption note:** I did NOT invent cost numbers. The baseline
config in §15 therefore uses FRICTIONLESS (0/0/0), which is optimistic and must be stated alongside any result.
If you want a REALISTIC baseline, supply broker spread/commission/slippage (or the real tick data to derive them).

## 8. BASELINE BACKTEST RESULTS
**NOT RUN** (no data). Nothing to report. No placeholder numbers.

## 9. TRADE-LEVEL AUDIT RESULTS
Ledger schema implemented (`trade_ledger.csv` + `trades.jsonl`): trade id, engine, config hash, direction, setup /
zone-creation / touch / confirmation / entry timestamps, entry, stop, TP1, TP2, volumes, TP1 timestamp/price/split reason,
exit timestamp/price/reason, gross/commission/net, dollar risk, R, cost model and assumptions, balance before/after,
setup score, setup expiry, entry reason. Verified on the one fixture trade (§5). Real-trade sampling: PENDING.
Not captured per trade (limitation L8): equity before/after separate from balance (floating P&L is not tracked),
the individual score factors, and exit reason granularity beyond SL/TP1/TP2/TRAILING.

## 10. REPRODUCIBILITY RESULTS
Software determinism: PASS on the fixture (REFERENCE twice: identical events/trade/ledger) and on a 0-trade synthetic
runner run. Baseline reproducibility: **NOT RUN**. Config hash is deterministic (same config -> same hash; any param
change -> different hash; tested). Note the hash printed under my stand-in is not the real-pydantic hash.

## 11. KNOWN LIMITATIONS
L1 Real dependencies/test suite unverified. L2 No real data run. L3 Trailing stop evaluated at candle close; tick mode
resolves exits against the stop in force at the start of each candle, so intra-candle trail changes are not reflected.
L4 The ledger tracks realized P&L only; `equity` = balance (no floating P&L marking inside the pipeline), so the
2% daily-loss gate sees REALIZED losses, not open-position drawdown. L5 Entry at candle close (not first tick).
L6 No data-gap/quality report for the tick file; the gap policy remains pass-through. L7 FAST untrusted; REFERENCE is slow
(O(n^2) equal-level mapping per step): a 12-month run may take a very long time - run a 1-month slice first and time it.
L8 See §9. L9 `RunConfig` (tick size 0.01, tick value $1.00/lot-tick, volume step/min/max) is hard-coded in the runner:
confirm against your broker contract before trusting P&L in dollars. L10 If a 30/70 split is below min volume the position is
not split (disclosed per trade); with 0.5% risk and wide stops this can be frequent. L11 Legacy `backtest/engine.py`
still uses old single-exit accounting; it is NOT used by the research runner. L12 Phase B items remain untouched:
D1/H4 label, dead score factors (H1/OTE/session), premium/discount geometry, target selection. L13 Initial H4 seed is a
10-candle vote of the first loaded H4 bars. L14 The 5,000/3,000-candle FAST cutoffs are untested.

## 12. UNRESOLVED ISSUES / OPEN OWNER DECISIONS
1. Supply real data (slice) + allow network/real deps (or run pytest locally).
2. TP2 guard semantics: equal TP1==TP2 allowed now - confirm "closer" vs "strictly beyond".
3. Cost baseline: FRICTIONLESS (optimistic) vs a REALISTIC set you provide.
4. Floating-P&L equity marking for the daily-loss gate (L4) - behaviour change; not done without your decision.
5. Broker contract values for RunConfig (L9).
6. Whether to wire first-tick entry (changes entry prices) after real ticks exist.

## 13. FILES CHANGED
backtest/engine.py (causality arg only), backtest/pipeline.py, backtest/trade_log.py, core/config/registry.py,
core/domain/risk.py, decision/trade_decision_generator.py, docs/DECISIONS.md, market_data/replay/incremental_m5_layer.py,
scripts/run_backtest.py, strategy/confirmation/confirmation_engine.py, strategy/displacement/displacement_engine.py,
tests/test_backtest_pipeline.py.

## 14. FILES ADDED
backtest/accounting.py, backtest/tick_execution.py, market_data/replay/reference_m5_layer.py, scripts/baseline_report.py,
scripts/compare_runs.py, tests/test_backtest_accounting.py, tests/test_phase_a_correctness.py,
tests/test_reference_fast_equivalence.py, tests/test_run_backtest_runner.py, tests/test_tick_execution.py,
CHANGELOG_PHASE_A.md, PHASE_A_COMPLETION_REPORT.md.

## 15. CONFIGURATION USED (frozen Phase A baseline config = registry defaults)
engine REFERENCE; sizing FIXED_RISK; risk 0.5%; min RR 3.8; min score 70; TP1 allocation 30%; min stop 0.25 ATR (reject);
max stop 3.5 ATR; min target 1.0 ATR; daily loss 2%; max consecutive losses 3; max trades/day 3; reset 00:00 UTC; touch
timeout 0 (off); sweep->displacement max age 0 (off); cost model FRICTIONLESS (0/0/0); warm-up 30 days; initial balance
10,000. Config hash: written to `run_metadata.json` by the real run (not quoted here; see §10).

## 16. EXACT COMMANDS
```
# 0. real dependencies + real test suite (REQUIRED, not done by me)
pip install pytest hypothesis pydantic
pytest -q

# 1. time a 1-month REFERENCE slice first (REFERENCE is slow)
python scripts/run_backtest.py "<path>\XAUUSD_ticks_2025-09-01_2026-08-31.csv" ^
  --window-start "2025-10-01 00:00:00" --window-end "2025-10-31 23:59:59" --warmup-days 30 ^
  --engine REFERENCE --output-dir baseline_probe

# 2. the control baseline (no strategy flags => registry config)
python scripts/run_backtest.py "<path>\XAUUSD_ticks_2025-09-01_2026-08-31.csv" ^
  --window-start "2025-10-01 00:00:00" --window-end "2026-08-31 23:59:59" --warmup-days 30 ^
  --engine REFERENCE --output-dir baseline_A1
python scripts/baseline_report.py baseline_A1

# 3. reproducibility (identical command, new dir) and comparison
python scripts/run_backtest.py ... --output-dir baseline_A2
python scripts/compare_runs.py baseline_A1 baseline_A2

# 4. FAST divergence (same window) - differences are findings, not errors
python scripts/run_backtest.py ... --engine FAST --output-dir baseline_FAST
python scripts/compare_runs.py baseline_A1 baseline_FAST
```
(Window start 2025-10-01 leaves the first 30 days of the file as warm-up; use the real first/last timestamps of your file.)

## 17. OUTPUT LOCATIONS (this delivery)
smc-trading-engine-PHASE-A-v2.zip, PHASE_A_COMPLETION_REPORT.md, CHANGELOG_PHASE_A.md, fixture_trade_audit.json,
test_output_standin.txt, reference_vs_fast_synthetic.json. Baseline outputs (`baseline_A1/`, `baseline_A2/`,
`baseline_FAST/`) will be created on YOUR machine by the commands above - none exist yet.

## 18. RECOMMENDATION FOR NEXT STEP
Classification: **NOT technically validated** (real test suite and real-data runs outstanding). It is "technically
sound at the software/fixture level" and expected to reach "technically validated with limitations" after: (1) real
pytest passes; (2) the REFERENCE baseline and its rerun are identical on real data; (3) a manual read of ~10 real
trades from the ledger; (4) the FAST-vs-REFERENCE divergence on the same window is recorded. Do not read anything into
trade counts or profitability from the run - the control is the control whatever it shows. Resolve open decisions 2-5
BEFORE the control run, because they change results and the control should not be re-run after seeing them.
