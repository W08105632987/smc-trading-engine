# Gate 0 Evidence — SMC Trading Engine (Phase-A-v2 / E0)

## Purpose

This document records the reproducibility evidence for the frozen October 2025
Phase-A REFERENCE baseline and the E0 instrumentation regression check.

These results establish **reproducibility of the baseline and instrumentation,
not profitability.**

---

## Frozen Configuration

| Parameter | Value |
|---|---|
| Config hash | `ecbfc8a5dc92fc7e25af485be3a9f34e44fa588629419276115800b9757fa9fe` |
| Engine | REFERENCE (CANONICAL) — ReplayPipeline |
| Initial equity | $10,000 |
| Risk per trade | 0.5% (FIXED_RISK sizing) |
| Minimum R:R | 3.8 |
| Minimum setup score | 70 |
| TP1 allocation | 30% (runner: 70%) |
| Stop bounds | 0.25–3.5 ATR(M5) |
| Daily loss limit | 2% |
| Max trades/day | 3 |
| Cost model | FRICTIONLESS |
| Dataset | XAUUSD_ticks_2025-09-01_2026-08-31.csv (external, ~6.5 GB) |
| Warmup | 30 days (Sep 1 – Sep 30, 2025) |
| Trading window | Oct 1 – Oct 31, 2025 |

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
| `run_metadata.json` | — | IDENTICAL_EXCEPT_WALLCLOCK |
| E0 additions present | — | candidates.jsonl, h4_transitions.jsonl, funnel_counters.json, telemetry_manifest.json |

**OVERALL: PASS — exact regression**

---

## E0 Prefix Early-Warning Check

A shorter Oct 1–3 prefix run was also compared before the full month run.

| Field | Value |
|---|---|
| Run directory | `runs/probe_oct2025_E0_prefix/` (local only) |
| Events (prefix) | 26,934 (first 26,934 lines of frozen run) |
| Prefix comparator result | **PASS** — exit code 0 |
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

To verify the dataset identity, check its byte size matches the value above.

---

## Known Validity Defects (V1–V4)

The validity test suite (`tests_validity/`) documents four known defects in the
current strategy implementation. These defects are **asserted to fail** by the
validity tests, confirming they are present in the frozen baseline.

**No strategy fixes have been implemented as of this commit.**
See `tests_validity/` for the precise defect assertions and `docs/e0/` for
the full E0 runbook.

---

## What Has NOT Been Done

- No strategy parameter changes
- No V1–V4 defect fixes implemented
- No 12-month or walk-forward backtest authorized
- No changes to the frozen baseline

---
*Document generated: 2026-10-16. Evidence preserved by automated backtest operator.*
