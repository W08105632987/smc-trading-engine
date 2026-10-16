# Decision Register

Records project-owner decisions that resolve an ambiguity engineering
was not authorized to guess at (per the "no silent assumptions" rule
governing the Milestone 10 blockers-1-5 pass). Each entry is locked
once recorded here — implementation must conform to it exactly, and
must not silently drift from it in later work.

---

## ATR-01 — ATR calculation method

**STATUS: LOCKED**

The ATR implementation (`strategy/indicators/atr.py`) is authorized as
part of the frozen strategy implementation contract, replacing its
previous "flagged, inferred, not verified" status.

### Definition

1. **Period:** 14 completed candles.
2. **Method:** Wilder's Average True Range smoothing.
3. **True Range:**
   `TR = max(high - low, abs(high - previous_close), abs(low - previous_close))`
   (first candle in a series has no previous close: `TR = high - low`).
4. **Initialization:** no ATR value exists until 14 completed candles
   are available — the first ATR value is the simple average of the
   first 14 True Range values.
5. **Subsequent smoothing:** Wilder's recursive smoothing,
   `ATR_t = (ATR_{t-1} * 13 + TR_t) / 14`.
6. **Look-ahead:** prohibited — ATR at candle *i* uses only candles
   `0..i` of the series it belongs to.
7. **Timeframe:** ATR is timeframe-specific. A component consumes ATR
   calculated from its OWN candle timeframe, never a different one,
   and never one global series reused across timeframes:
   - M5 consumer → M5 ATR(14)
   - H4 consumer → H4 ATR(14)
   - A future component requiring another timeframe uses ATR(14) from
     that timeframe.

   (Already how `backtest/pipeline.py` calls the provider —
   `self._atr_provider(Timeframe.M5, m5_candles)` and
   `self._atr_provider(Timeframe.H4, h4_candles)` are two independent
   calls into two independent per-timeframe series; there was never a
   single shared/global ATR series to begin with, so this rule is
   confirmed as already satisfied, not something requiring a code
   change.)
8. **Numeric precision:** internal True Range / running-average
   arithmetic may use float; the value handed to any financial/risk/
   decision calculation is normalized to `Decimal` first (Numeric
   Precision Boundary, Technical Architecture v1.0).
9. **Determinism:** identical historical candle input produces
   identical ATR output.
10. **Gaps:** ATR never manufactures a candle across a data gap; it
    only operates on candles that genuinely exist in the canonical
    market-data stream (a gap-affected candle IS still a real,
    existing candle per v5 I10 — see gap_detector.py / this decision
    register's forthcoming GAP-01 if one is ever needed — so it is not
    excluded from the TR/ATR calculation on that basis alone).

### Explicit non-lock

This decision locks the ATR **calculation method only**. It does
**not** lock any strategy threshold expressed as a multiple of ATR.
In particular:

    micro_swing_min_retracement_atr = 0.1

remains **PROVISIONAL** and must stay configurable for Phase 3
sensitivity/ablation testing. ATR-01 answers "how is ATR computed";
it does not answer "is 0.1x ATR the right retracement threshold" —
that question was never open and this decision does not close it.

The ATR calculation itself must not be tuned/optimized against the
same historical data used for the first validation run (that would
reintroduce a parameter-selection problem this decision was meant to
avoid).

### Basis for this decision

Recorded by the project owner, not inferred by engineering, after
reviewing: (a) `period=14` was the only concrete number found anywhere
in the repository (a test fixture, `tests/test_domain_market_data.py`);
(b) Wilder smoothing is the conventional meaning of unqualified "ATR";
(c) the implementation was already built and tested around exactly
this definition; (d) the separately-provisional `0.1 x ATR` threshold
means this decision does not imply any strategy parameter has been
validated as optimal. No entry/exit/scoring/risk/TP/SL/setup rule is
changed by this decision.

---

## RR-01 — Minimum R:R gate measured as a BLENDED TP1/TP2 ratio

**STATUS: LOCKED**

**Context:** Real-data preflight runs showed 583/583 confirmed setups
(100%) rejected before becoming trades — 61% specifically on
`RR_BELOW_MINIMUM`. Investigated per explicit authorization to
re-evaluate strategy decisions using external SMC/ICT research,
cross-checked against the actual codebase.

