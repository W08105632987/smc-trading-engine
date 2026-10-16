from datetime import datetime, timezone
from decimal import Decimal

from core.domain.common import Direction
from core.domain.execution import Position, PositionLifecycleState, ProtectionState
from execution.reconciliation.reconciliation_engine import MismatchType, ReconciliationEngine
from execution.testing.mock_adapter import MockExecutionAdapter


def make_position(position_id: str, volume="0.10", sl="2395", tp1="2410") -> Position:
    return Position(
        position_id=position_id, decision_id=f"d-{position_id}", symbol="XAUUSD",
        direction=Direction.BULLISH, volume=Decimal(volume), entry_price=Decimal("2400"),
        stop_loss=Decimal(sl), take_profit_1=Decimal(tp1), take_profit_2=Decimal("2420"),
        protection_state=ProtectionState.PROTECTED, lifecycle_state=PositionLifecycleState.POSITION_ACTIVE,
        opened_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )


class TestReconciliationEngine:
    def test_matching_positions_produce_no_mismatches(self):
        adapter = MockExecutionAdapter()
        pos = make_position("p1")
        adapter.set_broker_position(pos)
        engine = ReconciliationEngine()
        mismatches = engine.find_mismatches([pos], adapter)
        assert mismatches == []

    def test_local_missing_at_broker(self):
        adapter = MockExecutionAdapter()  # broker reports nothing
        local = make_position("p1")
        engine = ReconciliationEngine()
        mismatches = engine.find_mismatches([local], adapter)
        assert f"{MismatchType.LOCAL_MISSING_AT_BROKER}:p1" in mismatches

    def test_broker_missing_locally_orphan_position(self):
        # Adversarial: broker reports a position we have no local record of.
        adapter = MockExecutionAdapter()
        adapter.set_broker_position(make_position("p1"))
        engine = ReconciliationEngine()
        mismatches = engine.find_mismatches([], adapter)  # no local positions at all
        assert f"{MismatchType.BROKER_MISSING_LOCALLY}:p1" in mismatches

    def test_quantity_mismatch_detected(self):
        adapter = MockExecutionAdapter()
        local = make_position("p1", volume="0.10")
        broker = make_position("p1", volume="0.20")
        adapter.set_broker_position(broker)
        engine = ReconciliationEngine()
        mismatches = engine.find_mismatches([local], adapter)
        assert f"{MismatchType.QUANTITY_MISMATCH}:p1" in mismatches

    def test_sl_mismatch_detected(self):
        adapter = MockExecutionAdapter()
        local = make_position("p1", sl="2395")
        broker = make_position("p1", sl="2390")
        adapter.set_broker_position(broker)
        engine = ReconciliationEngine()
        mismatches = engine.find_mismatches([local], adapter)
        assert f"{MismatchType.SL_MISMATCH}:p1" in mismatches

    def test_tp_mismatch_detected(self):
        adapter = MockExecutionAdapter()
        local = make_position("p1", tp1="2410")
        broker = make_position("p1", tp1="2415")
        adapter.set_broker_position(broker)
        engine = ReconciliationEngine()
        mismatches = engine.find_mismatches([local], adapter)
        assert f"{MismatchType.TP_MISMATCH}:p1" in mismatches

    def test_multiple_mismatches_for_same_position_all_reported(self):
        adapter = MockExecutionAdapter()
        local = make_position("p1", volume="0.10", sl="2395", tp1="2410")
        broker = make_position("p1", volume="0.20", sl="2390", tp1="2415")
        adapter.set_broker_position(broker)
        engine = ReconciliationEngine()
        mismatches = engine.find_mismatches([local], adapter)
        assert len(mismatches) == 3

    def test_engine_never_creates_a_trade_only_reports(self):
        # Structural guarantee check: find_mismatches has no adapter
        # method call capable of submitting an order — verified by the
        # adapter contract it depends on (ReconciliationAdapter Protocol
        # only exposes get_open_positions, nothing order-submitting).
        from execution.reconciliation.reconciliation_engine import ReconciliationAdapter
        methods = [m for m in dir(ReconciliationAdapter) if not m.startswith("_")]
        assert methods == ["get_open_positions"]
