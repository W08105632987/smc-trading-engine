# Diagnostic Run #1 -- Capability & Data Sufficiency Audit Report

## 1. Artifact Inventory

Inspected directory: `runs/probe_oct2025_reference/`

| Artifact | Format | Size | Description | Sufficient for Diagnostic #1? | Unique Candidate ID? | Timestamps Preserved? | Geometry Preserved? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `events.jsonl` | JSONL | 32.65 MB | 53,357 granular lifecycle events across all components | **Partial** (has geometry, missing score & HTF breakdown) | **YES** (`entity_id`) | **YES** (ISO 8601 UTC) | **YES** (ATR multiples, stop distances, zone bounds) |
| `run_metadata.json` | JSON | 11.9 KB | Config hash, strategy parameters, execution model, data file metadata | **YES** | N/A | **YES** | **YES** (All engine threshold specifications) |
| `run_log.txt` | Text | 180.5 KB | 1,670 progress heartbeat lines, streaming stats, replay timings | Supplementary | N/A | **YES** | N/A |
| `statistics_final.json` | JSON | 374 B | Final trade statistics (0 trades, 0 win rate, 0 DD) | Supplementary | N/A | N/A | N/A |
| `statistics_robustness.json` | JSON | 533 B | Outlier robustness calculations | Supplementary | N/A | N/A | N/A |
| `trade_ledger.csv` | CSV | 537 B | Standard 38-column TradeRecord ledger (0 trade rows) | Supplementary | N/A | N/A | N/A |
| `trades.jsonl` | JSONL | 0 B | Serialized TradeRecord JSON lines (0 trade rows) | Supplementary | N/A | N/A | N/A |
| `plausibility_report.json` | JSON | 51 B | Plausibility check output (0 flagged trades, 0 warnings) | Supplementary | N/A | N/A | N/A |
| `baseline_report.md` | Markdown | ~2.5 KB | Automated Phase-A baseline summary report | Supplementary | N/A | **YES** | Summary counts |

---

## 2. 294-Candidate Reconstruction Status

All **294 active October setup evaluations** are uniquely identified in `events.jsonl` by `entity_id` and have full chronological state transitions (`SETUP_CREATED` -> `SETUP_CONFIRMED` -> `TRADE_REJECTED`).

### Active Rejection Breakdown:
* **STOP_TOO_WIDE**: 126 candidates
* **RR_BELOW_MINIMUM**: 72 candidates
* **HTF_CONTEXT_INVALID**: 59 candidates
* **TP2_NOT_BEYOND_TP1**: 34 candidates
* **STOP_BELOW_MINIMUM**: 3 candidates
* **SCORE_BELOW_THRESHOLD**: 0 candidates
* **Total Active Evaluations**: **294 candidates**

---

## 3. Field-by-Field Availability Matrix

