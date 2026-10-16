# Gate 0 Evidence - SMC Trading Engine (Phase-A-v2 / E0)

> [!IMPORTANT]
> ### CRITICAL DISTINCTION: REPRODUCIBILITY PASS vs. TRADING PROFITABILITY
> 
> The **Gate 0 regression comparator result is PASS** (exit code 0).
> 
> **This certifies 100% deterministic bit-for-bit regression parity between the frozen reference baseline and the E0 instrumented engine** across all 53,357 events, state emissions, accounting balances, and diagnostic checksums.
> 
> **THIS IS NOT EVIDENCE OF TRADING PROFITABILITY, STRATEGY VIABILITY, OR ALPHA GENERATION.**
> 
> In fact, during the October 2025 backtest window:
> - **Completed trades**: 0
> - **Trade PnL**: $0.00
> - **Account balance**: $10,000.00 (unchanged)
> - **Max drawdown**: 0.00%
> 
> The zero-trade outcome is caused by upstream gating bottlenecks and known strategy defects documented in the diagnostic autopsies (`runs/probe_oct2025_reference/diagnostic_01/` and `diagnostic_02/`) and asserted by the validity harness (`tests_validity/` V1–V4).
> 
> **Gate 0 exists solely to guarantee that diagnostic telemetry (E0) does not perturb engine behavior or alter simulation outputs before any strategy defects are addressed.**

---

## Purpose

This document records the reproducibility evidence for the frozen October 2025 Phase-A REFERENCE baseline and the E0 instrumentation regression check.

These results establish **reproducibility of the baseline and instrumentation, not profitability.**

---

## Frozen Configuration

| Parameter | Value |
|---|---|
| Config hash | `ecbfc8a5dc92fc7e25af485be3a9f34e44fa588629419276115800b9757fa9fe` |
| Engine | REFERENCE (CANONICAL) - ReplayPipeline |
| Initial equity | $10,000 |
| Risk per trade | 0.5% (FIXED_RISK sizing) |
| Minimum R:R | 3.8 |
| Minimum setup score | 70 |
| TP1 allocation | 30% (runner: 70%) |
| Stop bounds | 0.25-3.5 ATR(M5) |
| Daily loss limit | 2% |
| Max trades/day | 3 |
| Cost model | FRICTIONLESS |
| Dataset | `XAUUSD_ticks_2025-09-01_2026-08-31.csv` (external, ~6.5 GB) |
| Warmup | 30 days (Sep 1 - Sep 30, 2025) |
| Trading window | Oct 1 - Oct 31, 2025 |

---

## October 2025 REFERENCE Baseline Run

| Field | Value |
|---|---|
| Run directory | `runs/probe_oct2025_reference/` |
| Completed trades | 0 |
| Final balance | $10,000 |
| Maximum drawdown | 0.00% |
| Total events | 53,357 |
| `events.jsonl` SHA-256 | `e2afc708845b8e45cfd0e10822046f119a9bfc85ae81472b1b7ea37282fe3a95` |
| Runtime | ~24.5 hours (reference hardware) |

---

## October 2025 E0 Instrumentation Regression Run

| Field | Value |
|---|---|
| Run directory | `runs/probe_oct2025_E0/` |
| Completed trades | 0 |
| Final balance | $10,000 |
| Maximum drawdown | 0.00% |
| Total events | 53,357 |
| `events.jsonl` SHA-256 | `e2afc708845b8e45cfd0e10822046f119a9bfc85ae81472b1b7ea37282fe3a95` |
| Runtime | 93,362 seconds (~25.9 hours on test hardware) |
| Telemetry errors | 0 |
| Shadow-gate mismatches | 0 |
| Plausibility guard | no flagged trades |

---

## Regression Comparator Result

Script: `scripts/e0_regression_compare.py`  
Comparator version: 1.1 | Exit code: 0

| File | Lines | Result |
|---|---|---|
| `events.jsonl` | 53,357 | IDENTICAL (SHA `e2afc708845b8e45`) |
| `trades.jsonl` | 0 | IDENTICAL |
| `statistics_final.json` | 17 | IDENTICAL (SHA `4058151c65fe0f68`) |
| `statistics_robustness.json` | 24 | IDENTICAL (SHA `45aae7b854d21f8a`) |
| `plausibility_report.json` | 4 | IDENTICAL (SHA `967fbbf540b2ddff`) |
| `trade_ledger.csv` | 1 | IDENTICAL (SHA `e028e271d14f71a3`) |
| `run_metadata.json` | - | IDENTICAL_EXCEPT_WALLCLOCK |
| E0 additions present | - | candidates.jsonl, h4_transitions.jsonl, funnel_counters.json, telemetry_manifest.json |

