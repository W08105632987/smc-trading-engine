# news/ — Status

**This module is architecture-only. It is not wired into the strategy or
execution pipeline yet**, per News Awareness Architecture Amendment v1.0
§16 ("continue completing the current milestone... schedule the full
News Awareness module according to the existing build order").

## What exists
- `domain/news_event.py` — `NewsEvent`, `NewsImpact` (provider-normalized representation)
- `domain/news_window.py` — `NewsWindow`, `NewsBlockReason` (shape only, no durations)
- `contracts/news_provider.py` — `NewsProvider` Protocol (no concrete implementation)
- `filtering/config.py` — `NewsMode`, `NewsFilterConfig` (no default blackout values — see below)
- `providers/`, `service/` — empty, scheduled for a later milestone

## What does NOT exist yet (intentionally)
- Any concrete `NewsProvider` implementation or external API credentials
- Any actual `minimum_impact` / `minutes_before` / `minutes_after` values —
  `NewsFilterConfig` raises a validation error if `FILTER_ONLY`/`BLACKOUT`
  mode is selected without these being explicitly set; there is no
  fallback default, by design (amendment §6)
- Any integration point in `strategy/` or `execution/` — those modules
  remain completely unaware of news, per amendment §5

## One implementation decision — now RESOLVED

`NewsEvent.is_knowable_at(at)` is **LOCKED**: `at >= created_at`.

`created_at` is the timestamp at which the scheduled event became
available to the engine/calendar; `scheduled_at` remains only the release
timestamp and the anchor for blackout-window calculations (`NewsWindow`).
This isn't an arbitrary pick — a "before the event" blackout window is
only possible at all if the event's existence is knowable ahead of
`scheduled_at`; gating availability on `scheduled_at` instead would make
a "before" window require look-ahead by construction.

**Critical constraint carried forward to future milestones:** `created_at`
must come from the news provider's actual historical publication/calendar
metadata. Nothing in this engine may derive, infer, or substitute it from
`scheduled_at` (e.g. `created_at = scheduled_at - N days`) — that would
silently reintroduce a fixed advance-notice assumption this decision
explicitly avoided making. A provider adapter that cannot supply a
genuine `created_at` cannot be used for backtesting until it can.
