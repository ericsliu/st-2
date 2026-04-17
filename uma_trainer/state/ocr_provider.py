"""OCR-based state provider — full perception pipeline every call."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from uma_trainer.state.provider import GameStateProvider

if TYPE_CHECKING:
    from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
    from uma_trainer.perception.assembler import StateAssembler
    from uma_trainer.perception.screen_identifier import ScreenIdentifier
    from uma_trainer.types import ActionType, GameState

logger = logging.getLogger(__name__)


class OCRStateProvider(GameStateProvider):
    """Full OCR + assembly on every get_state() call.

    Simple and reliable. Used by the single-turn script and as the
    reference implementation.
    """

    def __init__(
        self,
        capture: "ScrcpyCapture",
        assembler: "StateAssembler",
        screen_id: "ScreenIdentifier",
    ) -> None:
        self._capture = capture
        self.assembler = assembler
        self.screen_id = screen_id
        self._last_frame: np.ndarray | None = None
        self._last_state: "GameState | None" = None

    @property
    def capture(self):
        return self._capture

    def get_state(self) -> "GameState":
        self._last_frame = self._capture.grab_frame()
        self._last_state = self.assembler.assemble(self._last_frame)
        return self._last_state

    def get_frame(self) -> np.ndarray:
        if self._last_frame is None:
            self._last_frame = self._capture.grab_frame()
        return self._last_frame

    def is_stat_selection(self) -> bool:
        frame = self.get_frame()
        return self.screen_id.is_stat_selection(frame)

    def invalidate(self) -> None:
        self._last_frame = None
        self._last_state = None

    def update_after_action(self, action_type: "ActionType", target: str = "") -> None:
        # OCR provider doesn't track — just invalidate so next read is fresh
        self.invalidate()

    def refresh_frame(self) -> np.ndarray:
        self._last_frame = self._capture.grab_frame()
        return self._last_frame
