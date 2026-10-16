# Patch sequence (APPROVED with the conditions below — V1–V4 remain unimplemented until Gate 0 passes)

**Status:** E0 is implemented and passes the synthetic exact regression. The **October exact regression on your machine is pending** and is gate 0 for everything below. V1–V4 are *not* implemented; per your instruction they come only after that gate, one at a time.

## Approved decisions and conditions (recorded)
1. **Gate 0 first.** Exact October REFERENCE regression for E0 on the owner's machine (`GATE0_RUNBOOK.md`). No V1–V4 work until the comparator prints `PASS: exact regression`.
2. **Compute option B.** Synthetic differential tests and short REFERENCE windows for intermediate patches; full-month REFERENCE runs only for E0 and C1. No 12-month backtest.
3. **V2 approved:** discard retroactive opportunities (qualifying break before the setup was constructed and available).
4. **V3 Option A approved for the validity control:** terminal `SKIPPED_ONE_POSITION_RULE`, reported separately from strategy-quality rejections; missing targets and wrong-side stops also get explicit terminal outcomes.
5. **V4 approved provisionally:** existing equal-level tolerance, anchored to the first member, earliest-constructed wins (ties → lowest pool id). Acceptance requires all control tests **and a candidate-by-candidate merge mapping** (every merged candidate maps to exactly one kept candidate with identical zone bounds).
6. **V5 deferred** until after C1; its root-cause investigation stays a separate task.

Additional conditions:
* **V1 test set** (beyond the existing three): out-of-order historical events; duplicate application of one break; **legitimate reversals still confirm and persist**; **warning-expiry restores** (silent transitions, which have no event). After the patch the H4 transition log is **inspected by hand**; a watermark alone is not accepted as proof that the reconstructed H4 state is correct.
* **Real pytest** with actual dependencies, not only the stand-in, before and after every patch.
* C0 stays frozen; each version has its own branch/folder and run directory.
* No change to thresholds, stop placement, target selection, sizing, risk controls or costs during the validity sequence.
* No strategy loosening is recommended until the exact E0 regression passes and C1 is established.

### Per-patch report (required after every patch; stop on any unexplained difference)
1. Real-pytest results (default suite and `tests_validity`): counts, and which tests flipped red → green.
2. Differential changes versus the previous version (synthetic harness byte comparison; short REFERENCE window where applicable), each difference attributed to the patch.
3. Risk and accounting invariants (accounting, ledger, daily-loss, loss-lock, trades/day, sizing, partial-close tests) unchanged.
4. Config hash and parameter-path list unchanged.
5. Funnel changes from the candidate ledger (`e0_funnel_report.py` A → B transition matrix), as diagnostics only.
6. Unresolved issues and anything unexplained. **Any unexplained difference stops the sequence.**

## Gate 0 — E0 exact regression on the real data (yours to run)
Command and expected result are in `E0_IMPLEMENTATION_SUMMARY.md` §5. Pass criteria: `scripts/e0_regression_compare.py` reports `PASS`. If anything differs, stop: E0 is not observation-only and is fixed before anything else.

## Version and run naming
Each patch is a separate commit/branch and a separate run directory; the frozen October directory is never written to.

| Version | Contents | Run directory (October window, REFERENCE) |
|---|---|---|
| C0 | frozen Phase-A-v2 | `runs/probe_oct2025_reference` (exists, read-only) |
| E0 | C0 + telemetry | `runs/probe_oct2025_E0` |
| E0+V1 | + H4 watermark | `runs/probe_oct2025_V1` |
| E0+V1+V2 | + construction boundary | `runs/probe_oct2025_V2` |
| E0+V1+V2+V3 | + same-step decision | `runs/probe_oct2025_V3` |
| **C1** = E0+V1+V2+V3+V4 | + event dedup = the **validity control** | `runs/probe_oct2025_C1` |

**Compute cost (measured, not estimated):** one October REFERENCE run took 24.5 h. Five runs is about five days on one machine. Options for your decision: (a) run all five REFERENCE runs sequentially; (b) validate V1–V3 with the failing-test flips, the synthetic differential harness and a short REFERENCE window (for example Oct 20–23, where the H4 flips occur) and run the full month only for E0 and C1; (c) as an extra diagnostic only, run FAST (non-canonical) per version. I recommend (b). The 12-month REFERENCE run stays out of scope.

## Per-patch plan
Common acceptance for **every** patch: (1) its `tests_validity` file flips from red to green and its controls stay green; (2) the default suite stays green except the known wall-clock test; (3) the synthetic differential harness compared with the previous version shows differences **only** in the intended categories; (4) risk and accounting tests (`test_backtest_accounting`, `test_phase_a_correctness`, `test_strategy_risk_engine`) unchanged; (5) `e0_funnel_report A B` transition matrix is explained entirely by the patch; (6) shadow-gate mismatches stay 0; (7) all configuration parameters and the config hash unchanged unless explicitly approved.

