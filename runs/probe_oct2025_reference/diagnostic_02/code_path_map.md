# Code Path Map — SMC Trading Engine Geometry Pipeline

This document maps the exact execution path from a confirmed setup to final trade decision in the frozen **Phase-A-v2 REFERENCE trading engine** (`smc-trading-engine`).

---

## 1. Setup Creation
* **File:** `backtest/pipeline.py`
* **Function / Class:** `BacktestPipelineImpl._build_setup_candidate(zone, mss, sweeps, pools, m5_atr)`
* **Lines:** 728–803
* **Inputs:**
  * `zone`: `Zone` object (originating FVG or Order Block)
  * `mss`: `MSSEvent` object (Market Structure Shift)
  * `sweeps`: Sequence of `SweepEvent`
  * `pools`: Sequence of `LiquidityPool`
  * `m5_atr`: Dict mapping `candle_id` to M5 ATR(14)
* **Outputs:** `Setup` domain object with `state=SetupState.WAITING_FOR_RETEST`, `invalidation_level`, `created_at`, `expiry_at`, `score`.
* **Formula / Logic:**
  ```python
  created_at = max(zone.creation_timestamp, mss.confirmed_at)
  sweep = next((s for s in sweeps if s.sweep_id == mss.sweep_id), None)
  pool = next((p for p in pools if sweep is not None and p.pool_id == sweep.liquidity_pool_id), None)
  atr_candle_id = Candle.build_id(self._symbol, Timeframe.M5, created_at)
  atr_at_setup = m5_atr.get(atr_candle_id)

  if pool is not None and atr_at_setup is not None:
      buffer = self._config.strategy.initial_sl_buffer_atr.value * atr_at_setup
      invalidation_level = (
          pool.price - buffer if zone.direction == Direction.BULLISH else pool.price + buffer
      )
  else:
      invalidation_level = zone.lower if zone.direction == Direction.BULLISH else zone.upper
  ```
* **Structural Interpretation:** Implements `STOP-ANCHOR-01` (Specification §8.1–§8.2). Anchors the structural stop to the pre-MSS swept liquidity pool rather than the zone boundary.

---

## 2. Entry Calculation
* **File:** `backtest/pipeline.py`
* **Function / Class:** `BacktestPipelineImpl._process_confirmed_setup(...)`
* **Lines:** 911–929
* **Inputs:**
  * `setup`: `Setup` object in `CONFIRMED` state
  * `setup.touch_events[-1].break_candle_id`: The ID of the candle that broke the micro-swing pivot
  * `self._m5_candle_by_id`: Candle map
* **Outputs:** `entry_price: Decimal`
* **Formula / Logic:**
  ```python
  break_candle = self._m5_candle_by_id.get(break_candle_id)
  entry_price = break_candle.close
  ```
* **Structural Interpretation:** Entry is taken at the market on the close of the M5 candle that confirmed the break of the qualified micro-swing during the zone retest.

---

## 3. Zone Selection
* **File:** `strategy/confirmation/confirmation_engine.py` & `backtest/pipeline.py`
* **Function / Class:** `ConfirmationEngineImpl.process_touch(...)` / `BacktestPipelineImpl._step_setups(...)`
* **Lines:** `confirmation_engine.py:114–206`, `pipeline.py:670–726`
* **Inputs:** M5 candles, `Zone` boundaries (`zone.lower`, `zone.upper`), `available_at` boundary.
* **Outputs:** `TouchEvent` with `TouchOutcome.CONFIRMED` or `TouchOutcome.ACTIVE`.
* **Formula / Logic:**
  Identifies first candle overlapping the zone (`c.low <= zone.upper and c.high >= zone.lower`). Scans subsequent candles for 1-candle micro-swing pivots with retracement $\ge 0.5 	ext{ ATR}$. Confirms when a candle closes beyond the selected micro-swing pivot.
* **Structural Interpretation:** Ensures entry is triggered only after price has entered the zone and shown micro-structural confirmation of reversal.

---

## 4. Structural Stop Calculation
* **File:** `backtest/pipeline.py`
* **Function / Class:** `BacktestPipelineImpl._process_confirmed_setup(...)`
* **Lines:** 930–941
* **Inputs:** `setup.invalidation_level`, `entry_price`, `setup.direction`
* **Outputs:** `stop_loss: Decimal`
* **Formula / Logic:**
  ```python
  stop_loss = setup.invalidation_level
  # Directional sanity guard:
  if setup.direction.value == "BULLISH" and stop_loss >= entry_price:
      return setup, events
  if setup.direction.value == "BEARISH" and stop_loss <= entry_price:
      return setup, events
  ```
* **Structural Interpretation:** The stop loss passed to trade decision generation is precisely `setup.invalidation_level` established at setup creation (anchored to the swept pool).

---

## 5. TP1 Calculation
* **File:** `strategy/targets/target_selector.py`
* **Function / Class:** `select_targets(...)` -> `_nearest(internal_candidates, entry)`
* **Lines:** 120–185
* **Inputs:** `direction`, `entry_price`, active `pools`, `consumed_pool_ids`, `at`, `atr`, `min_target_atr_mult` (1.0x ATR)
* **Outputs:** `take_profit_1: Decimal`, `tp1_pool_id: str`
* **Formula / Logic:**
  Filters pools:
  1. Direction match: `_HIGH_TYPES` for BULLISH, `_LOW_TYPES` for BEARISH.
  2. Unconsumed: `pool_id not in consumed_pool_ids`.
  3. Ahead of entry: `pool.price > entry` (BULLISH) or `pool.price < entry` (BEARISH).
  4. Noise filter: $|pool.price - entry| \ge min\_target\_atr\_mult 	imes ATR$.
  5. Category: `classify(pool) == INTERNAL` (EQUAL_HIGH/LOW, SESSION_HIGH/LOW, minor SWING_HIGH/LOW).
  Selection:
  ```python
  tp1_pool = min(internal_candidates, key=lambda p: (abs(p.price - entry), p.pool_id))
  take_profit_1 = tp1_pool.price
  ```