**Finding:** `minimum_rr = 2.0` (PROVISIONAL) was gated against TP1
alone. TP1 is deliberately defined (`strategy/targets/target_selector.py`)
as the *nearest* internal liquidity pool — close by design, because
it's the target most likely to actually be reached. Multiple
independent external sources on this exact trading style describe the
same structural fact: a nearest/internal-liquidity target typically
produces roughly 1:1 to 1:1.5 R:R, not 2:1+, regardless of setup
quality — larger multiples come from a further, external-liquidity
target (here, TP2). Requiring 2.0 from a target chosen specifically for
being close was asking it to do a job it wasn't designed for, and this
codebase's own docstring (`decision/trade_decision_generator.py`, prior
to this decision) already flagged "stop-to-TP1 or blended R:R" as the
two open candidate interpretations — this was never silently assumed
settled.

**Decision:** The gate now computes a BLENDED R:R —
`(tp1_allocation_pct/100) * RR(TP1) + ((100-tp1_allocation_pct)/100) * RR(TP2)`
— using the SAME `tp1_allocation_pct` split already governing position
sizing, not a new invented weighting. `minimum_rr = 2.0` is UNCHANGED —
only what it's measured against changed. This is deliberately the more
conservative fix: rather than lowering the bar to match TP1's
structural ceiling, the bar stays where it was and is now measured
against the trade's full, realistic expected outcome across both
targets.

**Explicit non-claim:** this is an informed engineering judgment call
made under authorization to use external research and professional
SMC/ICT trading conventions, cross-checked against this codebase's own
already-flagged ambiguity — it is NOT an empirically backtested,
optimized, or validated threshold. Whether 2.0-blended is the "right"
number for this strategy's edge can only be answered by the actual
backtest results, not asserted in advance. If real results suggest a
different number, that is future work grounded in evidence, not
something to guess at again now.

**Basis:** `strategy/targets/target_selector.py` docstring (TP1 =
nearest internal, TP2 = nearest external, both already "LOCKED" prior
decisions this does not touch); `decision/trade_decision_generator.py`'s
own pre-existing "stop-to-TP1 or blended R:R" flag; external SMC/ICT
research on typical near-target vs far-target R:R ranges; the existing
`tp1_allocation_pct` (30/70) split reused as-is for weighting.

**Not touched by this decision:** target selection itself (TP1/TP2
definitions), the 30/70 allocation split's own value, HTF context
validity gate, score threshold gate, risk engine, position sizing,
execution, any entry/exit/setup-lifecycle rule.

---

## PERF-01 — Incremental M5 strategy-layer computation

**STATUS: IMPLEMENTED, verified by equivalence test**

**Context:** A real production run measured per-M5-step cost climbing
from ~100ms early on to ~52 SECONDS by step 4,750 (5.6% into a one-year
replay) and still rising — extrapolating to weeks-to-months for a full
year. Traced to source: `backtest/pipeline.py`'s step() recomputed
swings, liquidity pools, sweeps, displacement, MSS, FVGs, and order
blocks from scratch, on the ENTIRE growing M5 candle history, every
single step. `map_equal_level_pools` alone is O(n^2) (nested loop) to
O(n^3) (an O(n) `.index()` call inside that loop) per call on its own.

**Fix:** `market_data/replay/incremental_m5_layer.py` — each detector's
output is cached and only extended using new candles each step, not
recomputed from scratch. Full reasoning, including two genuine
correctness subtleties found and fixed via the fix's own equivalence
test (swing major-flag confirmation can resolve arbitrarily far in the
future; sweep detection reports only the first-ever qualifying candle
per pool, so a naive tail-rescan can produce spurious duplicate or
stale results) is documented in that module directly.

