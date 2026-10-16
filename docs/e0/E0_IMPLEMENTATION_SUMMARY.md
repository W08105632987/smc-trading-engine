# E0 — candidate-level instrumentation (observation only)

**Contract.** E0 adds telemetry. It must not change any decision, threshold, stop, target, sizing, risk control, event id, event order, trade record, statistic or accounting value. The frozen Phase-A-v2 package and the October 2025 baseline are untouched; E0 lives in a separate copy.

## 1. What was added

| File | Kind | Purpose |
|---|---|---|
| `backtest/telemetry.py` | new | `Telemetry` (hooks + writer), `describe_targets`, `shadow_gates`. Pydantic-free. Off unless an instance is passed to `ReplayPipeline(telemetry=...)`. |
| `backtest/pipeline.py` | edited | 16 observation blocks, each guarded by `if self.telemetry is not None:`; see section 3. |
| `scripts/run_backtest.py` | edited | `--no-telemetry` flag (telemetry **on by default**); `build_pipeline(..., telemetry=None)` pass-through; writes telemetry files to the run directory. |
| `scripts/e0_regression_compare.py` | new (v1.1) | Exact-regression comparator against a frozen run directory. v1.1: `python` and `data_file.path` are informational; config hash, window, warm-up, costs, data file name and size, results and all outputs stay strict; `trade_ledger.csv` is compared too. |
| `scripts/e0_prefix_compare.py` | new | Early-warning check: a shorter run (same start, earlier end) must equal the leading part of the frozen event stream. Not a substitute for the full comparison. |
| `scripts/e0_funnel_report.py` | new | Funnel diagnostics and A→B candidate transition matrix from `candidates.jsonl` (counts and geometry only). |
| `tests/test_e0_telemetry.py` | new | 16 tests: behavior neutrality, no config change, never raises, gate/target mirroring, ledger fields. |
| `tests/test_e0_comparators.py` | new | 10 tests of the Gate 0 tooling (pass, fail, strict vs informational metadata, prefix logic). |
| `tests_validity/` | new | Minimal failing tests for V1–V4 (outside the default test path). |
| `tools/e0_regression/` | new | Synthetic-data differential harness (generator, scenario runner, byte comparer, resumable matrix script). |
| `docs/e0/*.md` | new | This summary, defect evidence, patch sequence, **`GATE0_RUNBOOK.md` (exact Gate 0 commands and expected comparator output)**. |

**No configuration parameter was added or removed** (a golden list of all 48 parameter paths is asserted in a test), so the config hash is unchanged.

## 2. What telemetry records
Separate files in the run directory: `candidates.jsonl` (one row per constructed setup), `h4_transitions.jsonl`, `funnel_counters.json`, `telemetry_manifest.json`. `events.jsonl`, `trades.jsonl`, `statistics_*.json`, `plausibility_report.json` and `run_metadata.json` keep their exact existing content.

