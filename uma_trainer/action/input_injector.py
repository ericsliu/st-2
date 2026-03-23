"""Human-like input injection with timing variance and jitter."""

from __future__ import annotations

import logging
import random
import time

from uma_trainer.action.adb_client import ADBClient
from uma_trainer.config import AppConfig
from uma_trainer.types import BotAction

logger = logging.getLogger(__name__)

# Timing constants (seconds)
PRE_TAP_DELAY_MIN = 0.20
PRE_TAP_DELAY_MAX = 0.80
POST_TAP_DELAY_MIN = 0.10
POST_TAP_DELAY_MAX = 0.40

# Spatial jitter (pixels) applied to every tap
JITTER_MIN = 5
JITTER_MAX = 15


class InputInjector:
    """Wraps ADBClient to inject human-like timing variance and spatial jitter.

    All taps include:
    - Random pre-tap delay (200–800ms)
    - ±5–15px random offset from the nominal target
    - Random post-tap delay (100–400ms)
    """

    def __init__(self, adb: ADBClient, config: AppConfig) -> None:
        self.adb = adb
        self.config = config

    def tap(self, x: int, y: int) -> None:
        """Tap at (x, y) with human-like timing and jitter."""
        # Pre-tap delay
        delay = random.uniform(PRE_TAP_DELAY_MIN, PRE_TAP_DELAY_MAX)
        time.sleep(delay)

        # Spatial jitter
        jx = x + random.randint(-JITTER_MAX, JITTER_MAX)
        jy = y + random.randint(-JITTER_MAX, JITTER_MAX)
        jx = max(0, jx)
        jy = max(0, jy)

        self.adb.tap(jx, jy)
        logger.debug("Tap (%d,%d) → (%d,%d) after %.2fs", x, y, jx, jy, delay)

        # Post-tap delay
        time.sleep(random.uniform(POST_TAP_DELAY_MIN, POST_TAP_DELAY_MAX))

    def tap_action(self, action: BotAction) -> None:
        """Execute the tap specified in a BotAction."""
        if action.tap_coords == (0, 0):
            logger.warning("Action has no tap coords: %s %s", action.action_type, action.reason)
            return
        self.tap(action.tap_coords[0], action.tap_coords[1])

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> None:
        """Swipe with a pre-action delay."""
        time.sleep(random.uniform(PRE_TAP_DELAY_MIN, PRE_TAP_DELAY_MAX))
        self.adb.swipe(x1, y1, x2, y2, duration_ms)

    def back(self) -> None:
        """Tap the Android back button."""
        time.sleep(random.uniform(0.3, 0.6))
        self.adb.back()

    def wait_random_pause(self) -> None:
        """Occasional longer pause (1–3s) to simulate human breaks."""
        if random.random() < 0.05:  # 5% chance
            pause = random.uniform(1.0, 3.0)
            logger.debug("Random pause: %.1fs", pause)
            time.sleep(pause)
