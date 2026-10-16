# Diagnostic Run #2 — Read-Only Stop / Target / RR Geometry Audit

## Executive Summary

This investigation performed a comprehensive, **read-only algorithmic audit** of the frozen Phase-A-v2 REFERENCE trading engine (`smc-trading-engine`) to examine the exact end-to-end trade geometry:

$$\text{Setup} \longrightarrow \text{Entry} \longrightarrow \text{Structural Stop} \longrightarrow \text{TP1} \longrightarrow \text{TP2} \longrightarrow \text{Blended RR} \longrightarrow \text{Gate Decision}$$

The audit investigated whether the extreme trade geometry observed in the October 2025 baseline (stops ranging up to 26.89 ATR, 72 RR failures capped below 2.38R, and 34 inverted TP2 configurations) was caused by an implementation bug, a component interaction, or the current strategy specification.

### Key Audit Findings
1. **Mathematical Implementation Correctness:** 100% of the 72 blended R:R calculations reproduced independently matched the engine's recorded values to machine precision ($\Delta = 0.00 \times 10^{-26}$). The code contains no formulaic, indexing, rounding, or unit errors.
2. **Structural Stop Behavior:** Stops are placed at `pool.price - buffer` (BULLISH) / `pool.price + buffer` (BEARISH) where `pool` is the liquidity pool swept in the setup's originating MSS event (`STOP-ANCHOR-01`). Stops frequently exceed 5 ATR (and reach 10–27 ATR) because the swept pool precedes the displacement leg, and long setup expiry windows (up to 12+ hours) allow price to expand dozens of points before retesting the zone.
3. **TP2 Inversion Behavior:** 34 candidates exhibited $TP2 \le TP1$. This occurs because TP1 (nearest internal pool) and TP2 (nearest external pool) are selected independently with zero cross-category ordering constraints, as explicitly documented in `target_selector.py` line 40. The `TP2_NOT_BEYOND_TP1` gate in `trade_decision_generator.py` correctly identified and rejected all 34 inverted setups.
4. **Structural R:R Deficit:** The failure of 100% of candidates to reach the $3.80R$ threshold is primarily driven by target selection geometry. Because TP1 is constrained to the *nearest* internal pool (yielding $\approx 0.5\text{--}1.2R$) and TP2 to the *nearest* external pool (yielding $\approx 1.0\text{--}2.5R$), the 30/70 weighted blended R:R mechanically clusters between $0.4R$ and $1.5R$. Requiring $3.80R$ against the mechanically nearest liquidity pools is structurally unreachable under current parameters.

---

## 1. Traceability & Code Path Verification

The complete pipeline path was mapped across the codebase:

| Stage | Responsible Module & Function | Lines | Inputs & Logic | Output |
| :--- | :--- | :--- | :--- | :--- |
| **Setup Creation** | `backtest/pipeline.py`<br>`_build_setup_candidate` | 728–803 | Zone, MSS event, Sweeps, Pools, M5 ATR.<br>`invalidation_level = pool.price - 0.10*ATR` | `Setup` domain object |
| **Zone Touch & M5 Confirmation** | `strategy/confirmation/confirmation_engine.py`<br>`ConfirmationEngineImpl.process_touch` | 114–206 | M5 Candles, Zone boundaries, `available_at`.<br>Micro-swing pivot break $\ge 0.5\text{ ATR}$ retracement. | `TouchEvent` (`CONFIRMED`) |
| **Entry Price** | `backtest/pipeline.py`<br>`_process_confirmed_setup` | 911–929 | `break_candle_id`.<br>`entry_price = break_candle.close` | Intended entry price |
| **Structural Stop** | `backtest/pipeline.py`<br>`_process_confirmed_setup` | 930–941 | `stop_loss = setup.invalidation_level`<br>Sanity check: stop must be on valid side of entry. | Structural stop price |
| **Target Selection (TP1 & TP2)** | `strategy/targets/target_selector.py`<br>`select_targets` | 120–185 | Unconsumed pools ahead of entry $\ge 1.0\text{ ATR}$.<br>TP1 = nearest INTERNAL pool.<br>TP2 = nearest EXTERNAL pool. | `TargetSelectionResult`<br>(`take_profit_1`, `take_profit_2`) |
| **Reward & R:R** | `decision/trade_decision_generator.py`<br>`_compute_rr` | 224–229 | $|tp - entry| / |entry - stop|$ | Individual leg R:R |
| **Blended R:R** | `decision/trade_decision_generator.py`<br>`_compute_blended_rr` | 231–254 | `0.30 * rr_tp1 + 0.70 * rr_tp2` | Blended R:R multiple |
| **TP2 Order Guard** | `decision/trade_decision_generator.py`<br>`generate` | 155–160 | Checks if TP2 is closer to entry than TP1. | `TP2_NOT_BEYOND_TP1` rejection |
| **Sequential Gates** | `decision/trade_decision_generator.py`<br>`generate` | 112–180 | Sequential checks: HTF $\to$ Stop bounds $\to$ TP2 order $\to$ RR gate $\to$ Score. | `TradeDecision` or `Rejection` |