Per-candidate fields:
* **Identity and time:** setup/zone/MSS/sweep/displacement/pool ids and types, pool price and creation time, zone creation time, MSS confirmation time, logical `created_at`, **`constructed_at`**, construction lag from zone, expiry, admission result, displaced setup, pending count, H4 state at construction, score snapshot.
* **Lifecycle:** touch first seen and start, break candle id and **close time**, confirmation step, `retroactive_confirmation`, decision step, `decision_after_break_step`, terminal state and source, skip counts by reason (`NO_TARGET_PAIR_YET`, `STOP_ON_WRONG_SIDE_OF_ENTRY`), `parked_steps` behind an open position, micro-swing list with pivot prices and retracements.
* **Geometry:** entry, stop, ATR, stop distance (points, ATR), zone width, **true entry position in zone** and `stop_over_zone_width` (the pipeline's own payload field `entry_position_in_zone` is the latter, mislabelled; the payload is left untouched), zone-to-entry ATR, stop-inside-zone flag, TP1/TP2 and distances in ATR.
* **Targets:** the selected pool ids; per class (internal/external) the eligible count and 5 nearest pools (id, type, price, major-swing flag, ATR distance); exclusion counts by reason; **shadow** strict pair (nearest external strictly beyond TP1) and its extension versus the unconstrained nearest external. The shadow never feeds back.
* **RR:** RR(TP1), RR(TP2), blended, and pass flags at 1.0/1.5/2.0/2.5/3.8 (diagnostic flags only).
* **Gates:** every gate evaluated **independently** (HTF, min stop, max stop, TP2 order, RR, score, risk), `all_failed_gates`, `first_failed_gate`, the actual outcome, and `shadow_matches_actual` (any disagreement is counted as `shadow_gate_mismatch`; required to be 0).
* **Risk/sizing at decision:** equity, daily start equity, daily P&L, consecutive losses, trades today, computed/rounded volume, risk amount and actual risk %.
* **Trade outcome** (when closed): ids, prices, exit reason, R, net P&L.
* **H4 log (application order):** every structure event fed to the H4 state machine with its `confirmed_at`, state before/after, and `applied_out_of_time_order`; silent warning-expiry restores.
* **Funnel counters:** zones considered, setups constructed/admitted/not admitted, terminal states, confirmed, confirmed retroactively, decisions by outcome, skips by reason, parked steps, H4 counters.

Safety properties (tested): hooks never raise into the pipeline (errors are counted; `strict=True` in tests surfaces them); no wall-clock, no randomness; no mutation of inputs.

## 3. Pipeline edits (complete list)
Net change: **69 added lines, 4 removed**. All additions are inside `if self.telemetry is not None:` except these inert ones:
* constructor: `telemetry=None` parameter, `self.telemetry`, `self._last_build_ctx = None`;
* `_build_setup_candidate`: `self._last_build_ctx = (sweep, pool, atr_at_setup)` (attribute assignment only);
* `_attempt_decision`: `selection = None` and `consumed_pool_ids = frozenset()` defaults before the target branch (both are reassigned in the branch that uses them);
* **two logically-equivalent rewrites** (the only removed lines): `for setup in confirmed:` → `for _idx, setup in enumerate(confirmed):`, and the two wrong-side-stop `if … return` statements → one `if (A) or (B): [telemetry]; return setup, events`.

Hook sites: `_recompute_h4_layer` (structure event applied), `_check_warning_expiry` (silent restore), `_construct_setup_candidates` (zone considered, candidate built/admitted/not admitted, displaced), `_update_setup_lifecycles` (terminal), `_process_touches` (touch seen, timeout, confirmed), `_attempt_decisions` (parked behind open position), `_attempt_decision` (warm-up reject, wrong-side skip, no-target skip, full decision context), `step` (trade closed).

## 4. Evidence that behavior is unchanged
1. **Static:** the diff above.
2. **Unit tests** (`tests/test_e0_telemetry.py`): events, trades, emitted ids and ledger identical with and without telemetry (including rejecting scenarios); config parameter paths unchanged; hooks never raise; shadow gates equal the generator's reason for min-stop, RR and daily-loss rejections; `describe_targets` agrees with `select_targets` over 200 random pool sets; the true-entry-in-zone field equals `(entry − lower)/width`.
3. **Differential regression** on identical synthetic input, original code vs E0 (telemetry ON strict, and OFF): see section 5.
4. **Full suite, stand-in environment (not real pytest):** original 622 passed / 1 failed (623); E0 648 passed / 1 failed (649). The one failure is the same wall-clock test (`test_candle_store_scaling … elapsed < 5.0`) on both. +26 = 16 E0 telemetry tests + 10 Gate 0 tooling tests. `tests_validity`: 11 failed (intended) / 16 passed.

## 5. Exact regression comparison

| Scenario | Engine | Preset | Steps | Events | Confirmed | Rejected | Approved | Closed trades | Final balance | ON identical | OFF identical | Telemetry rows | Telemetry errors | Shadow mismatches |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fast_s11_default | FAST | default | 864 | 1246 | 9 | 8 | 1 | 1 | 10158.70600 | YES | YES | 383 | 0 | 0 |
| fast_s11_relaxed | FAST | relaxed | 864 | 1246 | 9 | 8 | 1 | 1 | 10158.70600 | YES | YES | 383 | 0 | 0 |
| fast_s11_relaxed_costs | FAST | relaxed_costs | 864 | 1246 | 9 | 8 | 1 | 1 | 10157.76600 | YES | YES | 383 | 0 | 0 |
| fast_s12_default | FAST | default | 864 | 1256 | 10 | 10 | 0 | 0 | 10000 | YES | YES | 415 | 0 | 0 |
| fast_s12_relaxed | FAST | relaxed | 864 | 1272 | 10 | 6 | 4 | 4 | 10424.62013333333333333333334 | YES | YES | 415 | 0 | 0 |
| fast_s12_relaxed_costs | FAST | relaxed_costs | 864 | 1272 | 10 | 6 | 4 | 4 | 10390.31013333333333333333334 | YES | YES | 415 | 0 | 0 |
| fast_s13_default | FAST | default | 864 | 1404 | 14 | 12 | 0 | 0 | 10000 | YES | YES | 427 | 0 | 0 |
| fast_s13_relaxed | FAST | relaxed | 864 | 1404 | 14 | 12 | 0 | 0 | 10000 | YES | YES | 427 | 0 | 0 |
| fast_s13_relaxed_costs | FAST | relaxed_costs | 864 | 1404 | 14 | 12 | 0 | 0 | 10000 | YES | YES | 427 | 0 | 0 |
| ref_s11_relaxed | REFERENCE | relaxed | 576 | 873 | 8 | 6 | 2 | 2 | 10187.23400 | YES | YES | 259 | 0 | 0 |

**10 scenarios (3 synthetic seeds × 3 threshold/cost presets on the FAST engine, plus 1 on the REFERENCE engine): ALL byte-identical with telemetry ON and OFF; 0 telemetry errors; 0 shadow-gate mismatches.** Totals compared: 12,623 events, 13 approvals, 13 closed trades. Presets: `default` = registry configuration; `relaxed` = RR 0.2, score 0, max stop 50 ATR, min target 0.2 ATR, min stop 0.01 ATR (to force decisions); `relaxed_costs` = relaxed + REALISTIC costs (spread 0.3, slippage 0.1, commission 3.5/lot). Side observations from the synthetic E0 ledgers: 35 retroactive confirmations, 1090 stale no-target retries, 58 parked-behind-open-position steps.


Compared files: `events.jsonl` (every event: id, type, timestamp, entity, states, payload), `trades.jsonl` (every TradeRecord field), and `summary.json` (final account balance, realized P&L, drawdown, trade count, trades-today, loss streak, peak equity, config hash, final pipeline state counts). Comparison is by SHA-256 of the whole file.

### Frozen October 2025 REFERENCE regression — **NOT RUN here**
This sandbox has no market data and the October REFERENCE run took **88,338 s (24.5 h)** for 12,174 steps in the frozen log. E0 therefore passes the synthetic exact regression above, but **the gate "E0 reproduces the frozen October run exactly" is PENDING** and must be run on your machine with the original CSV:

```
python scripts/run_backtest.py "<path>\XAUUSD_ticks_2025-09-01_2026-08-31.csv" ^
  --window-start "2025-10-01 00:00:00" --window-end "2025-10-31 23:59:59" --warmup-days 30 ^
  --engine REFERENCE --output-dir runs/probe_oct2025_E0

python scripts/e0_regression_compare.py runs/probe_oct2025_reference runs/probe_oct2025_E0
```
See `GATE0_RUNBOOK.md` for the full procedure, the optional 4-hour prefix check and the exact expected output. Expected: `OVERALL: PASS: exact regression` (events, trades, statistics and plausibility files byte-identical; `run_metadata.json` identical except wall-clock fields; 0 telemetry errors; 0 shadow-gate mismatches). Same arguments as the frozen run are required (check its `run_metadata.json`). Nothing downstream (V1–V4) should be run before this passes.

Documented additions versus the frozen output (all new files): `candidates.jsonl`, `h4_transitions.jsonl`, `funnel_counters.json`, `telemetry_manifest.json`. Note the frozen run produced no ledger, so the two ledgers cannot be compared against October; telemetry correctness is established by the unit tests and the shadow/actual agreement counter.

## 6. Limits and honesty notes
* Tests ran under a minimal pydantic/pytest stand-in (no network for real packages). Calibration: it reproduces the original suite's results. Run `pytest` with real dependencies before relying on any of this.
* Synthetic data exercises code paths; it says nothing about the market. The synthetic scenarios produced 0–1 trades each, so the *trade-level* regression is thin; the event-level regression (≈1,200 events per run, including decisions and rejections) is the stronger evidence.
* Telemetry is on by default in the runner; `--no-telemetry` restores the exact previous output set. Its memory use grows with the number of constructed setups (about 5,000 in the October window, about 9,000 including warm-up).