* **Structural Interpretation:** Selects the nearest unconsumed internal liquidity pool ahead of entry that exceeds the 1.0 ATR noise floor.

---

## 6. TP2 Calculation
* **File:** `strategy/targets/target_selector.py`
* **Function / Class:** `select_targets(...)` -> `_nearest(external_candidates, entry)`
* **Lines:** 120–185
* **Inputs:** Same pool candidate pool, filtered for `classify(pool) == EXTERNAL` (PDH/PDL, PWH/PWL, major SWING_HIGH/LOW).
* **Outputs:** `take_profit_2: Decimal`, `tp2_pool_id: str`
* **Formula / Logic:**
  ```python
  tp2_pool = min(external_candidates, key=lambda p: (abs(p.price - entry), p.pool_id))
  take_profit_2 = tp2_pool.price
  ```
* **Structural Interpretation:** Selects the nearest unconsumed external liquidity pool ahead of entry exceeding the noise floor. Selected independently of TP1.

---

## 7. Reward Calculation
* **File:** `decision/trade_decision_generator.py`
* **Function / Class:** `TradeDecisionGeneratorImpl._compute_rr(...)`
* **Lines:** 224–229
* **Inputs:** `direction`, `entry`, `stop`, `tp`
* **Outputs:** `Decimal` R-multiple
* **Formula / Logic:**
  ```python
  risk = abs(entry - stop)
  if risk == 0:
      return Decimal("0")
  reward = abs(tp - entry)
  return reward / risk
  ```
* **Structural Interpretation:** Absolute price difference reward normalized by absolute stop risk.

---

## 8. Blended RR Calculation
* **File:** `decision/trade_decision_generator.py`
* **Function / Class:** `TradeDecisionGeneratorImpl._compute_blended_rr(...)`
* **Lines:** 231–254
* **Inputs:** `direction`, `entry`, `stop`, `take_profit_1`, `take_profit_2`, `tp1_allocation_pct` (30%)
* **Outputs:** `blended_rr: Decimal`
* **Formula / Logic:**
  ```python
  rr_tp1 = self._compute_rr(direction, entry, stop, take_profit_1)
  rr_tp2 = self._compute_rr(direction, entry, stop, take_profit_2)
  tp1_weight = tp1_allocation_pct / Decimal("100")  # 0.30
  tp2_weight = Decimal("1") - tp1_weight           # 0.70
  blended_rr = (tp1_weight * rr_tp1) + (tp2_weight * rr_tp2)
  ```
* **Structural Interpretation:** Reflects the integrated multi-target position structure per decision `RR-01`.

---

## 9. TP2 Ordering Validation Guard
* **File:** `decision/trade_decision_generator.py`
* **Function / Class:** `TradeDecisionGeneratorImpl.generate(...)`
* **Lines:** 155–160
* **Inputs:** `take_profit_1`, `take_profit_2`, `setup.direction`
* **Outputs:** `TradeDecisionRejection("TP2_NOT_BEYOND_TP1")` if violated.
* **Formula / Logic:**
  ```python
  tp2_closer = (take_profit_2 < take_profit_1) if setup.direction == Direction.BULLISH else (take_profit_2 > take_profit_1)
  if tp2_closer:
      return TradeDecisionRejection("TP2_NOT_BEYOND_TP1", ...)
  ```
* **Structural Interpretation:** Fast-rejects any candidate where the nearest external pool is closer to entry than the nearest internal pool.

---

## 10. Sequential Gate Architecture
In `TradeDecisionGeneratorImpl.generate(...)` (lines 112–180), gates are evaluated strictly in fixed sequence:
1. **Gate 1: HTF Context Gate** (lines 114–115): `if not htf_context_still_valid: return TradeDecisionRejection("HTF_CONTEXT_INVALID")`
2. **Gate 2a: Stop Floor Gate** (lines 121–131): `if stop_distance < min_stop_atr * atr: return TradeDecisionRejection("STOP_BELOW_MINIMUM")` ($0.25 	imes ATR$)
3. **Gate 2b: Stop Ceiling Gate** (lines 133–145): `if stop_distance > max_stop_atr * atr: return TradeDecisionRejection("STOP_TOO_WIDE")` ($3.50 	imes ATR$)
4. **Gate 3: TP2 Geometry Guard** (lines 155–160): `if tp2_closer: return TradeDecisionRejection("TP2_NOT_BEYOND_TP1")`
5. **Gate 4: Blended RR Gate** (lines 167–173): `if blended_rr < minimum_rr: return TradeDecisionRejection("RR_BELOW_MINIMUM")` ($3.80R$)
6. **Gate 5: Score Gate** (lines 175–176): `if setup.score < minimum_score: return TradeDecisionRejection("SCORE_BELOW_THRESHOLD")`
7. **Gate 6: Risk Engine Gate** (lines 178–179): `if not risk_decision.approved: return TradeDecisionRejection(...)`
