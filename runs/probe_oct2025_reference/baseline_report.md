# Phase A baseline report

**Engine:** REFERENCE - CANONICAL (REFERENCE)  
**Config hash:** `ecbfc8a5dc92fc7e25af485be3a9f34e44fa588629419276115800b9757fa9fe`  
**Validation status:** NOT VALIDATED: Phase A baseline; no real-tick execution validation performed

## DATA
```json
{
  "dataset": {
    "path": "C:\\Users\\hp\\Downloads\\xauusd_working\\XAUUSD_ticks_2025-09-01_2026-08-31.csv",
    "name": "XAUUSD_ticks_2025-09-01_2026-08-31.csv",
    "size_bytes": 7003802861
  },
  "loaded_from": "2025-09-01T00:00:00+00:00",
  "window_start": "2025-10-01T00:00:00+00:00",
  "window_end": "2025-10-31T23:59:59+00:00",
  "m5_candles": 12174,
  "h4_candles": 275,
  "valid_ticks": 14881952,
  "invalid_rows": 0,
  "timezone": "UTC (reader treats Tickstory timestamps as UTC; broker-offset NOT independently verified)",
  "missing_data": "Gap statistics are NOT computed by this report (see KNOWN LIMITATIONS)."
}
```

## CONFIG
```json
{
  "config_hash": "ecbfc8a5dc92fc7e25af485be3a9f34e44fa588629419276115800b9757fa9fe",
  "engine": "REFERENCE",
  "engine_status": "CANONICAL (REFERENCE)",
  "research_path": "backtest.pipeline.ReplayPipeline (NOT the legacy backtest.engine)",
  "sizing_mode": "FIXED_RISK",
  "cost_model": "FRICTIONLESS",
  "cost_inputs": {
    "spread": "0",
    "slippage": "0",
    "commission_per_lot": "0"
  },
  "cli_overrides_applied": {},
  "effective_config": {
    "strategy": {
      "fractal_left": {
        "value": 2,
        "unit": "candles",
        "status": "LOCKED",
        "source_version": "phase2-v1",
        "phase3_testable": false
      },
      "fractal_right": {
        "value": 2,
        "unit": "candles",
        "status": "LOCKED",
        "source_version": "phase2-v1",
        "phase3_testable": false
      },
      "major_swing_atr": {
        "value": "1.0",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "equal_level_tolerance_atr": {
        "value": "0.05",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "sweep_min_penetration_atr": {
        "value": "0.05",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "sweep_max_penetration_atr": {
        "value": "0.50",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "sweep_reversal_window": {
        "value": 3,
        "unit": "candles",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "displacement_body_median_multiple": {
        "value": "1.5",
        "unit": "x median",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "displacement_range_atr": {
        "value": "1.2",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "premium_zone_pct": {
        "value": "52.5",
        "unit": "%",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "discount_zone_pct": {
        "value": "47.5",
        "unit": "%",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "micro_swing_min_retracement_atr": {
        "value": "0.1",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "v5-I1",
        "phase3_testable": true
      },
      "initial_sl_buffer_atr": {
        "value": "0.10",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "trailing_sl_buffer_atr": {
        "value": "0.10",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "v4-G",
        "phase3_testable": true
      },
      "minimum_stop_atr": {
        "value": "0.25",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "max_stop_atr": {
        "value": "3.5",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "fix-zero-trade",
        "phase3_testable": true
      },
      "min_target_atr": {
        "value": "1.0",
        "unit": "x ATR",
        "status": "PROVISIONAL",
        "source_version": "fix-zero-trade",
        "phase3_testable": true
      },
      "minimum_rr": {
        "value": "3.8",
        "unit": "ratio",
        "status": "PROVISIONAL",
        "source_version": "RR-02",
        "phase3_testable": true
      },
      "tp1_allocation_pct": {
        "value": "30",
        "unit": "%",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "minimum_score": {
        "value": 70,
        "unit": "points/100",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "choch_warning_max_candles": {
        "value": 20,
        "unit": "H4 candles",
        "status": "PROVISIONAL",
        "source_version": "v4-C",
        "phase3_testable": true
      },
      "setup_expiry_m15_candles": {
        "value": 48,
        "unit": "M15 candles",
        "status": "PROVISIONAL",
        "source_version": "v5-I8",
        "phase3_testable": true
      },
      "max_pending_setups": {
        "value": 3,
        "unit": "count",
        "status": "PROVISIONAL",
        "source_version": "v5-I6",
        "phase3_testable": true
      },
      "touch_confirmation_timeout_minutes": {
        "value": 0,
        "unit": "minutes (0 = disabled)",
        "status": "PROVISIONAL",
        "source_version": "phase-a-audit-5.1",
        "phase3_testable": true
      },
      "sweep_to_displacement_max_age_candles": {
        "value": 0,
        "unit": "M5 candles (0 = disabled)",
        "status": "PROVISIONAL",
        "source_version": "phase-a-audit-5.2",
        "phase3_testable": true
      },
      "scoring_weights": {
        "d1_h4_alignment": {
          "value": "15",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        },
        "h1_alignment": {
          "value": "10",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        },
        "premium_discount": {
          "value": "10",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        },
        "liquidity_sweep": {
          "value": "15",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        },
        "mss": {
          "value": "15",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        },
        "displacement": {
          "value": "10",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        },
        "fvg": {
          "value": "5",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        },
        "order_block": {
          "value": "5",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        },
        "ote": {
          "value": "5",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        },
        "session_alignment": {
          "value": "5",
          "unit": "points",
          "status": "DEFERRED",
          "source_version": "phase2-v1",
          "phase3_testable": false
        },
        "target_liquidity": {
          "value": "5",
          "unit": "points",
          "status": "PROVISIONAL",
          "source_version": "phase2-v1",
          "phase3_testable": true
        }
      }
    },
    "risk": {
      "risk_per_trade_pct": {
        "value": "0.5",
        "unit": "% equity",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "daily_loss_limit_pct": {
        "value": "2.0",
        "unit": "% equity",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "max_consecutive_losses": {
        "value": 3,
        "unit": "count",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "max_trades_per_day": {
        "value": 3,
        "unit": "count",
        "status": "PROVISIONAL",
        "source_version": "phase2-v1",
        "phase3_testable": true
      },
      "sizing_mode": {
        "value": "FIXED_RISK",
        "unit": "FIXED_RISK | COMPOUNDING",
        "status": "LOCKED",
        "source_version": "phase-a-P0.3",
        "phase3_testable": false
      },
      "reset_consecutive_losses_at_utc_midnight": {
        "value": true,
        "unit": "bool",
        "status": "LOCKED",
        "source_version": "phase-a-P0.3",
        "phase3_testable": false
      }
    },
    "execution": {
      "decision_expiry_seconds": {
        "value": 5,
        "unit": "seconds",
        "status": "PROVISIONAL",
        "source_version": "execution-v1-OD3",
        "phase3_testable": true
      },
      "max_acceptable_slippage_atr": {
        "value": "0.10",
        "unit": "x ATR(M5)",
        "status": "PROVISIONAL",
        "source_version": "execution-v1-OD4",
        "phase3_testable": true
      },
      "reconciliation_heartbeat_seconds": {
        "value": 15,
        "unit": "seconds",
        "status": "PROVISIONAL",
        "source_version": "execution-v1-OD5",
        "phase3_testable": true
      },
      "retry_max_attempts": {
        "value": 3,
        "unit": "count",
        "status": "PROVISIONAL",
        "source_version": "execution-v1-OD6",
        "phase3_testable": true
      },
      "sl_attach_max_retries": {
        "value": 2,
        "unit": "count",
        "status": "LOCKED",
        "source_version": "execution-v1-OD2",
        "phase3_testable": false
      }
    },
    "data": {
      "symbol": {
        "value": "XAUUSD",
        "unit": "symbol",
        "status": "LOCKED",
        "source_version": "phase1-v1",
        "phase3_testable": false
      }
    }
  }
}
```

