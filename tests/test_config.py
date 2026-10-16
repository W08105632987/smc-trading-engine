from decimal import Decimal

from core.config.registry import EngineConfig, Parameter, ParameterStatus, StrategyConfig


class TestParameterStatus:
    def test_fractal_definition_is_locked(self):
        # The algorithm (2-left/2-right) is LOCKED; only threshold
        # numbers like major_swing_atr are PROVISIONAL.
        cfg = StrategyConfig()
        assert cfg.fractal_left.status == ParameterStatus.LOCKED
        assert cfg.fractal_right.status == ParameterStatus.LOCKED

    def test_thresholds_are_provisional(self):
        cfg = StrategyConfig()
        assert cfg.minimum_score.status == ParameterStatus.PROVISIONAL
        assert cfg.sweep_min_penetration_atr.status == ParameterStatus.PROVISIONAL

    def test_frozen_defaults_match_strategy_spec(self):
        cfg = StrategyConfig()
        assert cfg.minimum_score.value == 70
        # RR-02 (docs/DECISIONS.md): deliberately raised from 2.0 to 3.8 as
        # the first tuning pass following the TRAIL-01 bugfix. This is an
        # intentional PROVISIONAL value change, not drift -- update this
        # assertion again if RR-02 is superseded by a later decision.
        assert cfg.minimum_rr.value == Decimal("3.8")
        assert cfg.max_pending_setups.value == 3
        assert cfg.tp1_allocation_pct.value == Decimal("30")


class TestEngineConfig:
    def test_config_hash_is_deterministic(self):
        cfg1 = EngineConfig()
        cfg2 = EngineConfig()
        assert cfg1.config_hash() == cfg2.config_hash()

    def test_config_hash_changes_when_a_parameter_changes(self):
        cfg1 = EngineConfig()
        cfg2 = EngineConfig()
        cfg2.strategy.minimum_score.value = 75
        assert cfg1.config_hash() != cfg2.config_hash()

    def test_risk_defaults_match_frozen_spec(self):
        cfg = EngineConfig()
        assert cfg.risk.risk_per_trade_pct.value == Decimal("0.5")
        assert cfg.risk.daily_loss_limit_pct.value == Decimal("2.0")

    def test_execution_defaults_match_od_resolutions(self):
        cfg = EngineConfig()
        assert cfg.execution.decision_expiry_seconds.value == 5
        assert cfg.execution.max_acceptable_slippage_atr.value == Decimal("0.10")
        assert cfg.execution.reconciliation_heartbeat_seconds.value == 15
        assert cfg.execution.sl_attach_max_retries.status == ParameterStatus.LOCKED
        assert cfg.execution.sl_attach_max_retries.value == 2
