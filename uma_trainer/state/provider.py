"""Abstract base for game state providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from uma_trainer.types import ActionType, GameState


class GameStateProvider(ABC):
    """Interface for obtaining the current game state.

    Two implementations:
      - OCRStateProvider: full OCR every call (reliable, slower)
      - TrackedStateProvider: OCR once, then track mutations (faster, may drift)
    """

    @property
    @abstractmethod
    def capture(self):
        """Return the underlying capture backend (needed for scan_training_gains etc.)."""

    @abstractmethod
    def get_state(self) -> "GameState":
        """Return the current game state."""

    @abstractmethod
    def get_frame(self) -> np.ndarray:
        """Return the most recent raw frame (BGR numpy array)."""

    @abstractmethod
    def is_stat_selection(self) -> bool:
        """Check if the current frame shows the stat selection screen."""

    @abstractmethod
    def invalidate(self) -> None:
        """Force a full re-read on the next get_state() call."""

    @abstractmethod
    def update_after_action(self, action_type: "ActionType", target: str = "") -> None:
        """Notify the provider that an action was executed.

        The provider can use this to predict state mutations (energy changes,
        stat gains, turn increments) without re-reading via OCR.
        """

    @abstractmethod
    def refresh_frame(self) -> np.ndarray:
        """Capture a new frame without full state assembly.

        Useful for lightweight screen checks between full state reads.
        """
