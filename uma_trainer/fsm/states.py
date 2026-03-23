"""FSM state definitions and allowed transition table."""

from __future__ import annotations

from enum import Enum


class FSMState(str, Enum):
    """Bot-level operational states (distinct from game screen states)."""

    IDLE = "idle"
    INITIALIZING = "initializing"
    WAITING_FOR_GAME = "waiting_for_game"
    STARTING_CAREER = "starting_career"
    RUNNING_TURN = "running_turn"
    EXECUTING_ACTION = "executing_action"
    WAITING_ANIMATION = "waiting_animation"
    HANDLING_EVENT = "handling_event"
    IN_RACE = "in_race"
    SKILL_SHOPPING = "skill_shopping"
    CAREER_COMPLETE = "career_complete"
    ERROR_RECOVERY = "error_recovery"
    PAUSED = "paused"
    SHUTDOWN = "shutdown"


# Allowed state transitions — any unlisted transition raises InvalidTransitionError
TRANSITIONS: dict[FSMState, frozenset[FSMState]] = {
    FSMState.IDLE: frozenset({FSMState.INITIALIZING, FSMState.SHUTDOWN}),
    FSMState.INITIALIZING: frozenset({
        FSMState.WAITING_FOR_GAME,
        FSMState.ERROR_RECOVERY,
        FSMState.SHUTDOWN,
    }),
    FSMState.WAITING_FOR_GAME: frozenset({
        FSMState.STARTING_CAREER,
        FSMState.RUNNING_TURN,
        FSMState.ERROR_RECOVERY,
        FSMState.PAUSED,
        FSMState.SHUTDOWN,
    }),
    FSMState.STARTING_CAREER: frozenset({
        FSMState.RUNNING_TURN,
        FSMState.ERROR_RECOVERY,
        FSMState.SHUTDOWN,
    }),
    FSMState.RUNNING_TURN: frozenset({
        FSMState.EXECUTING_ACTION,
        FSMState.HANDLING_EVENT,
        FSMState.IN_RACE,
        FSMState.SKILL_SHOPPING,
        FSMState.CAREER_COMPLETE,
        FSMState.ERROR_RECOVERY,
        FSMState.PAUSED,
        FSMState.SHUTDOWN,
    }),
    FSMState.EXECUTING_ACTION: frozenset({
        FSMState.WAITING_ANIMATION,
        FSMState.RUNNING_TURN,
        FSMState.ERROR_RECOVERY,
    }),
    FSMState.WAITING_ANIMATION: frozenset({
        FSMState.RUNNING_TURN,
        FSMState.HANDLING_EVENT,
        FSMState.SKILL_SHOPPING,
        FSMState.IN_RACE,
        FSMState.ERROR_RECOVERY,
    }),
    FSMState.HANDLING_EVENT: frozenset({
        FSMState.EXECUTING_ACTION,
        FSMState.WAITING_ANIMATION,
        FSMState.RUNNING_TURN,
        FSMState.ERROR_RECOVERY,
    }),
    FSMState.IN_RACE: frozenset({
        FSMState.RUNNING_TURN,
        FSMState.CAREER_COMPLETE,
        FSMState.ERROR_RECOVERY,
    }),
    FSMState.SKILL_SHOPPING: frozenset({
        FSMState.RUNNING_TURN,
        FSMState.EXECUTING_ACTION,
        FSMState.ERROR_RECOVERY,
    }),
    FSMState.CAREER_COMPLETE: frozenset({
        FSMState.IDLE,
        FSMState.STARTING_CAREER,
        FSMState.SHUTDOWN,
    }),
    FSMState.ERROR_RECOVERY: frozenset({
        FSMState.RUNNING_TURN,
        FSMState.WAITING_FOR_GAME,
        FSMState.IDLE,
        FSMState.PAUSED,
        FSMState.SHUTDOWN,
    }),
    FSMState.PAUSED: frozenset({
        FSMState.RUNNING_TURN,
        FSMState.WAITING_FOR_GAME,
        FSMState.SHUTDOWN,
    }),
    FSMState.SHUTDOWN: frozenset(),
}


class InvalidTransitionError(RuntimeError):
    def __init__(self, from_state: FSMState, to_state: FSMState) -> None:
        super().__init__(
            f"Invalid FSM transition: {from_state.value} → {to_state.value}. "
            f"Allowed from {from_state.value}: {[s.value for s in TRANSITIONS[from_state]]}"
        )
        self.from_state = from_state
        self.to_state = to_state