| Field | Status | Count / 294 | Notes |
| :--- | :--- | :--- | :--- |
| **Candidate ID** | **AVAILABLE** | 294 / 294 (100%) | Preserved in `entity_id` |
| **Direction** | **AVAILABLE** | 294 / 294 (100%) | Encoded in entity ID (sweep type: EQUAL_LOW=BULLISH, EQUAL_HIGH=BEARISH, or explicit) |
| **Setup Timestamp** | **AVAILABLE** | 294 / 294 (100%) | Preserved in `SETUP_CREATED` event timestamp |
| **Zone Creation Timestamp** | **AVAILABLE** | 294 / 294 (100%) | Encoded in entity ID timestamp anchor |
| **Zone / Touch Info** | **AVAILABLE** | 294 / 294 (100%) | `zone_upper`, `zone_lower`, `zone_width`, `zone_type`, `entry_position_in_zone` |
| **Confirmation Timestamp** | **AVAILABLE** | 294 / 294 (100%) | Preserved in `SETUP_CONFIRMED` and `TRADE_REJECTED` timestamps |
| **Entry Price** | **PARTIAL** | 201 / 294 (68.4%) | Explicit in STOP_TOO_WIDE (126), RR_BELOW_MINIMUM (72), STOP_BELOW_MINIMUM (3); `MISSING` in HTF (59) & TP2 (34) |
| **Structural Stop** | **PARTIAL** | 201 / 294 (68.4%) | Explicit in 201 candidates; `MISSING` in HTF (59) & TP2 (34) |
| **ATR at Decision Time** | **AVAILABLE** | 294 / 294 (100%) | Explicit in `atr_at_decision` for all 294 candidates |
| **Stop Distance** | **AVAILABLE** | 294 / 294 (100%) | Absolute distance in 201 candidates; `stop_distance_atr_mult` in all 294 |
| **Take Profit 1** | **PARTIAL** | 106 / 294 (36.1%) | Explicit in RR_BELOW_MINIMUM (72) & TP2_NOT_BEYOND_TP1 (34); `MISSING` in others |
| **Take Profit 2** | **PARTIAL** | 106 / 294 (36.1%) | Explicit in RR_BELOW_MINIMUM (72) & TP2_NOT_BEYOND_TP1 (34); `MISSING` in others |
| **TP1 Distance** | **AVAILABLE** | 294 / 294 (100%) | `reward_tp1_atr_mult * atr_at_decision` available for all 294 |
| **TP2 Distance** | **AVAILABLE** | 294 / 294 (100%) | `reward_tp2_atr_mult * atr_at_decision` available for all 294 |
| **Risk/Reward (RR)** | **AVAILABLE** | 294 / 294 (100%) | Exact `blended_rr` in 72 candidates; exact `reward_mult / stop_mult` reconstructible for all 294 |
| **Score** | **`MISSING`** | **0 / 294 (0.0%)** | `setup.score` was NOT serialized into `events.jsonl` |
| **D1 Context** | **`MISSING`** | **0 / 294 (0.0%)** | D1 bias state at decision time was NOT serialized |
| **H4 Context** | **`MISSING`** | **0 / 294 (0.0%)** | H4 bias / warning breakdown was NOT serialized |
| **Final Rejection Reason** | **AVAILABLE** | 294 / 294 (100%) | Preserved in `reason_code` |
| **Rejection Stage** | **AVAILABLE** | 294 / 294 (100%) | Trade Decision Generator stage (Post-confirmation) |

---

## 4. Causality Assessment

* **Causality Integrity**: **PASS**.
* All recorded diagnostics in `events.jsonl` were strictly generated at bar close (`break_candle.timestamp_close`) using only historical indicators (M5 ATR, confirmed swings, active liquidity pools, and prior H4/D1 closes).
* Zero future market information was accessed or leaked.
* No outcome calculations (win/loss) are required or present.

---

## 5. Funnel Reconstruction Assessment

The complete pipeline sequential funnel is 100% reconstructible:
1. **Zone Created**: 15,740 events
2. **MSS Confirmed**: 15,739 events
3. **Setup Created (`WAITING_FOR_RETEST`)**: 9,336 events
4. **Touch Event (`WAITING_FOR_CONFIRMATION`)**: 617 events
5. **Setup Confirmed (`CONFIRMED`)**: 617 events
6. **Trade Decision Gate Evaluated**: 612 events (318 Warmup + 294 Active October)
7. **Rejection Funnel Order (Strict Gate Order in Engine)**:
   * **Stage 1: HTF Context Validation** -> 59 rejected (`HTF_CONTEXT_INVALID`)
   * **Stage 2: Stop Bounds Validation** -> 129 rejected (126 `STOP_TOO_WIDE`, 3 `STOP_BELOW_MINIMUM`)
   * **Stage 3: TP2 Geometry Validation** -> 34 rejected (`TP2_NOT_BEYOND_TP1`)
   * **Stage 4: Risk/Reward Validation** -> 72 rejected (`RR_BELOW_MINIMUM`)
   * **Stage 5: Score Validation** -> 0 rejected (`SCORE_BELOW_THRESHOLD`) -- *No candidate reached Stage 5 because all 294 candidates failed earlier hard gates (Stages 1-4).* 
   * **Stage 6: Risk Account Limits** -> 0 rejected
   * **Stage 7: Trade Approved** -> 0 approved -> 0 completed trades.

---

## 6. Geometry Availability

* **Stop / ATR Analysis**: **100% AVAILABLE**. `stop_distance_atr_mult` is preserved for all 294 candidates.
* **RR Analysis**: **100% AVAILABLE**. Exact blended RR, TP1 RR, and TP2 RR are preserved for all 72 RR rejections, and exact ATR reward/stop ratios are preserved for all others.
* **TP Geometry**: **100% AVAILABLE**. TP1 and TP2 distances in ATR multiples exist for all 294 candidates; exact prices exist for all 34 `TP2_NOT_BEYOND_TP1` candidates, showing exact target overlap/inversion.

---

## 7. HTF Context Availability