---

## 2. Structural Stop Audit

### Candidate Distribution & Root Cause
The October 2025 baseline produced 126 `STOP_TOO_WIDE` rejections (42.86% of all candidates), with stop multiples ranging from 3.5026 ATR to 26.8852 ATR (median 5.6459 ATR).

### Representative Candidate Traces
From `stop_geometry_audit.csv` and `extreme_stop_analysis.csv`:
* **Min Failure (3.5026 ATR):** `setup:ob:mss:sweep:EQUAL_LOW:2025-10-03T19:10:00:3884.875`
  * Entry: 3893.845, Stop: 3884.618, ATR: 2.634. Stop distance = 9.227 pts ($3.5026\text{ ATR}$).
  * Marginal boundary overrun: displacement was modest, but entry closed 9.2 pts above the swept low.
* **Median Failure (5.6476 ATR):** `setup:ob:mss:sweep:EQUAL_LOW:2025-09-30T14:10:00:3827.863`
  * Entry: 3846.948, Stop: 3827.116, ATR: 3.511. Stop distance = 19.832 pts ($5.648\text{ ATR}$).
  * Standard impulse: a 19 pt displacement leg formed an Order Block at 3846. Retest entry was 19.8 pts from the swept low.
* **Maximum Failure (26.8852 ATR):** `setup:ob:mss:sweep:EQUAL_LOW:2025-09-30T11:45:00:3814.145`
  * Entry: 3862.165, Stop: 3813.682, ATR: 1.803. Stop distance = 48.483 pts ($26.885\text{ ATR}$).
  * Extended aging: the setup candidate was created on 2025-09-30 at 12:50 from an EQUAL_LOW at 3814.145. Price rallied 48 points over the next 11 hours. When price finally touched an Order Block at 3862 on 2025-10-01 at 00:00, the stop was still anchored 48 points below at 3813.682!

### Answers to Structural Stop Audit Questions
1. **Intended Invalidation Point:** The relevant swept low/high buffered by $0.10 \times ATR(14)$ per Specification §8.1–§8.2 and `DECISIONS.md` (`STOP-ANCHOR-01`).
2. **Placement:** The stop is placed beyond that invalidation point (`pool.price - buffer` for BULLISH, `pool.price + buffer` for BEARISH).
3. **Anchor Selection:** The stop is anchored to the liquidity pool referenced by `MSSEvent.sweep_id -> SweepEvent.liquidity_pool_id`.
4. **Older Structure Reference:** Yes. If a setup remains pending for several hours under long expiry settings, the swept pool represents a market level from many hours prior.
5. **Cross Unrelated Structures:** Yes. The stop spans the entire pre-MSS sweep, displacement leg, and subsequent continuation structure.
6. **Timeframe Influence:** The swept pool can originate from higher timeframe structures (PDL, PWL, or H4 swings converted to pools), which naturally have larger price ranges.
7. **Cause of 10–27 ATR Stops:** Combinations of strong multi-hour directional trends and long setup validity windows where the entry occurs far above/below the original sweep anchor.
8. **ATR Timeframe:** ATR is calculated from the M5 execution timeframe (`Timeframe.M5`), consistent with §8.2.
9. **Unit Mismatches:** None. All values are in price points ($/oz on XAUUSD).
10. **Index/Timestamp Mismatches:** None.
11. **Mathematical Correctness:** 100% correct.
12. **Are Extreme Stops Expected?** Under the literal rule that structural stops are anchored to the pre-MSS swept pool regardless of subsequent displacement size or time elapsed, wide stops are an inevitable geometric consequence.

