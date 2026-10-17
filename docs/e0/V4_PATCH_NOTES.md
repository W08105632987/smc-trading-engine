# V4 patch notes -- event-level de-duplication of setup candidates

Base: E0 (Phase-A-v2 + telemetry; Gate 0 exact regression PASSED). One defect only: V4. No threshold, stop, target, scoring,
admission, sizing, risk, cost or config change. Config hash and parameter-path list must be unchanged.

## What changed
| File | Change |
|---|---|
| `backtest/event_dedup.py` (new) | pure logic: event key, anchor registry, `plan_step` |
| `backtest/pipeline.py` | import; `ReplayState.event_dedup`; `_plan_event_dedup` (read-only pre-pass); skip + anchor registration inside `_construct_setup_candidates` |
| `backtest/telemetry.py` | `on_dedup_skip` hook, `v4_merge_map.jsonl`, manifest fields `v4_merge_rows`, `v4_bounds_mismatch`, `v4_unmapped_to_kept` |
| `scripts/v4_merge_mapping.py` (new) | offline replay of the rule over an E0 `candidates.jsonl` (no engine run) |
| `tests/test_v4_event_dedup.py` (new) | pure-logic + pipeline-behaviour tests |

`tests_validity/test_validity_v4_duplicate_events.py` is NOT modified. Its defect test must flip red -> green; its 8 controls must stay green.

## Rule (as approved provisionally)
* Key = (direction, zone type, zone upper, zone lower, sweep candle id, displacement id, pool type). Pool price is NOT in the key.
* Inside one key, pool prices are clustered with the strategy's existing equal-level tolerance: max(2 ticks, `equal_level_tolerance_atr` x ATR at the anchor's setup time). No new parameter.
* Anchor rule: a variant merges only if |price - anchor price| <= the anchor's tolerance (no chaining).
* Keep rule: earliest constructed wins; within one step ties -> lowest pool id (independent of detector emission order).

## Decisions made while writing the patch (flag for owner approval; each is deterministic and logged)
1. **Registration at construction, not at admission.** An event is 'seen' once its first variant is constructed, even if that variant was not admitted (3-slot cap) or later dies. A later pool-id variant of the same event is never re-created. Alternative (register only when admitted) would let a re-rolled variant re-enter admission after the first was refused.
2. **Earliest constructed wins even if a later variant scores higher.** Score depends on H4 state/pools at construction time. Offline replay on the E0 ledger: 19 of 10,725 merged candidates had a higher score than the kept one. Reported, not changed.
3. **Tolerance uses the ATR at the anchor's setup creation time** (same ATR lookup `_build_setup_candidate` already uses), stored with the anchor. If that ATR is missing the tolerance is 2 ticks.
4. **FVG zones**: unchanged in practice (already one setup per displacement via the zone-id marker); the pre-pass mirrors the construction loop's first-occurrence-per-zone rule.
5. **Merged candidates emit no ZONE_CREATED / MSS_CONFIRMED / SETUP_CREATED events** (they are never constructed). `events.jsonl` therefore changes by design; every removed event must be explained by `v4_merge_map.jsonl`.

## Offline evidence (E0 October ledger, 15,740 candidates; first-order estimate only)
Kept 5,015, merged 10,725; every merged candidate maps to exactly one kept candidate; 0 merged with different zone bounds; max price delta = 0.9998 x tolerance; largest group 66. (The naive pool-free key gives about 3,750 events but merges levels up to 0.44 ATR apart and is rejected.)
The real run's `v4_merge_map.jsonl` is authoritative; downstream admission changes (fewer competitors for the 3 slots) can shift which candidates are kept.

## Required per-patch report (from PATCH_SEQUENCE_PROPOSAL.md)
1. Real pytest (default suite + `tests_validity`): counts, red->green flips.
2. Differential changes vs E0, each attributed to V4.
3. Accounting/risk invariants unchanged (`test_backtest_accounting`, `test_phase_a_correctness`, `test_strategy_risk_engine`).
4. Config hash + parameter-path list unchanged.
5. Funnel A -> B transition matrix (`e0_funnel_report.py`), diagnostics only.
6. Unresolved issues. Any unexplained difference stops the sequence.
