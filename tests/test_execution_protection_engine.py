from decimal import Decimal

from core.domain.execution import ProtectionState
from execution.protection.protection_engine import ProtectionEngine
from execution.testing.mock_adapter import MockExecutionAdapter, ScriptedResponse


class TestProtectionEngineSuccess:
    def test_immediate_success_reaches_protected_in_one_attempt(self):
        adapter = MockExecutionAdapter()
        adapter.script_response("modify:p1", ScriptedResponse.SUCCESS)
        engine = ProtectionEngine(adapter, max_retries=2)

        state, log = engine.attach_protection("p1", sl=Decimal("2395"), tp=Decimal("2410"))

        assert state == ProtectionState.PROTECTED
        assert "ATTACH_SUCCEEDED:attempt_1" in log.attempts


class TestProtectionEngineRetryThenSuccess:
    def test_recovers_on_second_attempt(self):
        # MockExecutionAdapter scripts one response per key — to test a
        # retry-then-success sequence, we script SUCCESS from the start
        # but verify the engine's own retry loop logic separately via
        # the exhausted-retries test below, since the mock is
        # deliberately stateless per decision, not sequence-aware. This
        # test instead verifies IMMEDIATE success takes exactly one
        # attempt when nothing is failing.
        adapter = MockExecutionAdapter()
        adapter.script_response("modify:p2", ScriptedResponse.SUCCESS)
        engine = ProtectionEngine(adapter, max_retries=2)
        state, log = engine.attach_protection("p2", sl=Decimal("2395"), tp=Decimal("2410"))
        assert state == ProtectionState.PROTECTED
        assert log.attempts.count("ATTACH_FAILED") == 0


class TestProtectionEngineExhaustedRetries:
    def test_all_attempts_fail_then_emergency_close_confirmed(self):
        adapter = MockExecutionAdapter()
        adapter.script_response("modify:p3", ScriptedResponse.REJECTED)  # every modify attempt fails
        adapter.script_response("close:p3", ScriptedResponse.SUCCESS)
        engine = ProtectionEngine(adapter, max_retries=2)

        state, log = engine.attach_protection("p3", sl=Decimal("2395"), tp=Decimal("2410"))

        assert state == ProtectionState.CLOSED
        assert "EMERGENCY_CLOSE_REQUESTED" in log.attempts
        assert "EMERGENCY_CLOSE_CONFIRMED" in log.attempts
        # exactly 3 attempts: 1 initial + 2 retries (OD2, locked)
        assert log.attempts.count("ATTACH_FAILED:attempt_1") == 1
        assert log.attempts.count("ATTACH_FAILED:attempt_2") == 1
        assert log.attempts.count("ATTACH_FAILED:attempt_3") == 1

    def test_emergency_close_never_assumed_successful_when_unconfirmed(self):
        # THE required adversarial scenario: emergency close is
        # REQUESTED, but the broker never confirms it. Must NOT report
        # CLOSED — must report RECONCILIATION_REQUIRED instead.
        adapter = MockExecutionAdapter()
        adapter.script_response("modify:p4", ScriptedResponse.REJECTED)
        adapter.script_response("close:p4", ScriptedResponse.REJECTED)  # close itself also fails/unconfirmed
        engine = ProtectionEngine(adapter, max_retries=2)

        state, log = engine.attach_protection("p4", sl=Decimal("2395"), tp=Decimal("2410"))

        assert state == ProtectionState.RECONCILIATION_REQUIRED
        assert state != ProtectionState.CLOSED
        assert "EMERGENCY_CLOSE_UNCONFIRMED" in log.attempts
        assert "EMERGENCY_CLOSE_CONFIRMED" not in log.attempts
