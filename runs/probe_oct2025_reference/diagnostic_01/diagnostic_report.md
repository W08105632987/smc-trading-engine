# Diagnostic Run #1 — Existing-Data Forensic Analysis Report

**Target Baseline:** `runs/probe_oct2025_reference/`  
**Engine:** REFERENCE  
**Period:** October 1, 2025 – October 31, 2025 (30-day warmup excluded)  
**Execution Date:** 2026-10-05  
**Nature of Run:** Read-Only Forensic Analysis (Zero code modifications, zero reruns)

---

## Executive Summary

Diagnostic Run #1 executed a rigorous, read-only forensic audit of the frozen October 2025 REFERENCE baseline. The baseline recorded **294 active setup evaluations** and **0 approved trades**. 

This forensic analysis conclusively explains why zero trades qualified, establishes the exact sequential bottleneck distribution, quantifies near misses versus extreme outliers, and identifies data points not preserved in the serialized baseline.

### Key Headline Metrics:
* **Total Active Candidates Evaluated:** 294
* **Total Approved Trades:** 0 (0.00%)
* **Total Filled Orders:** 0
* **Dominant Rejection Bottleneck:** `STOP_TOO_WIDE` (126 candidates, 42.86%)
* **Secondary Bottleneck:** `RR_BELOW_MINIMUM` (72 candidates, 24.49%)
* **Tertiary Bottleneck:** `HTF_CONTEXT_INVALID` (59 candidates, 20.07%)
* **Quaternary Bottleneck:** `TP2_NOT_BEYOND_TP1` (34 candidates, 11.56%)
* **Minor Rejection:** `STOP_BELOW_MINIMUM` (3 candidates, 1.02%)
* **Score Gate Rejections:** 0 (Gate never reached)

---

## 1. Candidate Population & Reconciliation

Every active October candidate evaluation was reconstructed from `events.jsonl`. Lifecycle integrity was confirmed across all 294 entity records:
* Confirmed Setups entering Decision: **294**
* Rejections Reconciled: $126 + 72 + 59 + 34 + 3 = 294$ (100.0% exact match)
* Duplicate Lifecycle Records: **0**
* Unaccounted / Dropped Setups: **0**

---

## 2. Sequential Rejection Funnel

The decision pipeline applies gating rules in a strict deterministic sequence. The table below reconstructs how candidates flowed through each successive gate:

| Stage # | Stage Name | Candidates Entering | Candidates Rejected | Primary Reason Code | Passing Stage | Stage Pass Rate |
|:---:|:---|:---:|:---:|:---|:---:|:---:|
| 1 | Active October Candidates | 294 | 0 | None | 294 | 100.0% |
| 2 | HTF Context Gate | 294 | 59 | `HTF_CONTEXT_INVALID` | 235 | 79.93% |
| 3 | Stop Bounds Gate | 235 | 129 | `STOP_TOO_WIDE` (126) / `STOP_BELOW_MIN` (3) | 106 | 45.11% |
| 4 | TP2 Geometry Gate | 106 | 34 | `TP2_NOT_BEYOND_TP1` | 72 | 67.92% |
| 5 | Blended RR Gate | 72 | 72 | `RR_BELOW_MINIMUM` | **0** | **0.00%** |
| 6 | Score Gate | 0 | 0 | `SCORE_BELOW_THRESHOLD` | 0 | N/A (Bypassed) |
| 7 | Risk Limits Gate | 0 | 0 | Risk Manager Limits | 0 | N/A (Bypassed) |
| 8 | **Approved Trades** | **0** | **0** | **None** | **0** | **0.00%** |

*Crucial Note on Sequential Causality:* The funnel demonstrates where candidates were *first* halted. A candidate rejected at Gate 2 (Stop Bounds) was never evaluated at Gate 4 (RR) or Gate 6 (Score).

---

## 3. Stop / ATR Distribution Analysis (126 Candidates)

The frozen engine imposes a strict stop distance ceiling of **3.50 ATR**. All 126 `STOP_TOO_WIDE` candidates breached this rule.

### Statistical Summary:
* **Count:** 126
* **Minimum:** 3.5026 ATR
* **Maximum:** 26.8852 ATR
* **Mean:** 7.0270 ATR
* **Median:** 5.8679 ATR
* **Standard Deviation:** 3.7533 ATR
* **25th Percentile:** 4.2255 ATR
* **75th Percentile:** 8.6534 ATR

### Bucket Breakdown:
* `<= 3.50 ATR`: 0 (0.00%)
* `3.50 – 3.60 ATR`: 3 (2.38%) — *Immediate boundary overruns*
* `3.60 – 3.75 ATR`: 4 (3.17%) — *Tight near misses*
* `3.75 – 4.00 ATR`: 13 (10.32%) — *Near misses*
* `4.00 – 4.50 ATR`: 16 (12.70%)
* `4.50 – 5.00 ATR`: 14 (11.11%)
* `> 5.00 ATR`: 76 (60.32%) — *Massively oversized stops*

