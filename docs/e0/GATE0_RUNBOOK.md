# GATE 0 RUNBOOK — exact October REFERENCE regression for E0

**Rule:** nothing beyond E0 (no V1–V4, no strategy change) happens until the comparator in section 4 prints `PASS: exact regression`. Your frozen run `runs/probe_oct2025_reference` is never written to.

Everything below was checked against your frozen run's own `run_metadata.json` and `run_log.txt`:
frozen run = REFERENCE engine, window `2025-10-01 00:00:00` → `2025-10-31 23:59:59` UTC, `--warmup-days 30` (data loaded from 2025-09-01), FRICTIONLESS, equity 10,000, **no CLI overrides**, Python **3.11.9**, CSV size **7,003,802,861 bytes**, config hash `ecbfc8a5…56fe`, 53,357 events, 0 trades, runtime **88,338 s (24.5 h)**.

## 1. Pre-flight (about 20 minutes)

**1.1 Use a NEW folder.** Extract `smc-trading-engine-PHASE-A-v2-E0.zip` next to your current project (for example `smc-trading-engine-E0`). Do not overwrite your reference copy.

**1.2 Use the same Python environment that produced the frozen run** (this keeps the pydantic version the same). Check:
```
python --version        # frozen run used 3.11.9
pip list | findstr /i "pydantic pytest hypothesis"
```
Write down the versions. A different patch version of Python does **not** fail the comparator (it is reported as informational), but anything that changes behavior would, so the same environment is the safe choice. If you must create a new environment, the project's declared dependencies are `pydantic>=2.6,<3`, `pytest>=8`, `hypothesis>=6.100` (`pip install -e ".[dev]"`).

**1.3 Run the REAL test suites** (from the E0 project root, the folder that contains `pyproject.toml`):
```
python -m pytest -q
python -m pytest tests_validity -q
```
Expected (my stand-in environment; real pytest may differ in detail):
* `tests/`: about 650 tests; all pass, except possibly the machine-speed test `test_candle_store_scaling ... practical_at_scale` (it fails in my sandbox; it may pass on yours). **Any other failure or error: stop and send me the output before starting the 24-hour run.**
* `tests_validity/`: **11 failed, 16 passed** (the 11 are the intended defect assertions). If you see `ERROR` instead of `FAILED` for some tests, that is my synthetic test objects tripping real-pydantic validation, not a Gate 0 problem; send me the output and I will fix the tests. It does not block Gate 0.

**1.4 Check the data file.** Same path as before and the same size:
```
dir "C:\Users\hp\Downloads\xauusd_working\XAUUSD_ticks_2025-09-01_2026-08-31.csv"
```
Must show **7,003,802,861** bytes. A different size means a different file: stop.

**1.5 Check the frozen reference is intact** (optional but cheap). PowerShell:
```
Get-FileHash runs\probe_oct2025_reference\events.jsonl -Algorithm SHA256
```
must equal `e2afc708845b8e45cfd0e10822046f119a9bfc85ae81472b1b7ea37282fe3a95`. Other frozen hashes:
```
trades.jsonl               e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855   (empty file)
statistics_final.json      4058151c65fe0f6896882645e036d6b9dd7a766c3840db102c4fa8ec7c789b32
statistics_robustness.json 45aae7b854d21f8a00697861577a05581e987eb52e30a2b3bc21de33e8fbda70
plausibility_report.json   967fbbf540b2ddffb75563ad09fdcf02b8a0f0a5b335438c10e83903e248e290
trade_ledger.csv           e028e271d14f71a3cf3c2b5c238b1d625a3cce640dbb3482008608895e1bbb88
```
(Run the E0 project from its own folder; its `runs/probe_oct2025_reference` is a byte-identical copy of the frozen one, or point the comparator at your original reference folder.)

## 2. Optional early-warning check (Gate 0a): about 4 hours instead of 24.5
Because the replay is strictly chronological, a run with the **same start but an earlier end** must reproduce the leading part of the frozen event stream. End it on a Friday after the market has closed so no candle is cut in half. From the frozen log, the first three trading days finish about 3.7 h in (the whole September warm-up, including all 318 warm-up decisions, takes about 2.5 h). This is an estimate for the same machine speed.

