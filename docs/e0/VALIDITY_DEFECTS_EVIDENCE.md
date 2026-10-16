# Suspected validity defects V1–V4: evidence, classification, minimal failing tests

Nothing here changes strategy behavior. The four test files live in `tests_validity/` (outside the default `tests/` path) and assert the **desired** behavior, so the defect assertions are expected to FAIL on the current code. Each file also contains **control** tests that pass today, proving the failing tests discriminate and guarding against over-fixing.

Run: `pytest tests_validity -q` (expected on Phase-A-v2/E0: 11 failures, 16 passes).
Evidence base: the frozen October 2025 REFERENCE run (event timestamps, ids and payloads only; no outcomes) and deterministic synthetic fixtures (software tests, not market evidence).

**Legend.** *Proven defect* = violates an invariant that does not depend on strategy intent. *Policy* = a choice the owner must make; the tests are written so that every reasonable policy passes.

---

## V1 — H4 structure events re-applied after a later reversal
Test: `tests_validity/test_validity_v1_h4_reapplication.py`

| | |
|---|---|
| **Classification** | **Proven correctness defect.** The policy question (what a flip should mean) is not involved. |
| **Why proven** | `StructureDetectorImpl.detect()` classifies every break relative to ONE caller-supplied `prevailing_direction` for the whole history and embeds the BOS/CHoCH type in the event id (`bos_choch_detector.py:30-72`; its docstring says it does not reconstruct history). `_recompute_h4_layer` (`pipeline.py` ~586-621) passes the *current* direction every step and applies every not-yet-emitted id. After a flip, old breaks are reclassified (new ids) and applied again, now. The state machine therefore consumes events dated before ones it already consumed. |
| **Evidence (frozen October)** | Three reversals to BEARISH (event dates Oct 21 16:00, Oct 27 16:00, Oct 28 08:00). Each is followed, in the same replay step, by transitions dated September 5–29 that end BULLISH (events.jsonl lines 39226-39231, 46292-46297, 47351-47357). End-of-step state was BULLISH or BEARISH_WARNING in every sample; BEARISH 0. 36 of the 59 HTF rejections occur after the first overwritten reversal. Caveat: silent warning-expiry emits no event, so a state reconstruction from events can miss silent restores. |
| **Minimal failing test** | 42 synthetic H4 candles. At step Jan 17 08:00 a reversal to BEARISH is applied, then two bullish CHoCH events dated **Jan 13 00:00 and Jan 14 20:00** are applied; the step ends BULLISH. |
| **Current failures** | (1) events applied out of time order; (2) the reversal step does not end BEARISH; (3) in a 22-candle series one break is applied twice (CHoCH, then re-classified as BOS). |
| **Passing controls** | a no-flip series applies events in order; the series does contain a reversal. |
| **Proposed fix (not implemented)** | Watermark: apply a structure event only if `(confirmed_at, event_id)` is later than the last applied one. Every event that was ever new is still applied; reclassified history is ignored. Re-deriving the prevailing direction per event is larger and a design choice. |

## V2 — Confirmations that predate setup eligibility
Test: `tests_validity/test_validity_v2_confirmation_predates_eligibility.py`

| | |
|---|---|
| **Classification** | **Proven correctness defect** (execution realism). One **policy** sub-question: what to do with such an opportunity (discard it, or require a *new* break after construction). Both pass the test. |
| **Why proven** | The Phase-A boundary is `setup.created_at`, a *logical* market time (`_process_touches` ~828-846). The step that actually constructs the setup can be hours later. The break search replays candles since `created_at`, so a break candle that closed before construction is accepted and the order is priced at that old close. No real system can do that. |
| **Evidence** | In the frozen run, **95 of 294** decided candidates (32%) were confirmed by a break candle that closed before `SETUP_CREATED`. Construction lag from zone creation: median 2.2 h (71% > 1 h, 19% > 6 h). `TOUCH_EVENT.timestamp` equals the break candle close (`confirmation_engine.py:113`). In a 3-day synthetic run, 5 of 9 confirmations were retroactive. |
| **Minimal failing test** | The setup first appears two steps after the break candle (injected, the mechanism every existing pipeline test uses). |
| **Current failures** | confirmation uses a pre-existence break candle; a position opens at a pre-existence price; the confirmation event is dated after the candle it trades. |
| **Passing control** | a setup present from the start confirms at the break step. |
| **Proposed fix** | Record `constructed_at` (default: the first step a setup is processed) and use `available_at = max(created_at, constructed_at)` in `process_touch`. |

## V3 — Confirmed setups decided at stale prices / retried indefinitely
Test: `tests_validity/test_validity_v3_stale_decisions.py`

| | |
|---|---|
| **Classification** | The **stale price is a proven correctness defect**. What to do with the opportunity is **policy** (below). |
| **Why proven** | `_attempt_decision` prices at `break_candle.close` with `decision_timestamp = break close`, but can run in a later step: targets missing returns `# retried next step`; a wrong-side stop returns silently; and `_attempt_decisions` stops while a position is open. CONFIRMED setups are exempt from expiry/invalidation, so they can wait forever. |
| **Evidence** | Frozen October: 48 of 294 decisions came after their confirmation step (p90 0.83 h, max 109 h); 5 setups stayed CONFIRMED with no decision. October never had an open position, so the open-position path is **not exercised by the frozen run**; it is proven only by the synthetic test below. |
| **Minimal failing tests** | (a) targets unavailable for 3 calls, then available: the position is decided 3 steps after confirmation at the old price; (b) targets never available: the setup stays CONFIRMED to the end; (c) a second confirmed setup parked behind an open position opens at step 01:20 using the price of a candle that closed at 00:30. |
| **Current failures** | 4 (a: two assertions; b; c). |
| **Passing controls / guards** | same-step trading works; the **one-position rule itself** (never two positions) holds; a guard proves fixture (c) really exercises the deferred path. |

