"""Abstract base class for screen capture backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class CaptureBackend(ABC):
    """Abstract screen capture interface.

    All backends must return BGR numpy arrays (OpenCV convention).
    """

    @abstractmethod
    def start(self) -> None:
        """Initialize the capture backend (connect, open window, etc.)."""

    @abstractmethod
    def stop(self) -> None:
        """Release resources."""

    @abstractmethod
    def grab_frame(self) -> np.ndarray:
        """Capture and return the current frame as a BGR numpy array.

        Returns:
            np.ndarray: Shape (H, W, 3), dtype uint8, BGR color order.

        Raises:
            CaptureError: If the frame could not be captured.
        """

    def __enter__(self) -> "CaptureBackend":
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.stop()


class CaptureError(RuntimeError):
    """Raised when a frame cannot be captured."""