**OVERALL: PASS - exact regression**

---

## Committed vs Local Evidence Inventory

To ensure transparency regarding what is committed to the public Git repository vs what is retained locally:

### 1. Committed Artifacts (Publicly Accessible in Repository)
* **Reference Baseline (`runs/probe_oct2025_reference/`)**:
  * `baseline_report.md` & `baseline_report.json`: Baseline performance report.
  * `statistics_final.json` & `statistics_robustness.json`: Simulation metrics.
  * `run_metadata.json`: Runtime configuration and environment metadata.
  * `plausibility_report.json`: Trade plausibility evaluation.
  * `trade_ledger.csv` & `trades.jsonl`: Empty trade outputs (0 trades).
  * `diagnostic_01/`: Capability audit (JSON/MD), diagnostic summary, and 7 detailed CSV autopsies (`rejection_funnel.csv`, `near_miss_candidates.csv`, `score_distribution.csv`, `rr_distribution.csv`, `stop_atr_distribution.csv`, `tp_geometry.csv`, `htf_rejection_analysis.csv`, `gate_interaction_matrix.csv`, `october_candidate_autopsy.csv`, `far_from_qualification.csv`).
  * `diagnostic_02/`: Code path mapping, diagnostic 02 report, and 6 CSV geometry autopsies (`code_path_map.md`, `diagnostic_02_report.md`, `diagnostic_02_summary.json`, `tp1_geometry_audit.csv`, `tp2_geometry_audit.csv`, `stop_geometry_audit.csv`, `rr_calculation_verification.csv`, `rr_distribution_recomputed.csv`, `extreme_stop_analysis.csv`, `representative_candidate_trace.csv`).
  * `probe_oct2025_reference.zip` (1.25 MB) & `diagnostic_01.zip` (0.06 MB): Compact binary archives.
* **E0 Telemetry Run (`runs/probe_oct2025_E0/`)**:
  * `telemetry_manifest.json`: Manifest of all E0 telemetry components.
  * `funnel_counters.json`: Quantitative counters at each candidate stage.
  * `h4_transitions.jsonl`: Structural H4 regime transitions.
  * `run_metadata.json`: E0 run configuration and wallclock metadata.
  * `statistics_final.json` & `statistics_robustness.json`: Post-run metrics.
  * `plausibility_report.json`: Plausibility checks.
  * `trade_ledger.csv` & `trades.jsonl`: Empty trade outputs (0 trades).
  * `probe_oct2025_E0.zip` (3.43 MB): Compact archive containing full telemetry and compressed logs.

### 2. Local-Only Files (Excluded by `.gitignore`)
* `XAUUSD_ticks_2025-09-01_2026-08-31.csv` (~6.5 GB): Raw external tick data.
* `runs/*/events.jsonl` (~31.1 MB uncompressed per run): Raw event stream.
* `runs/*/candidates.jsonl` (~37.8 MB uncompressed): Raw candidate log.
* `runs/*/run_log.txt`: Verbose execution log.

---

## E0 Prefix Early-Warning Check

A shorter Oct 1-3 prefix run was also compared before the full month run.

| Field | Value |
|---|---|
| Run directory | `runs/probe_oct2025_E0_prefix/` (local only) |
| Events (prefix) | 26,934 (first 26,934 lines of frozen run) |
| Prefix comparator result | **PASS** - exit code 0 |
| Script | `scripts/e0_prefix_compare.py` |

---

## External Dataset

The raw tick dataset is **not committed** to this repository. It is stored locally.

| Property | Value |
|---|---|
| Filename | `XAUUSD_ticks_2025-09-01_2026-08-31.csv` |
| Expected local path | `C:\Users\hp\Downloads\xauusd_working\` |
| Date range | 2025-09-01 to 2026-08-31 |
| File size | 7,003,802,861 bytes (~6.5 GB) |

To verify the dataset identity, check that its byte size matches the value above.

---

## Known Validity Defects (V1-V4)

The validity test suite (`tests_validity/`) documents four known defects in the current strategy implementation. These defects are **asserted to fail** by the validity tests, confirming they are present in the frozen baseline:
1. **V1**: H4 structural re-application
2. **V2**: Confirmation predating setup eligibility
3. **V3**: Stale decision persistence
4. **V4**: Duplicate event emissions

**No strategy fixes have been implemented as of this commit.**

---

## What Has NOT Been Done

- No strategy parameter changes
- No V1-V4 defect fixes implemented
- No 12-month or walk-forward backtest authorized
- No changes to the frozen baseline

---
*Document updated: 2026-10-16. Evidence preserved by automated backtest operator.*
