"""Tracked state provider — OCR once, then predict mutations.

Performs full OCR on first call and every N turns, predicting state
changes in between based on executed actions. Falls back to full OCR
on screen transitions or when drift is detected.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from uma_trainer.state.provider import GameStateProvider
from uma_trainer.types import ActionType, GameState, Mood, ScreenState

if TYPE_CHECKING:
    from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
    from uma_trainer.perception.assembler import StateAssembler
    from uma_trainer.perception.screen_identifier import ScreenIdentifier

logger = logging.getLogger(__name__)

# Default turns between full OCR validation reads
DEFAULT_VALIDATION_INTERVAL = 3
# Use every-turn validation when conditions are active or in late game
LATE_GAME_TURN_FRACTION = 0.7


class TrackedStateProvider(GameStateProvider):
    """Tracks game state mutations between OCR reads.

    Full OCR is performed:
      - On the first call
      - Every `validation_interval` turns
      - When invalidate() is called explicitly
      - When a screen transition is detected (lightweight screen_id check)
      - During late game or when negative conditions are active (every turn)
    """

    def __init__(
        self,
        capture: "ScrcpyCapture",
        assembler: "StateAssembler",
        screen_id: "ScreenIdentifier",
        validation_interval: int = DEFAULT_VALIDATION_INTERVAL,
    ) -> None:
        self._capture = capture
        self.assembler = assembler
        self.screen_id = screen_id
        self.validation_interval = validation_interval
        self._last_frame: np.ndarray | None = None
        self._cached_state: GameState | None = None
        self._needs_full_read = True
        self._turns_since_validation = 0
        self._last_validated_turn = -1

    @property
    def capture(self):
        return self._capture

    def get_state(self) -> GameState:
        self._last_frame = self._capture.grab_frame()

        if self._needs_full_read or self._cached_state is None:
            return self._do_full_read()

        # Lightweight screen check — if screen changed, do full read
        quick_state = self.assembler.assemble(self._last_frame)
        if quick_state.screen != self._cached_state.screen:
            logger.info("Screen changed %s → %s — full OCR",
                        self._cached_state.screen.value, quick_state.screen.value)
            self._cached_state = quick_state
            self._needs_full_read = False
            return self._cached_state

        # Check if periodic validation is due
        if self._should_validate(quick_state):
            return self._do_full_read()

        # Use cached state with quick-read updates for volatile fields
        self._cached_state.energy = quick_state.energy
        self._cached_state.mood = quick_state.mood
        self._cached_state.current_turn = quick_state.current_turn
        self._cached_state.skill_pts = quick_state.skill_pts
        self._cached_state.screen = quick_state.screen
        self._cached_state.is_race_day = quick_state.is_race_day
        self._cached_state.training_tiles = quick_state.training_tiles
        self._cached_state.event_text = quick_state.event_text
        self._cached_state.event_choices = quick_state.event_choices

        return self._cached_state

    def get_frame(self) -> np.ndarray:
        if self._last_frame is None:
            self._last_frame = self.capture.grab_frame()
        return self._last_frame

    def is_stat_selection(self) -> bool:
        frame = self.get_frame()
        return self.screen_id.is_stat_selection(frame)

    def invalidate(self) -> None:
        self._needs_full_read = True
        self._last_frame = None

    def update_after_action(self, action_type: ActionType, target: str = "") -> None:
        if self._cached_state is None:
            return

        # Predict state changes based on action
        if action_type == ActionType.REST:
            self._cached_state.energy = min(100, self._cached_state.energy + 30)
        elif action_type == ActionType.TRAIN:
            # Energy cost varies; invalidate to get accurate reading
            self._needs_full_read = True
        elif action_type == ActionType.RACE:
            self._needs_full_read = True
        elif action_type in (ActionType.GO_OUT, ActionType.INFIRMARY):
            self._needs_full_read = True

        self._turns_since_validation += 1

    def refresh_frame(self) -> np.ndarray:
        self._last_frame = self._capture.grab_frame()
        return self._last_frame

    def _do_full_read(self) -> GameState:
        if self._last_frame is None:
            self._last_frame = self._capture.grab_frame()
        self._cached_state = self.assembler.assemble(self._last_frame)
        self._needs_full_read = False
        self._turns_since_validation = 0
        self._last_validated_turn = self._cached_state.current_turn
        logger.debug("Full OCR read: screen=%s turn=%d energy=%d",
                     self._cached_state.screen.value,
                     self._cached_state.current_turn,
                     self._cached_state.energy)
        return self._cached_state

    def _should_validate(self, quick_state: GameState) -> bool:
        # Always validate if turn changed and interval exceeded
        if quick_state.current_turn != self._last_validated_turn:
            self._turns_since_validation += 1

        if self._turns_since_validation >= self.validation_interval:
            return True

        # Validate every turn in late game
        if self._cached_state and self._cached_state.current_turn > (
            self._cached_state.max_turns * LATE_GAME_TURN_FRACTION
        ):
            return True

        # Validate every turn when negative conditions are active
        if self._cached_state and self._cached_state.active_conditions:
            from uma_trainer.types import Condition
            negative = {Condition.NIGHT_OWL, Condition.MIGRAINE,
                        Condition.SKIN_OUTBREAK, Condition.SLACKER,
                        Condition.PRACTICE_POOR, Condition.OVERWEIGHT}
            if any(c in negative for c in self._cached_state.active_conditions):
                return True

        return False