### V1 — H4 watermark
* **Change:** `_recompute_h4_layer` applies a structure event only if `(confirmed_at, event_id)` is later than the last applied watermark (stored in `ReplayState`).
* **Files:** `backtest/pipeline.py` (`_recompute_h4_layer`), `ReplayState` (one new field).
* **Tests:** all of `test_validity_v1`; existing `test_strategy_h4_warning_engine`, `test_strategy_structure_detector`, `test_backtest_pipeline` unchanged.
* **Expected diagnostic change:** `h4_events_applied_out_of_time_order` → 0; HTF rejection counts will change (the 59 are computed against a suspect state). Reported as a diagnostic, not as improvement.
* **Risk:** a reversal can now persist as BEARISH, which changes which setups the HTF gate admits. That is the point of the fix, but it changes the opportunity set, so review the H4 transition log before approving.

### V2 — construction-time causality boundary
* **Change:** `Setup.constructed_at` (default: the first step the setup is processed, which keeps injected-setup tests valid); `available_at = max(created_at, constructed_at)` passed to `process_touch`.
* **Files:** `core/domain/setups.py`, `backtest/pipeline.py` (`_construct_setup_candidates`, `_process_touches`).
* **Policy to approve:** an opportunity whose break already happened is discarded (proposed) versus waiting for a new break; both satisfy the test; the proposal discards (no re-trigger logic).
* **Expected diagnostic change:** `confirmed_retroactively` → 0 (95 of 294 in October today).

### V3 — same-step decision, explicit open-position handling
* **Change:** a setup is decided only in the step of its own break candle. Missing targets → terminal `NO_TARGET_PAIR`; wrong-side stop → terminal `STOP_ON_WRONG_SIDE_OF_ENTRY`; confirmed while a position is open → terminal `SKIPPED_ONE_POSITION_RULE` (Option A in `VALIDITY_DEFECTS_EVIDENCE.md`). The one-position rule itself is unchanged.
* **Files:** `backtest/pipeline.py` (`_attempt_decision`, `_attempt_decisions`), `tests/test_backtest_pipeline.py` (the parked-`CONFIRMED` assertion is rewritten; documented in the change log).
* **Expected diagnostic change:** `decided_after_confirmation_step` → 0; `confirmed_never_decided` → 0; the new skip is its own funnel category and is **not** counted among strategy rejections.
* **Approval needed:** Option A versus B/C for open positions; whether a skipped-by-position outcome should be an event type or a payload reason (proposal: existing `TRADE_REJECTED` with `reason_code="SKIPPED_ONE_POSITION_RULE"` and `strategy_rejection=false`, so the event schema is unchanged).

### V4 — event-level deduplication
* **Change:** at construction, skip a candidate whose key `(direction, zone_type, zone bounds, sweep candle, displacement, pool type, pool-price cluster within the existing equal-level tolerance, anchored to the first member)` already has a setup; keep the earliest constructed (ties → lowest pool id); count the skip.
* **Files:** `backtest/pipeline.py` (`_construct_setup_candidates`).
* **Proof of non-merging:** `VALIDITY_DEFECTS_EVIDENCE.md` §V4; the 8 control tests must stay green.
* **Expected diagnostic change (October decided candidates):** 290 parsed → about 215 opportunities at the existing tolerance.
* **Not included:** the root cause (pool identity depending on future volatility). Proposed as a separate approved change "V5": freeze pool membership and tolerance at the sweep step. It changes strategy semantics and would also make an exact incremental engine possible, so it needs its own evidence and approval.

## After the validity control C1 exists (diagnostics only)
These use the E0 ledger on E0 versus C1 and report counts and geometry, never profitability, and never treat more trades as better:
1. **Stop geometry:** stop ATR, zone width, zone-to-entry, stop-inside-zone share, by version.
2. **Coordinated target pair (shadow):** how often the strict pair exists, the TP2 extension it requires, eligible-pool counts and pool types (types are now recorded; they were unknown before).
3. **Entry freshness:** construction lag, decision lag, entry run-away distance after V2/V3.
4. **RR thresholds:** the would-pass counts at 1.0/1.5/2.0/2.5/3.8 on geometry-valid candidates, with the independent gate matrix.
5. **Funnel comparison:** `e0_funnel_report E0 C1` transition matrix.

Only after these exist will I recommend the first strategy experiment. **That recommendation is deliberately not made now**: with the October ledger unavailable, any pick would rest on the same inference the redesign report already flagged. Selection rule I will apply then: choose the single-variable experiment whose diagnostic prerequisite shows the largest unexplained gap among **temporally valid** candidates, and that can be evaluated without changing the cost model, sizing or risk controls.

## Decisions requested
1. Run gate 0 and send the comparator output (or authorize me to proceed if you prefer to run it yourself first).
2. Approve V1–V4 order and the compute option (a/b/c).
3. Approve the V2 policy (discard retroactive opportunities) and the V3 open-position option (A proposed).
4. Approve the V4 merge criterion (existing equal-level tolerance; no new parameter) and the tie-break.
5. Say whether "V5" (causal pool snapshot) should be scoped now or after C1.