Windows cmd (one line each, run from the E0 project root):
```
python scripts\run_backtest.py "C:\Users\hp\Downloads\xauusd_working\XAUUSD_ticks_2025-09-01_2026-08-31.csv" --window-start "2025-10-01 00:00:00" --window-end "2025-10-03 23:59:59" --warmup-days 30 --engine REFERENCE --output-dir runs\probe_oct2025_E0_prefix

python scripts\e0_prefix_compare.py runs\probe_oct2025_reference runs\probe_oct2025_E0_prefix
```
Expected: `OVERALL: PASS: prefix identical (early-warning only; full Gate 0 still required)`, with `short_lines` in the tens of thousands (the whole September warm-up plus Oct 1–3, always fewer than the frozen 53357) and `frozen_lines` 53357. A FAIL here means E0 is not observation-only: **do not start the 24-hour run; send me the output.** A PASS does not replace the full comparison.

## 3. Gate 0: the exact command (about 24.5 hours)
Run from the E0 project root. The output folder **must not already exist** (the runner appends to existing files). Keep the machine awake (disable sleep, no other heavy jobs). There is no resume: an interrupted run must restart.

Windows cmd:
```
python scripts\run_backtest.py "C:\Users\hp\Downloads\xauusd_working\XAUUSD_ticks_2025-09-01_2026-08-31.csv" ^
  --window-start "2025-10-01 00:00:00" --window-end "2025-10-31 23:59:59" ^
  --warmup-days 30 --engine REFERENCE --output-dir runs\probe_oct2025_E0
```
PowerShell (same arguments; backtick continuation):
```
python scripts\run_backtest.py "C:\Users\hp\Downloads\xauusd_working\XAUUSD_ticks_2025-09-01_2026-08-31.csv" `
  --window-start "2025-10-01 00:00:00" --window-end "2025-10-31 23:59:59" `
  --warmup-days 30 --engine REFERENCE --output-dir runs\probe_oct2025_E0
```
Do **not** add any strategy, risk, sizing or cost flag (the frozen run had none; the default is the registry configuration). Do **not** add `--no-telemetry`. Progress: `Get-Content runs\probe_oct2025_E0\run_log.txt -Tail 3 -Wait`. The log's first lines must show the same config hash line (`EFFECTIVE CONFIG HASH: ecbfc8a5…`) and `ENGINE: REFERENCE (CANONICAL)`. Final line: `DONE in ~88000s. trades=0 events=53,357`.

## 4. The comparison and its exact expected output
```
python scripts\e0_regression_compare.py runs\probe_oct2025_reference runs\probe_oct2025_E0
```
Exit code must be `0`. Expected output (generated by running this comparator on a copy of your real frozen files plus placeholder telemetry files; line counts and hash prefixes are the real values; a passing run prints exactly this):