* **Overall Status**: **PARTIAL / CATEGORICAL ONLY**.
* We know exactly which 59 candidates failed HTF context.
* We **CANNOT** determine the internal breakdown (e.g., whether D1 alignment failed, H4 CHoCH warning was active, or H4 structure was opposed) because the sub-flags were not logged in `events.jsonl`.

---

## 8. Score Availability

* **Overall Status**: **`MISSING`**.
* The numeric score (0-100) and factor snapshot were computed in memory at setup creation, but were omitted from the event emission payload in `backtest/pipeline.py`.
* Consequently, candidate score distribution cannot be extracted from the existing files.

---

## 9. Near-Miss Feasibility

| Near-Miss Category | Definition | Feasibility | Reason |
| :--- | :--- | :--- | :--- |
| **Stop Near-Miss** | 3.5 ATR < Stop <= 4.0 ATR | **FEASIBLE** | `stop_distance_atr_mult` is preserved for all 126 `STOP_TOO_WIDE` candidates. |
| **RR Near-Miss (Broad)** | 3.0R <= RR < 3.8R | **FEASIBLE** | Exact `blended_rr` is preserved for all 72 `RR_BELOW_MINIMUM` candidates. |
| **RR Near-Miss (Narrow)** | 3.5R <= RR < 3.8R | **FEASIBLE** | Exact `blended_rr` is preserved for all 72 `RR_BELOW_MINIMUM` candidates. |
| **Score Near-Miss** | 65 <= Score <= 69 | **NOT FEASIBLE** | `score` is `MISSING` from artifacts. |
| **TP2 Near-Miss** | Threshold based on TP2 - TP1 | **FEASIBLE** | Exact TP1 and TP2 prices are preserved for all 34 `TP2_NOT_BEYOND_TP1` candidates. |
| **HTF Near-Miss** | Sub-component near miss | **NOT FEASIBLE** | HTF internal sub-state is `MISSING` from artifacts. |

---

## 10. Far-From-Qualification Feasibility

* **FEASIBLE** for Stop Loss (e.g., stops exceeding 10 ATR or 20 ATR) and RR (e.g., RR < 1.0).
* Not feasible for composite multi-gate scoring due to missing score values.

---

## 11. Zero-Trade-Path Verification

* **Audit Finding**: **100% VERIFIED**.
* Exhaustive scan of all 53,357 events confirms:
  * `TRADE_APPROVED`: 0 events
  * `ORDER_FILLED`: 0 events
  * `PROTECTED`: 0 events
  * `TRADE_CLOSED`: 0 events
  * Execution exceptions: 0
  * Risk / accounting drop: 0
* **Conclusion**: 294 confirmed setups were evaluated; 294 were formally rejected at the trade decision generator gate; 0 trades were approved. There is zero silent trade drop.

---

## 12. Missing Information Summary

1. `setup.score` (numeric score 0-100 and factor snapshot) for the 294 candidates.
2. D1 and H4 sub-state breakdown for the 59 `HTF_CONTEXT_INVALID` candidates.
3. Absolute entry and stop prices for the 59 HTF and 34 TP2 candidates (though their ATR distances and zone geometries are preserved).

---

## 13. Audit Conclusion: Is Rerun Necessary?

### **C -- INSUFFICIENT; RERUN REQUIRED (For Full Scope of Diagnostic #1)**

**Rationale**:
* If Diagnostic Run #1 strictly mandates analyzing **Score Near-Misses (65-69)** and the **D1/H4 Context Breakdown**, the existing artifacts **do not contain** those fields. A diagnostic-instrumented rerun of October would be required to emit them.
* **Important Nuance**: If the diagnostic scope is scoped to **Geometry & Funnel Analysis** (Stop distance ATR analysis, Stop near-miss 3.5-4.0 ATR, RR near-miss 3.0-3.8R, TP2 geometry, Funnel attrition, Zero-trade path verification), that sub-portion is **B -- SUFFICIENT WITH DIAGNOSTIC-ONLY CODE** without rerunning.

---

## 14. Exact Next Step

1. **DO NOT rerun the backtest yet.**
2. Present this Capability & Data Sufficiency Audit to the user and independent reviewer (ChatGPT).
3. Confirm whether to:
   * **Path 1**: Execute Diagnostic Run #1 on the **preserved geometry and funnel data** (Option B for geometry/funnel) using standalone read-only diagnostic code; OR
   * **Path 2**: Authorize a diagnostic-instrumented rerun of the frozen October baseline to serialize `score` and `HTF` sub-state into the event stream.