---

## 3. TP1 & TP2 Target Selection Audit

### TP1 Geometry
* TP1 is selected as the nearest unconsumed internal liquidity pool (EQUAL_HIGH/LOW, SESSION_HIGH/LOW, minor swing) ahead of entry $\ge 1.0\text{ ATR}$.
* TP1 distances cluster closely between 1.0 and 2.5 ATR from entry.
* TP1 functions as a conservative scale-out target to take off 30% of position size.

### TP2 Geometry & 34 Inversion Failures
* TP2 is selected as the nearest unconsumed external liquidity pool (PDH/PDL, PWH/PWL, major swing) ahead of entry $\ge 1.0\text{ ATR}$.
* In 34 candidates, $TP2 \le TP1$ (signed gap between $-0.02\text{ pts}$ and $-23.53\text{ pts}$).
* **Root Cause:** TP1 and TP2 are queried independently against different pool subsets (`internal_candidates` vs `external_candidates`). If a major swing high sits at 3870 and an equal high sits at 3875 with entry at 3865, TP2 is at 3870 (5 pts away) while TP1 is at 3875 (10 pts away).
* **Codebase Disclosure:** `target_selector.py` lines 40–44 explicitly documents:
  > *"DISCOVERED EDGE CASE, NOT FIXED... TP1 (nearest internal) and TP2 (nearest external) are selected independently. It is mathematically possible... for TP2 to end up nearer to entry than TP1. No cross-category ordering constraint was approved, so none is applied."*
* The engine protects execution integrity via the `TP2_NOT_BEYOND_TP1` gate in `trade_decision_generator.py:155`, which declined all 34 candidates.

---

## 4. Blended R:R Calculation Audit

### Independent Reproduction
The formula specified in `DECISIONS.md` (`RR-01`) was independently evaluated across all 72 RR-rejected candidates:

$$\text{Blended Reward} = 0.30 \times |TP1 - \text{Entry}| + 0.70 \times |TP2 - \text{Entry}|$$
$$\text{Blended RR} = \frac{\text{Blended Reward}}{|\text{Entry} - \text{Stop}|}$$

* **Verification Results:** 72 / 72 candidates PASSED (100.0%).
* Maximum absolute discrepancy: $0.00 \times 10^{-26}$.
* Relative error: $0.00\%$.

### Distribution of the 72 RR Failures
From `rr_distribution_recomputed.csv`:
* $\text{RR} < 0.5R$: 18 candidates (25.00%)
* $0.5R \le \text{RR} < 1.0R$: 35 candidates (48.61%)
* $1.0R \le \text{RR} < 1.5R$: 13 candidates (18.06%)
* $1.5R \le \text{RR} < 2.0R$: 5 candidates (6.94%)
* $2.0R \le \text{RR} < 2.5R$: 1 candidate (1.39%)
* $\ge 2.5R$: 0 candidates (0.00%)
* Mean RR: $0.8583R$, Median RR: $0.7313R$, Max RR: $2.3790R$.