## TRADING
```json
{
  "trade_count": 0,
  "note": "ZERO completed trades: no performance statistics are defined. Nothing was relaxed to manufacture trades."
}
```

## RISK
```json
{
  "rejection_counts_by_reason": {
    "WARMUP_PERIOD": 318,
    "STOP_TOO_WIDE": 126,
    "RR_BELOW_MINIMUM": 72,
    "TP2_NOT_BEYOND_TP1": 34,
    "HTF_CONTEXT_INVALID": 59,
    "STOP_BELOW_MINIMUM": 3
  },
  "daily_limit_blocks": 0,
  "consecutive_loss_blocks": 0,
  "trade_count_blocks": 0,
  "account": {
    "initial_balance": "10000",
    "final_balance": "10000",
    "realized_pnl_total": "0",
    "max_drawdown_abs": "0",
    "max_drawdown_pct": "0"
  }
}
```

## EXECUTION
```json
{
  "execution_model": "OHLC (SL-first on intrabar ambiguity); tick execution NOT used by this runner yet",
  "entry": "market fill at break-candle close (+spread+slippage if REALISTIC); NOT first-tick",
  "exit": "OHLC, stop-first on intrabar ambiguity; exit spread/slippage applied if REALISTIC; trailing updated at candle close",
  "ohlc_ambiguous_candles_while_in_position": 0,
  "validation_status": "NOT VALIDATED: Phase A baseline; no real-tick execution validation performed"
}
```