```json
{
 "comparator_version": "1.1",
 "checks": [
  {
   "file": "events.jsonl",
   "lines": 53357,
   "result": "IDENTICAL",
   "sha256_frozen": "e2afc708845b8e45",
   "sha256_e0": "e2afc708845b8e45"
  },
  {
   "file": "trades.jsonl",
   "lines": 0,
   "result": "IDENTICAL",
   "sha256_frozen": "e3b0c44298fc1c14",
   "sha256_e0": "e3b0c44298fc1c14"
  },
  {
   "file": "statistics_final.json",
   "lines": 17,
   "result": "IDENTICAL",
   "sha256_frozen": "4058151c65fe0f68",
   "sha256_e0": "4058151c65fe0f68"
  },
  {
   "file": "statistics_robustness.json",
   "lines": 24,
   "result": "IDENTICAL",
   "sha256_frozen": "45aae7b854d21f8a",
   "sha256_e0": "45aae7b854d21f8a"
  },
  {
   "file": "plausibility_report.json",
   "lines": 4,
   "result": "IDENTICAL",
   "sha256_frozen": "967fbbf540b2ddff",
   "sha256_e0": "967fbbf540b2ddff"
  },
  {
   "file": "trade_ledger.csv",
   "lines": 1,
   "result": "IDENTICAL",
   "sha256_frozen": "e028e271d14f71a3",
   "sha256_e0": "e028e271d14f71a3"
  },
  {
   "file": "run_metadata.json",
   "result": "IDENTICAL_EXCEPT_WALLCLOCK",
   "differences": [],
   "informational_differences_not_failing": []
  }
 ],
 "documented_additions_present_in_e0": {
  "candidates.jsonl": true,
  "h4_transitions.jsonl": true,
  "funnel_counters.json": true,
  "telemetry_manifest.json": true
 },
 "telemetry_error_count": 0,
 "shadow_gate_mismatch": 0,
 "OVERALL": "PASS: exact regression"
}
```
Reading it: every `IDENTICAL` means the E0 file is byte-for-byte the same as the frozen one. `run_metadata.json` must be identical except wall-clock fields; `python` and `data_file.path` differences, if any, are listed under `informational_differences_not_failing` and do not fail. Anything else in `differences` is a failure. `telemetry_error_count` and `shadow_gate_mismatch` must both be 0.

**If it prints `FAIL`:** copy the whole output. Do not rerun, do not edit anything, do not proceed. Send me the JSON, `runs\probe_oct2025_E0\run_log.txt` (first 20 and last 5 lines), and `telemetry_manifest.json`. `first_difference_line` identifies the first event that differs.

## 5. Telemetry cross-check (informational, not part of the pass/fail comparator)
The comparator proves behavior is unchanged. These checks show the new ledger agrees with my independent analysis of the frozen events. A mismatch means a telemetry bug (tell me), not a behavior change.

Counts that must equal the frozen event counts (`runs\probe_oct2025_E0\funnel_counters.json`):

| `funnel_counters.json` key | Expected | Frozen event count it mirrors |
|---|---|---|
| `zones_considered` minus `zones_skipped_already_has_setup` (absent = 0) | 15740 | ZONE_CREATED 15,740 (a zone that already has a setup, e.g. an FVG shared by pool variants, is counted as considered but emits no ZONE_CREATED) |
| `setups_constructed` | 15740 (expected; smaller only if some candidates could not be built) | one candidate per non-skipped zone |
| `setups_admitted` | 9336 | SETUP_CREATED 9,336 |
| `terminal_DISPLACED` | 5155 | SETUP_DISPLACED 5,155 |
| `terminal_INVALIDATED` | 2522 | SETUP_INVALIDATED 2,522 |
| `terminal_EXPIRED` | 1042 | SETUP_EXPIRED 1,042 |
| `setups_confirmed` | 617 | SETUP_CONFIRMED 617 |
| `decision_rejected_WARMUP_PERIOD` | 318 | warm-up rejections 318 |
| `decisions_evaluated` | 294 | in-window decisions: HTF 59 + STOP_TOO_WIDE 126 + RR 72 + TP2 34 + STOP_BELOW_MINIMUM 3 |

(`setups_not_admitted`, the candidates the 3-slot pending cap refused, should then be 15740 − 9336 = 6404.)

Values from my event-timestamp analysis of the frozen run that the ledger should reproduce (`candidates.jsonl`, in-window decided rows = those with `gates` and `decision_step_at` ≥ 2025-10-01):
* `retroactive_confirmation` true: **95** of 294;
* decided with at least one earlier skip (`skips` non-empty): **48** (late decisions);
* confirmed but never decided: **5** in the whole run;
* median `construction_lag_h_from_zone`: about **2.2 h**.

H4 log (`h4_transitions.jsonl`): `applied_out_of_time_order` should be true for **some** rows (the V1 evidence; I expect a handful per reversal, but the exact number is not predicted). A count of 0 would contradict my V1 finding and I would want to know.

## 6. After PASS
Send me the comparator output and `funnel_counters.json`. I then start V1 only on your explicit go-ahead, one patch at a time, in its own folder and run directory, with the per-patch report described in `PATCH_SEQUENCE_PROPOSAL.md`.
