# SMC Trading Engine — Milestone 1 (VERIFIED)

Foundation layer: `core/domain`, `core/contracts`, `core/config`, `core/events`,
`core/state`, plus `news/` architecture scaffolding (not yet wired into the
pipeline). 116 tests, verified passing on Windows / Python 3.11.9 / uv 0.12.3.

## Reproduce from a clean machine

1. Install Python 3.11.x (this project targets 3.11 specifically — not 3.12/3.13).
2. Install `uv`: https://docs.astral.sh/uv/getting-started/installation/
3. Extract this archive, open a terminal in the `smc-trading-engine` folder.
4. `uv sync --python 3.11 --extra dev`
5. `uv run pytest -q`

Expected result: `116 passed`.

## Status

- Strategy FROZEN v1.0, Execution FROZEN v1.0, Implementation Specification
  FROZEN v1.0, Technical Architecture FROZEN v1.0 — all unchanged.
- One deliberately unresolved decision: `NewsEvent.is_knowable_at()` (see
  `news/README.md`) — raises `NotImplementedError` on purpose rather than
  guessing between `created_at` and `scheduled_at` as the availability basis.
- `strategy/`, `execution/`, `brokers/` do not exist yet — scheduled for
  later milestones per the Technical Architecture build order.

Milestone 2 is not authorized to begin until this archive is reviewed.
