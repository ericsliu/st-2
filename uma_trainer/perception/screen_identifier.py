"""Screen identification via pixel colour sampling at anchor points.

Replaces YOLO-based screen state detection.  Checks a small number of
diagnostic pixels per screen type and returns the best match.
"""

from __future__ import annotations

import logging

import numpy as np

from uma_trainer.perception.regions import SCREEN_ANCHORS, ScreenAnchorSet
from uma_trainer.types import ScreenState

logger = logging.getLogger(__name__)

# Default tolerance added to each anchor's RGB range.
DEFAULT_TOLERANCE = 30


class ScreenIdentifier:
    """Identify the current game screen from a frame using pixel anchors."""

    def __init__(self, tolerance: int = DEFAULT_TOLERANCE) -> None:
        self.tolerance = tolerance
        self.anchors = SCREEN_ANCHORS

    def identify(self, frame: np.ndarray) -> ScreenState:
        """Determine which screen is currently displayed.

        Args:
            frame: BGR numpy array (H, W, 3) at 1080×1920.

        Returns:
            The detected ScreenState, or ScreenState.UNKNOWN.
        """
        h, w = frame.shape[:2]
        best_screen = ScreenState.UNKNOWN
        best_score = 0.0

        for anchor_set in self.anchors:
            matches = 0
            total = len(anchor_set.anchors)

            for anchor in anchor_set.anchors:
                # Clamp to frame bounds
                x = min(max(anchor.x, 0), w - 1)
                y = min(max(anchor.y, 0), h - 1)

                # Frame is BGR; convert to RGB for anchor matching
                b, g, r = frame[y, x]
                r, g, b = int(r), int(g), int(b)

                if anchor.matches(r, g, b, self.tolerance):
                    matches += 1

            if matches >= anchor_set.min_matches:
                # Score by fraction of anchors matched
                score = matches / total
                if score > best_score:
                    best_score = score
                    best_screen = anchor_set.screen

        if best_screen == ScreenState.UNKNOWN:
            logger.debug("Screen identification failed — no anchor set matched")
        else:
            logger.debug("Screen identified: %s (score=%.2f)", best_screen.value, best_score)

        return best_screen

    def is_stat_selection(self, frame: np.ndarray) -> bool:
        """Check if the current screen is the training stat selection sub-screen.

        Distinguished from the main turn action screen by the presence of
        a "Back" button at the bottom-left.  This is a heuristic check
        until we add a dedicated ScreenState.

        Args:
            frame: BGR numpy array (H, W, 3) at 1080×1920.

        Returns:
            True if this looks like the stat selection screen.
        """
        h, w = frame.shape[:2]
        if h < 1900 or w < 1000:
            return False

        # Sample the "Back" button region at bottom-left (~100, 1870)
        # On the stat selection screen this area has a dark button background.
        # On the turn action screen this area has the "Skip" button instead.
        # We check for the presence of "Back" by looking for a distinct
        # dark rectangle in the bottom-left corner.
        region = frame[1840:1900, 30:200]
        if region.size == 0:
            return False

        # The Back button has a dark background; compute average brightness
        avg_brightness = np.mean(region)
        # Stat selection "Back" button is darker than turn action "Skip" area
        return avg_brightness < 100

    def sample_pixel(self, frame: np.ndarray, x: int, y: int) -> tuple[int, int, int]:
        """Sample a single pixel from the frame as (R, G, B).

        Utility for calibration scripts.
        """
        h, w = frame.shape[:2]
        x = min(max(x, 0), w - 1)
        y = min(max(y, 0), h - 1)
        b, g, r = frame[y, x]
        return (int(r), int(g), int(b))