**Two disclosed, deliberate simplifications** (not silent
approximations — see the module's own docstring for full reasoning):
1. Equal-level pool matching is bounded to the most recent
   `equal_level_lookback_candles` (default 300, ~1.75 trading days) —
   its matching has no natural settling bound, unlike everything else,
   so an unbounded version would reintroduce real per-step growth.
2. Unswept stable pools and unresolved swing major-flag checks are
   given up on after `max_unswept_age_candles`/`max_pending_age_candles`
   (5000/3000 candles respectively, ~17/10 trading days) — most never
   resolve at all, and rechecking them forever reintroduces growth too.

**Verified:** `tests/test_incremental_m5_layer.py` — exact ID-level and
field-level equivalence (swings including major_flag/major_confirmed_at,
pools, sweeps, displacements, MSS events, FVGs, order blocks) against a
full from-scratch recompute at multiple checkpoints through a
multi-thousand-candle synthetic series designed to exercise a
genuinely-delayed swing confirmation, invalidations, sweeps, and MSS
events — not just a quick smoke check. 493/493 total tests pass with
this wired into the real `ReplayPipeline` construction.

**Measured impact:** real `ReplayPipeline.step()` benchmark over 1,000
steps of realistic synthetic M5 data held flat at ~90ms/step (vs. the
pre-fix trajectory's climb to 52,000ms/step and rising by a comparable
point in a real run) — full sandbox time constraints prevented running
the complete equivalence/benchmark suite to 85,000 steps directly, so
this is a strong, real, but partial measurement, not a claimed
full-year proof.

**Not touched:** any strategy rule, threshold, or detection algorithm's
actual logic — every detector's own decision-making code is called
unmodified; only how much data it's asked to re-process each step
changed. H4-side swing detection is untouched (small candle count all
year, never the bottleneck).

---

## STOP-ANCHOR-01 — Structural stop-loss anchored to swept liquidity level

**STATUS: RESOLVED (this entry created as part of implementing the fix
— no prior STOP-ANCHOR-01 entry existed in this file before this work;
a request that referenced one as already-present was inaccurate on that
specific point, see below)**

**Context:** Real backtest diagnostics (research_round_3_diagnostics)
showed confirmed setups' stop distances routinely 10-40x wider than
either take-profit target (e.g. stop=23.2pts vs TP1=0.5pts,
TP2=1.65pts). Traced to `invalidation_level = zone.lower / zone.upper`
— the stop was anchored to the far edge of the FVG/Order Block zone,
which can be very wide precisely when the displacement that formed it
was strong (the same displacement a valid setup requires).

**A proposed fix arrived via another model**, citing "docs/DECISIONS.md's
STOP-ANCHOR-01 entry, already RESOLVED" and "Specification §8, rule
T11." Before implementing anything, this was independently checked:
- The claimed pre-existing DECISIONS.md entry did **not** exist —
  verified directly, this file had no STOP-ANCHOR-01 section.
- No spec PDF was attached anywhere in the project at that point —
  verified by searching the full uploaded project archive.
- The user then supplied the actual
  `SMC_Trading_Engine_Specification.pdf`. Verified directly against
  it (extracted full text, grepped for the cited rule numbers):
  **§8.1-8.2 (p.19) do state exactly the formula proposed** — stop
  anchored to "the relevant swept low/high," buffered by
  `0.10 x ATR(14) of the execution timeframe`, with
  `Bullish SL = structural low - buffer` / `Bearish SL = structural
  high + buffer`. T11 (p. ~99 in the rules-lock section) does
  correctly reference this as "Phase 1 §8" governing the SL's
  structural anchor.

So: the specific claim about this project's own decision file was
false, but the substantive technical claim about what the frozen
specification says was accurate once checked against the real
document. Both were verified independently rather than either being
taken on trust — this entry exists because the check confirmed the
underlying fix, not because the citation was blindly followed.

**Decision:** `invalidation_level` is now:

    pool = the LiquidityPool referenced by the setup's originating
           MSSEvent.sweep_id -> SweepEvent.liquidity_pool_id
    buffer = initial_sl_buffer_atr (0.10, PROVISIONAL, already existed
             in core/config/registry.py, previously unused anywhere)
             x ATR(14) of the M5 execution timeframe
    invalidation_level = pool.price - buffer   [BULLISH]
    invalidation_level = pool.price + buffer   [BEARISH]

Reached via the sweep_id -> SweepEvent -> LiquidityPool chain already
produced the same step by `detect_mss(sweeps, displacements)` — no new
detection logic invented. A defensive fallback to the old zone-edge
value exists only for the case where that chain can't be resolved
(should not occur in practice, since mss.sweep_id is always produced
from the same sweeps list being searched).

**Explicitly NOT touched by this decision** (scoped fix, not a
redesign): TP1/TP2 target selection, the RR gate (RR-01, still blended
TP1/TP2, unchanged), `minimum_rr`, `tp1_allocation_pct`, scoring, risk
sizing, HTF validity gate, setup admission, any other config value.

**Explicitly NOT yet implemented** — found while reading §8 but out of
this task's scope, flagged for separate future work: §8.3 ("What if the
stop is too large? Skip the trade.") — a maximum-stop-distance gate that
should reject a setup outright rather than take it with a wide stop —
and §8.4 (minimum stop distance floor, `max(broker minimum, 0.25 x
ATR)`). Neither exists anywhere in the current codebase (confirmed:
`minimum_stop_atr` exists in config but is explicitly commented
"unused" where referenced). Given the diagnostic finding that motivated
this whole investigation was specifically about very wide stops, §8.3
in particular may turn out to matter — but implementing a new gate was
explicitly out of scope for this task and is not done here.

**Important, explicitly stated expectation-management note (per the
requesting instruction, preserved here verbatim in spirit):** this
fixes a confirmed implementation defect — the stop was anchored to the
wrong thing relative to the frozen specification. It does **not**
guarantee trades will start appearing. The swept-liquidity stop could
be tighter, wider, or similar to the old zone-edge stop depending on
the setup; that is an open, empirical question the next diagnostic run
answers, not something to assume in either direction.

---

## COST-01 — Realistic cost model: spread, slippage, commission

**STATUS: RESOLVED (Phase 1 implementation)**

**Context:** The first validated backtest run (`runs/validation_2025_09_v3`)
used `cost_model=FRICTIONLESS` throughout — fills at exact intended entry
price, zero spread/slippage/commission. This is appropriate for initial
signal validation but insufficient before trusting results for demo/live
account decisions.

**Decision:** A second, opt-in cost model (`REALISTIC`) has been added to
`backtest/execution_adapter.py`. It applies the following adjustments:

- **Spread**: Long entry fills at `intended_entry_price + spread`.
  Bearish SL/TP candle checks adjusted by spread on the ask side.
- **Slippage**: Adverse one-way slippage on entry and SL exits only.
  TP fills not slipped (limit orders assumed at-or-better).
- **Commission**: Round-turn = `commission_per_lot * volume * 2`.
  Deducted from `net_pnl` and converted to R units before subtracting
  from `realized_r`.

**CRITICAL — synthetic placeholder values only:** No real broker's
spread/commission/slippage numbers are hardcoded anywhere. The CLI args
`--spread`, `--slippage`, `--commission-per-lot` default to `0` (REALISTIC
with all zeros behaves identically to FRICTIONLESS). This is deliberate.

> **Before using REALISTIC results for any live/demo account decisions,
> real XAUUSD broker specifications MUST be supplied. Typical informational
> ranges only — raw spread ~0.10–0.30 pts, ECN commission ~$3–$7/lot
> round-turn. Obtain actual values from your specific broker's documentation.**

**Not touched:** FRICTIONLESS mode is unchanged in tag, fill logic, and R
calculation. `TradeRecord` gains optional `position_size`, `dollar_risk`,
`gross_pnl`, `commission`, `net_pnl` fields (all default `None`).
Backward compatible — existing FRICTIONLESS records unaffected.

---

## TRAIL-01 — Trailing-stop stale-swing bug (walk_forward_6m_v3)

**STATUS: FIXED, verified by direct reproduction + regression tests**

**Context:** The `walk_forward_6m_v3` run (6-month walk-forward,
2025-09-01 to 2026-02-28, 61 closed trades) reported a profit factor of
88.73 and net +2487.9R. Manual inspection of individual trades (sorted
by `realized_r`) found 11 of 61 trades with economically impossible
price moves — e.g. a 9.9% XAUUSD move in 15 minutes, an 18.6% move in
20 minutes — all 11 exiting exclusively via `TRAILING_STOP_HIT`.

**Root cause:** `TrailingEngineImpl.compute_new_stop`
(`strategy/trailing/trailing_engine.py`) scans whatever candle sequence
it is given for confirmed MAJOR swings, with no check that a candidate
swing's price was still consistent with current market price.
`backtest/pipeline.py`'s `_manage_open_position` passed the FULL
replay-to-date candle history on every `POST_TP1` step. In a multi-month
replay this let a chronologically-earlier major swing — from a wholly
unrelated, much-earlier price regime (e.g. before a later pullback) —
surface as "more protective" purely because its raw price exceeded
`current_sl`, with nothing checking whether that price was already
behind live market action at all. That stale candidate could then be
returned as the new trailing stop even though it sat ABOVE current
market price, which the very next candle trivially breached, producing
an immediate, meaningless `TRAILING_STOP_HIT` at the stale level.
Confirmed by direct reproduction (`tests/test_trailing_stale_swing_regression.py`):
a synthetic scenario with an early, high-priced major swing followed by
a large pullback and recovery reliably produced a computed stop ABOVE
live price.

**Fix:** Two layers, deliberately redundant:

1. **The actual correctness guarantee**, inside
   `TrailingEngineImpl.compute_new_stop` itself: a candidate is now
   rejected outright if the current (most recent) candle in the
   supplied sequence would already breach it (for BULLISH:
   `candidate >= current_candle.low`; for BEARISH:
   `candidate <= current_candle.high`). This holds regardless of what
   candle window a caller passes in, and regardless of *why* a bad
   candidate arose — it is a direct plausibility invariant at the
   source, not a workaround for this one cause.
2. **A secondary, performance-motivated bound**, in
   `backtest/pipeline.py`'s `_manage_open_position`
   (`TRAILING_SWING_LOOKBACK_MARGIN = 20`): the candle window passed to
   `compute_new_stop` is now bounded to ~20 candles before the
   position's entry onward, rather than the full replay-to-date
   history. This also removes a real O(full-history) rescan cost paid
   on every `POST_TP1` step for long runs — a genuine, separate
   performance issue the PERF-05 through PERF-10 optimizations (see
   prior entries) did not touch.

**Verification:** Full test suite green (501 passed, up from 499 —
2 new tests added). `tests/test_trailing_stale_swing_regression.py`
covers both directions: (a) the invariant holds across zero, small, and
large amounts of intervening history (0 / 20 / 500 / 5,000 / 20,000
filler candles), and (b) the fix does not reject a genuinely legitimate
trailing candidate — reusing `test_backtest_pipeline.py`'s own
hand-verified `TestReplayPipelineHappyPath` fixture, which must still
produce its expected `2409.900` trailing stop unchanged.

**Explicit non-claim:** This fixes the mechanism that produced
`walk_forward_6m_v3`'s 11 impossible-R trades. It does NOT mean every
number in that run's `statistics_final.json` was otherwise trustworthy
— that run also predates the `RR-02` tuning change below, so it should
not be treated as a current baseline. A clean re-run on the fixed code
is required before trusting any trade-count or expectancy figure from
this strategy going forward.

**Not touched:** Setup detection, confirmation, HTF regime gating (the
latter was independently checked this session via direct exhaustive
unit test across all 8 regime/direction combinations and found to have
no leak — a prior session's manual event-log cross-reference had
incorrectly suggested one; that finding is retracted here for the
record), scoring, risk sizing, execution/fill logic, SL/TP1/TP2 exit
logic (only the trailing-stop-specific swing selection was affected).

---

## RR-02 — minimum_rr raised from 2.0 to 3.8 (first tuning pass)

**STATUS: PROVISIONAL — first deliberate tuning pass, not yet validated
against a clean re-run**

**Context:** Following TRAIL-01's fix, a clean re-read of
`walk_forward_6m_v3`'s trades with both known bugs' effects manually
excluded gave a baseline of 35 trades/6mo, 42.9% win rate, expectancy
+0.95R, profit factor 2.72 — a real, modest, believable edge, with
every strategy parameter (including `minimum_rr`) still at its original
PROVISIONAL, never-tuned value. This is the first deliberate tuning
decision made against that baseline understanding.

**Decision:** Raise `minimum_rr` (RR-01's LOCKED blended TP1/TP2
algorithm, value only) from 2.0 to 3.8, with 3.5 as an explicitly
agreed fallback if 3.8 leaves too few trades over a 6-month window to
be statistically meaningful (rough floor: ~15-20 clean trades).

**Expected tradeoff (stated in advance, per this project's own
discipline of not retroactively excusing a result after seeing it):**
fewer total trades, a higher average win size, and likely a LOWER win
rate — high-RR setups are structurally harder to close as winners
before a stop triggers. This is a deliberate quality-over-volume
choice, not a free improvement. If the resulting numbers don't bear
this out as expected, that itself is worth investigating rather than
dismissing.

**Required next step before this is anything more than provisional:**
a fresh `walk_forward_6m` run on the TRAIL-01-fixed code, confirming
both plausibility checks introduced in TRAIL-01 pass clean, before any
headline statistic from it is trusted or reported.

**Not touched:** `max_stop_atr`, `minimum_stop_atr`, `min_target_atr`,
`tp1_allocation_pct`, `minimum_score`, the RR-01 blended-measurement
algorithm itself (only its threshold changed), HTF context gate, risk
engine, position sizing.

---
## PHASE-A (2026-10-03) — correctness/causality rebuild. See CHANGELOG_PHASE_A.md.
Owner decisions: Phase A before Phase B; consecutive-loss lock + daily counters reset 00:00 UTC;
FIXED_RISK default / COMPOUNDING selectable; minimum stop 0.25 ATR enforced by rejection (never
clamped); registry is the single config source (RR 3.8); no synthetic ticks as validation;
timing params (touch timeout, sweep->displacement age) configurable but DISABLED; no tuning.
