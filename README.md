# SMC Trading Engine (Phase-A-v2 / E0 Baseline)

Institutional Smart Money Concepts (SMC) quantitative trading engine for Gold (XAUUSD), featuring deterministic tick-level replay backtesting, multi-timeframe market structure analysis, and non-invasive E0 telemetry instrumentation.

---

## Project Status & Lineage

| Attribute | State |
|---|---|
| **Lineage** | Milestone 1 (Foundation) &rarr; Phase-A-v1 &rarr; Phase-A-v2 &rarr; **E0 Instrumentation** |
| **Current Baseline** | Frozen October 2025 REFERENCE baseline (`runs/probe_oct2025_reference/`) |
| **Gate 0 Regression** | **PASS** &mdash; exact regression parity confirmed (`runs/probe_oct2025_E0/`) |
| **Effective Config Hash** | `ecbfc8a5dc92fc7e25af485be3a9f34e44fa588629419276115800b9757fa9fe` |
| **Python Target** | Python 3.11.x (managed via `uv`) |

> [!WARNING]
> ### Critical Notice on Baseline Status
> The October 2025 frozen baseline produced **0 completed trades** ($10,000 initial balance unchanged, 53,357 events processed).
> **The Gate 0 regression PASS confirms deterministic execution and non-invasive telemetry instrumentation; it is NOT evidence of trading profitability.**
> See [`docs/GATE0_EVIDENCE.md`](docs/GATE0_EVIDENCE.md) for full audit reports and candidate autopsy analyses.

---

## Architectural Overview

The engine is structured into modular domain layers designed for zero temporal leakage and strict separation of concerns:

```
smc-trading-engine/
├── core/                  # Domain primitives, accounting models, event broker, configuration
├── market_data/           # Tick streaming, multi-timeframe aggregators (M1/M5/M15/H1/H4/D1), gap detection
├── strategy/              # Institutional SMC detection and filtering engines
│   ├── swings/            # Fractal swing high/low identification
│   ├── zones/             # Order Block (OB) and Fair Value Gap (FVG) extraction
│   ├── structure/         # Break of Structure (BOS) & Change of Character (CHOCH), H4 warnings
│   ├── displacement/      # High-momentum candle and displacement qualification
│   ├── liquidity/         # Buy-side & sell-side liquidity sweep detection
│   ├── confirmation/      # Multi-timeframe confirmation & entry trigger logic
│   ├── scoring/           # Confluence scoring engine (minimum score: 70)
│   ├── setups/            # Setup candidate lifecycle & admission control
│   ├── risk/              # Position sizing (0.5% risk/trade) and stop-loss boundary checks
│   ├── targets/           # Dual-target geometry (TP1 30% / TP2 runner 70%, min R:R 3.8)
│   └── trailing/          # Dynamic structural trailing stop engine
├── decision/              # Trade decision generator & candidate qualification pipeline
├── execution/             # Order state machines, idempotency tracking, broker protection
├── brokers/               # Broker profile definitions, fee/spread simulation, account reconciliation
├── backtest/              # Deterministic event-driven tick simulation engine & statistics
├── tests/                 # Comprehensive unit, integration, and Monte Carlo test suites (58 modules)
├── tests_validity/        # Known defect harness (V1-V4 asserted defects)
├── tools/e0_regression/   # Fast-path tick generation & cross-run verification tools
├── scripts/               # Operational entrypoints (backtest runner, regression comparator, audits)
└── runs/                  # Preserved baseline outputs, diagnostic autopsies, and E0 telemetry
```

---

## E0 Instrumentation Layer

The **E0 instrumentation** was introduced to diagnose upstream trade filtering bottlenecks without altering strategy decisions or corrupting baseline determinism:

* **Shadow Gating**: Evaluates rejection reasons across candidate life stages without affecting execution.
* **Funnel Telemetry**: Records event throughput across candidate admission stages in `funnel_counters.json`.
* **Candidate Autopsy Tracing**: Logs candidate lifecycle details in `candidates.jsonl` and diagnostic CSVs.
* **H4 Transition Logging**: Captures structural regime shifts in `h4_transitions.jsonl`.

---

## Verification & Reproducibility

### 1. Environment Setup

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync dependencies using Python 3.11
uv sync --python 3.11 --extra dev
```

### 2. Run Test Suite

```bash
# Run all unit and integration tests
uv run pytest tests/ -q
```

### 3. Run Validity Suite (V1-V4 Defect Assertions)

The repository includes a dedicated validity harness (`tests_validity/`) documenting four known defects in the frozen baseline:
* **V1**: H4 structural re-application
* **V2**: Confirmation predating setup eligibility
* **V3**: Stale decision persistence
* **V4**: Duplicate event emissions

These tests are expected to assert known baseline defects:
```bash
uv run pytest tests_validity/ -v
```

### 4. Gate 0 Regression Comparison

To verify that local execution exactly reproduces the frozen baseline without bit-level drift:

```bash
uv run python scripts/e0_regression_compare.py \
    --reference runs/probe_oct2025_reference \
    --candidate runs/probe_oct2025_E0
```

---

## Evidence & Diagnostic Artifacts

Committed evidence and autopsies are located in `runs/`:
* `runs/probe_oct2025_reference/`: Reference baseline reports, plausibility checks, and Diagnostic 01/02 autopsy suites.
* `runs/probe_oct2025_E0/`: E0 telemetry run metadata, funnel counters, H4 transitions, and manifest.
* Full details and SHA-256 signatures are documented in [`docs/GATE0_EVIDENCE.md`](docs/GATE0_EVIDENCE.md).