### Why Did 100% Fail 3.8R?
1. For setups that pass the stop gate ($\le 3.5\text{ ATR}$), the median stop distance is $\approx 2.2\text{ ATR}$.
2. TP1 is anchored to the *nearest* internal pool (median $\approx 1.2\text{ ATR}$ $\implies$ individual RR $\approx 0.55R$).
3. TP2 is anchored to the *nearest* external pool (median $\approx 2.0\text{ ATR}$ $\implies$ individual RR $\approx 0.91R$).
4. Blended RR is $0.30(0.55) + 0.70(0.91) \approx 0.80R$.
5. To attain $3.80R$ with TP1 at $1.0R$, TP2 would need an individual RR of:
   $$\frac{3.80 - 0.30(1.0)}{0.70} = 5.0R$$
   With a 2.5 ATR stop, TP2 would need to be $12.5\text{ ATR}$ away. Because `target_selector.py` chooses the *nearest* external pool, it almost never selects a target 12.5 ATR away when closer major swings exist.
6. The failure at 3.8R is an insurmountable structural gap between "nearest pool" target selection and the 3.8R hurdle.

---

## 5. Report Answers to Required Questions

1. **What exact code calculates the structural stop?**
   `backtest/pipeline.py`, lines 748–752 (in `_build_setup_candidate`) and line 930 (in `_process_confirmed_setup`).
2. **Is the structural stop calculation internally correct?**
   Yes. It exactly computes `pool.price +/- buffer` as specified in `STOP-ANCHOR-01`.
3. **Why are stops frequently >5 ATR?**
   Because the stop anchors to the pre-MSS swept pool. The displacement leg plus the retest travel distance naturally span several ATRs.
4. **Why do some reach 10–27 ATR?**
   Due to extended setup lifespan (hours) during which price rallied/fell substantially away from the original sweep before triggering a zone retest.
5. **What exact code selects TP1?**
   `strategy/targets/target_selector.py`, lines 149–176 (`_nearest(internal_candidates, entry)`).
6. **What exact code selects TP2?**
   `strategy/targets/target_selector.py`, lines 150–177 (`_nearest(external_candidates, entry)`).
7. **Why do 34 candidates have TP2 <= TP1?**
   TP1 and TP2 are selected independently from different candidate sets without any cross-target ordering constraint.
8. **Is TP2 inversion expected or suspicious?**
   It is an expected structural edge case of independent nearest-neighbor selection, explicitly disclosed in the codebase comments and handled by the `TP2_NOT_BEYOND_TP1` rejection gate.
9. **Is blended RR calculated correctly?**
   Yes. Exactly `(0.30 * rr_tp1) + (0.70 * rr_tp2)`.
10. **Does the independent RR calculation match the engine?**
    Yes. 72 of 72 candidates match to within machine precision ($0.00 \times 10^{-26}$).
11. **Is there any unit/timeframe/index mismatch?**
    No. All units are in price points, execution timeframe ATR (M5) is consistently used, and candle indexing matches.
12. **Is there evidence of stop/target structural misalignment?**
    Yes, structurally. The stop is macro-anchored to the pre-MSS sweep (large distance), while targets are micro-anchored to the nearest available liquidity pools (small distance).
13. **Is the 3.8R failure primarily caused by target selection, stop placement, or both?**
    Both, but primarily target selection: even candidates with tight stops (1.5–2.0 ATR) cannot achieve 3.8R because nearest-neighbor target selection caps TP1 and TP2 at modest multiples.
14. **Is there evidence of an implementation defect?**
    No. The implementation faithfully executes the approved and locked design decisions.
15. **Explicit defect statement:**
    **There is NO code defect in the trading engine implementation.**
16. **Defect path:**
    Not applicable (no defect identified).

---

## 6. Final Decision

### A — IMPLEMENTATION APPEARS CORRECT

The geometry is behaving strictly according to the current strategy specification and locked architectural decisions (`STOP-ANCHOR-01`, `RR-01`, `TP1/TP2 Resolution Gate`, and Phase A Owner Decisions). No code defect identified.