**Key Takeaway:** While **20 candidates (15.87%)** were objective near misses within 3.50–4.00 ATR, the majority (60.32%) had stops exceeding 5.0 ATR (with some exceeding 20 ATR), indicating that zone anchoring during volatile sessions frequently generates structural stops far beyond standard ATR dimensions.

---

## 4. Risk-Reward Ratio (RR) Distribution Analysis (72 Candidates)

All 72 candidates that survived Gates 1, 2, and 3 were halted at Gate 4 by the frozen **3.80R minimum blended RR** requirement.

### Statistical Summary:
* **Count:** 72
* **Minimum:** 0.3198R
* **Maximum:** 2.3790R
* **Mean:** 1.0967R
* **Median:** 0.9959R
* **Standard Deviation:** 0.4431R
* **25th Percentile:** 0.7788R
* **75th Percentile:** 1.3768R

### Bucket Breakdown:
* `< 2.00R`: 68 (94.44%)
* `2.00 – 2.50R`: 4 (5.56%)
* `2.50 – 3.00R`: 0 (0.00%)
* `3.00 – 3.50R` (Broad Near Miss): **0 (0.00%)**
* `3.50 – 3.80R` (Narrow Near Miss): **0 (0.00%)**
* `>= 3.80R`: 0 (0.00%)

**Key Takeaway:** There were **ZERO near misses** at the RR gate. Not a single candidate reached even 2.50R, let alone the 3.00–3.80R near-miss zone. The average candidate produced barely ~1.10R. This demonstrates that the failure at the RR gate is not an issue of marginal threshold tightness, but rather a profound structural divergence between the 3.80R requirement and available M5 target swing distances.

---

## 5. TP1 / TP2 Geometry Analysis (34 Candidates)

34 candidates were rejected with `TP2_NOT_BEYOND_TP1`. The rule requires that TP2 must extend strictly further in the trade direction than TP1.

### Forensic Findings:
* **TP2 == TP1 (Zero Gap):** 0 candidates (0.00%)
* **TP2 Inverted (Wrong Side of TP1):** 34 candidates (100.00%)
* **Minimum Inverted Gap:** -0.0200 points (closest candidate)
* **Maximum Inverted Gap:** -23.5270 points
* **Median Inverted Gap:** -0.6922 points

**Key Takeaway:** None of the rejections were caused by floating-point rounding equality (`TP2 == TP1`). In every case, the target resolution algorithm identified an opposing liquidity pool or structural level for TP2 that was positioned *behind* TP1 relative to the entry price. 7 candidates were close near misses with inverted gaps of less than 0.50 points.

---

## 6. Score & HTF Analysis — Preserved Limitations

### Score Analysis:
* **Status:** `UNAVAILABLE — NOT SERIALIZED IN FROZEN BASELINE`
* **Finding:** In `events.jsonl`, `score` was not preserved in rejection event payloads.
* **Funnel Reality:** Because 100% of candidates failed at or before Gate 4 (RR), the score gate (Gate 5) was **never reached**. Thus, `SCORE_BELOW_THRESHOLD = 0`. This does NOT indicate that setups scored above 70; it proves that score was never the binding rejection reason.

### HTF Analysis:
* **Status:** `Detailed HTF sub-flags UNAVAILABLE — NOT SERIALIZED IN FROZEN BASELINE`
* **Finding:** 59 candidates were rejected with `HTF_CONTEXT_INVALID`. However, individual sub-flags (D1 trend alignment, H4 bias, H4 CHoCH warning) were not serialized into `events.jsonl`.
* **Conclusion:** 20.07% of the candidate population was immediately eliminated at Gate 1 due to macro directional misalignment, but distinguishing between D1 vs H4 conflict requires a rerun with enhanced telemetry.

---

## 7. Answers to the 15 Diagnostic Questions

1. **Why did all 294 candidates fail?**  
   Every candidate was eliminated by one of four sequential admission gates: 129 at Stop Bounds, 72 at Blended RR, 59 at HTF Context, and 34 at TP2 Geometry.
2. **Which gate was encountered most often?**  
   The Stop Bounds Gate (`STOP_TOO_WIDE` = 126, `STOP_BELOW_MINIMUM` = 3) was the single largest bottleneck, eliminating 43.88% (129/294) of all candidates.
3. **What percentage were STOP_TOO_WIDE?**  
   42.86% (126 / 294).
4. **What percentage were RR_BELOW_MINIMUM?**  
   24.49% (72 / 294).
5. **What percentage were HTF_CONTEXT_INVALID?**  
   20.07% (59 / 294).
