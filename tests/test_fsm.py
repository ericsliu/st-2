"""Tests for FSM state transitions."""

import pytest

from uma_trainer.fsm.states import FSMState, TRANSITIONS, InvalidTransitionError


class TestFSMTransitions:
    def test_all_states_have_transitions_defined(self):
        for state in FSMState:
            assert state in TRANSITIONS, f"No transitions defined for {state}"

    def test_shutdown_has_no_outgoing_transitions(self):
        assert len(TRANSITIONS[FSMState.SHUTDOWN]) == 0

    def test_idle_can_transition_to_initializing(self):
        assert FSMState.INITIALIZING in TRANSITIONS[FSMState.IDLE]

    def test_running_turn_can_reach_all_expected_states(self):
        expected = {
            FSMState.EXECUTING_ACTION,
            FSMState.HANDLING_EVENT,
            FSMState.IN_RACE,
            FSMState.SKILL_SHOPPING,
            FSMState.CAREER_COMPLETE,
            FSMState.ERROR_RECOVERY,
            FSMState.PAUSED,
        }
        for state in expected:
            assert state in TRANSITIONS[FSMState.RUNNING_TURN], (
                f"RUNNING_TURN should be able to transition to {state}"
            )

    def test_invalid_transition_class(self):
        error = InvalidTransitionError(FSMState.IDLE, FSMState.CAREER_COMPLETE)
        assert "IDLE" in str(error) or "idle" in str(error)
        assert error.from_state == FSMState.IDLE
        assert error.to_state == FSMState.CAREER_COMPLETE

    def test_no_self_loops(self):
        """No state should transition to itself."""
        for state, targets in TRANSITIONS.items():
            assert state not in targets, f"State {state} has a self-loop"

    def test_transitions_are_frozensets(self):
        """All transition sets should be frozensets (immutable)."""
        for state, targets in TRANSITIONS.items():
            assert isinstance(targets, frozenset), (
                f"TRANSITIONS[{state}] should be frozenset, got {type(targets)}"
            )

    def test_error_recovery_can_reach_idle(self):
        """Error recovery should be able to return to idle for graceful shutdown."""
        assert FSMState.IDLE in TRANSITIONS[FSMState.ERROR_RECOVERY]

    def test_paused_can_shutdown(self):
        assert FSMState.SHUTDOWN in TRANSITIONS[FSMState.PAUSED]