### V3 — what happens when a position is already open (explicit definition)
The one-position rule is **not changed**. Only deferral at a past price is removed. A setup confirmed at a break step while a position is open must end in one of these, always logged explicitly:

| Option | Behavior | Status |
|---|---|---|
| **A (proposed minimal)** | Terminal outcome `SKIPPED_ONE_POSITION_RULE` at the break step. Logged as its own funnel category (not a strategy-quality rejection, not counted in rejection-reason tables). | Preserves baseline semantics: no second position; the opportunity is simply not taken. |
| B | Re-evaluate at a later candle with current data, requiring a **new** break. | New policy; changes the opportunity set. Needs approval. |
| C | Queue with expiry; price the order at the current step. | New policy; needs approval and a realistic fill model. |

The tests accept A, B or C. The frozen run cannot measure the cost of choosing A (it had no positions); after V3 the candidate ledger records `parked_steps` and the skip per setup, so that cost becomes a measurable diagnostic **before** any decision about B or C.

Note: `tests/test_backtest_pipeline.py::TestOneOpenPositionRule::test_second_confirmed_setup_cannot_open_while_position_active` currently asserts that the parked setup stays `CONFIRMED`. That assertion encodes parking. Its "no second trade while one is open" half stays; the "remains CONFIRMED" half must be revisited when V3 is approved.

Also under V3 (proven): missing targets at the break step → terminal `NO_TARGET_PAIR`; wrong-side stop → terminal `STOP_ON_WRONG_SIDE_OF_ENTRY`; no CONFIRMED setup may stay undecided after its break step.

## V4 — Multiple candidates representing the same event
Test: `tests_validity/test_validity_v4_duplicate_events.py`

| | |
|---|---|
| **Classification** | Duplication is a **proven defect**. The **merge criterion** is a **policy** choice; the proposed one adds no new parameter. The root cause (pool identity drifting with future volatility) is a separate defect, proposed as its own change "V5". |
| **Mechanism** | OB zones link per MSS and the MSS id embeds the swept pool id. Equal-level pools are re-mapped every step with a tolerance taken from the *latest* ATR (`pipeline.py` ~466-476; `liquidity_engine.py:134`), so one cluster yields pools with different average prices and ids. FVG zones link by displacement and are already deduplicated by the zone-id marker; only OB setups multiply. |
| **Evidence (frozen October, ids)** | 9,153 created setups collapse to 2,769 pool-free groups; 1,501 have more than one variant (7,885 variants). The largest October event had 13 variants constructed 16:20–20:40 for one sweep (14:55) and displacement (15:00). Of 290 parsed decided candidates there are 193 distinct events. |
| **Minimal failing test** | 3 pool variants of one swept level (prices within 0.09 points) produce 3 setups; should be 1. |
| **Current failures** | 1. |

### V4 — proof that the proposed key does not merge genuinely distinct opportunities
Proposed key: `(direction, zone_type, zone upper/lower, sweep candle id, displacement id, pool type, pool-price cluster)`. Variants merge only when pool prices lie within the **existing equal-level tolerance** (0.05 ATR, min 2 ticks) of the cluster's first member (anchor rule, so spread never exceeds the tolerance).

1. **By construction** the key contains everything that defines the entry zone (type, bounds, displacement), the trigger context (sweep candle), the direction, the kind of level (pool type) and the invalidation level (price band). Two members of one key share all of them.
2. **Empirical, frozen October:** zone bounds are identical within **all 179** groups that carry zone data (0 groups with different bounds). Anchor-rule results: tolerance 0.05 ATR turns 290 candidates into **215** opportunities, with max spread to anchor 0.048 ATR; for context, 0.10 → 209 and 0.25 → 200; a naive pool-free key gives 193.
3. **Why the naive key is rejected:** it would merge pool prices up to **0.444 ATR** apart (5 of the 56 in-window multi-variant groups exceed 0.25 ATR). Those are probably different levels with different stops. The tolerance band, which is the strategy's own definition of "the same level", keeps them apart: 21 groups split into more than one opportunity.
4. **Unit tests that pass today and must keep passing after V4** (8 controls): pools further apart than the tolerance; different sweep candle; different displacement; different pool type at the same price; different zone geometry; opposite directions; FVG vs OB for the same displacement; an FVG shared by variants already created once.
5. **Residual risk (stated, not hidden):** opportunity identity now depends on the tolerance; merged members' stops differ by at most the tolerance by definition. Which variant is kept (proposal: earliest constructed, ties → lowest pool id) is immaterial within that band but must be deterministic and logged. Before approving, compare candidate ledgers before and after V4 on the same input: every merged candidate must map to exactly one kept candidate with identical zone bounds.