6. **What percentage were TP2_NOT_BEYOND_TP1?**  
   11.56% (34 / 294).
7. **How close were stop failures to 3.5 ATR?**  
   20 candidates (15.87%) were near misses within 3.50–4.00 ATR. The closest was 3.5026 ATR. However, 60.32% were > 5.00 ATR.
8. **How close were RR failures to 3.8R?**  
   Zero near misses. The highest observed RR was 2.3790R. All 72 candidates were far below the 3.80R threshold.
9. **How common was TP2 failure?**  
   34 candidates (11.56%). All 34 had TP2 inverted behind TP1.
10. **How many candidates are objectively near misses?**  
    27 candidates (20 Stop near misses + 7 TP2 near misses). Zero RR near misses.
11. **How many are objectively far from qualification?**  
    148 candidates (50.34%) based on stop > 4.5 ATR, RR < 2.0R, or TP2 inverted > 5.0 points.
12. **What can we conclude about the score gate?**  
    The score gate was never reached. Zero candidates were rejected by score because 100% failed earlier gates.
13. **What can we conclude about HTF rejection?**  
    59 candidates (20.07%) failed macro directional alignment at Gate 1.
14. **What information remains unavailable?**  
    Candidate score, D1/H4 sub-flags, and entry/stop prices for candidates rejected before geometry assignment.
15. **What should be investigated next?**  
    Investigate zone anchoring dynamics causing stop overruns and examine target geometry causing low RR.

---

## 8. Preserved Artifacts Inventory

All diagnostic artifacts have been persisted to:  
`runs/probe_oct2025_reference/diagnostic_01/`

| Filename | Description | Rows / Entries |
|:---|:---|:---:|
| `october_candidate_autopsy.csv` | Full candidate-level ledger for all 294 evaluations | 294 rows |
| `rejection_funnel.csv` | Sequential gate-by-gate attrition funnel | 8 stages |
| `stop_atr_distribution.csv` | Statistical distribution & bucket counts for Stop ATR | 7 buckets + stats |
| `rr_distribution.csv` | Statistical distribution & bucket counts for Blended RR | 6 buckets + stats |
| `tp_geometry.csv` | Granular geometry & inverted gap analysis for TP2 | 34 rows |
| `htf_rejection_analysis.csv` | Ledger of all HTF gate failures | 59 rows |
| `score_distribution.csv` | Audit of score gate bypass | 294 rows |
| `near_miss_candidates.csv` | Top near-miss candidates ranked by delta | 27 rows |
| `far_from_qualification.csv` | Representative candidates furthest from passing | 35 rows |
| `gate_interaction_matrix.csv` | Cross-gate observable interaction matrix | 294 rows |
| `diagnostic_summary.json` | Machine-readable comprehensive summary | Full JSON |
| `diagnostic_report.md` | Human-readable comprehensive forensic report | Complete Report |

---

## Diagnostic Run #1 Status

* Existing frozen artifacts analyzed: YES
* Backtest rerun: NO
* Strategy modified: NO
* Engine modified: NO
* Parameters modified: NO
* Optimization performed: NO
* Phase B started: NO
* Active candidates analyzed: 294
* Approved trades: 0
* Score analysis: UNAVAILABLE
* Detailed HTF sub-analysis: UNAVAILABLE

### Main finding
The zero-trade outcome in October 2025 is fully explained by severe upstream gating attrition across four distinct gates: 43.88% of candidates failed Stop Bounds (primarily `STOP_TOO_WIDE`), 24.49% failed Blended RR, 20.07% failed HTF Context, and 11.56% failed TP2 Geometry. Not a single candidate reached the Score Gate or Risk Limits.

### Closest near-miss pattern
The only meaningful near misses in the dataset occurred at the Stop Bounds Gate (20 candidates within 3.50–4.00 ATR) and the TP2 Geometry Gate (7 candidates within 0.50 points of TP1).

### Far-from-qualification pattern
The RR gate exhibited extreme structural non-viability: 100% of candidates reaching the RR gate produced blended RR below 2.50R (median 0.996R) against a 3.80R requirement, yielding zero near misses. Furthermore, 60.32% of stop failures exceeded 5.00 ATR (reaching up to 26.89 ATR).

### Largest observed bottleneck
`STOP_TOO_WIDE` at Gate 2 is the largest single observed bottleneck, rejecting 126 candidates (42.86%). However, for all 106 candidates that successfully passed both HTF and Stop Bounds, 100% of those reaching the RR gate were eliminated by `RR_BELOW_MINIMUM`.

### Recommended next investigation
Conduct an algorithmic audit of the target level selection and zone anchor stop calculation to understand why structural stops routinely exceed 5.0 ATR and why target placement produces less than 2.0R blended reward.
