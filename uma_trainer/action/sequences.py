"""Named action macros: multi-tap sequences for common UI flows."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from uma_trainer.action.input_injector import InputInjector

if TYPE_CHECKING:
    from uma_trainer.capture.base import CaptureBackend
    from uma_trainer.perception.assembler import StateAssembler
    from uma_trainer.types import GameState

logger = logging.getLogger(__name__)

# Screen coordinates (1280×720 normalized)
# These are approximate and may need calibration per emulator resolution.
COORDS = {
    "confirm_btn": (640, 580),
    "cancel_btn": (400, 580),
    "ok_btn": (640, 500),
    "training_screen_continue": (640, 680),
    "result_screen_continue": (640, 620),
    "skill_shop_done": (640, 640),
    "main_menu_career": (640, 400),
    "career_start_btn": (900, 600),
}


class ActionSequences:
    """Higher-level action sequences built from InputInjector primitives."""

    def __init__(self, injector: InputInjector) -> None:
        self.injector = injector

    def confirm_dialog(self) -> None:
        """Tap the confirm/OK button in any popup dialog."""
        self.injector.tap(*COORDS["confirm_btn"])

    def cancel_dialog(self) -> None:
        """Tap the cancel button."""
        self.injector.tap(*COORDS["cancel_btn"])

    def dismiss_result_screen(self, num_taps: int = 3) -> None:
        """Tap through result/animation screens.

        Result screens often require multiple taps to advance through
        each animation frame.
        """
        for i in range(num_taps):
            self.injector.tap(*COORDS["result_screen_continue"])
            time.sleep(0.8 + i * 0.2)

    def advance_loading(self) -> None:
        """Tap to advance through a loading/transition screen."""
        self.injector.tap(*COORDS["training_screen_continue"])

    def tap_rest_button(self) -> None:
        """Navigate to the Rest action."""
        # Rest button coordinates (approximate)
        self.injector.tap(640, 540)

    def dismiss_cutscene(self) -> None:
        """Attempt to skip a cutscene by tapping the screen."""
        for _ in range(3):
            self.injector.tap(640, 360)
            time.sleep(0.5)

    def navigate_to_career_mode(self) -> None:
        """From main menu: navigate into Career Mode."""
        self.injector.tap(*COORDS["main_menu_career"])
        time.sleep(1.0)

    def scan_training_gains(
        self,
        state: "GameState",
        capture: "CaptureBackend",
        assembler: "StateAssembler",
    ) -> None:
        """Tap each rainbow tile, OCR stat gains, store on the tile.

        Only scans tiles with is_rainbow=True. Non-rainbow tiles are
        skipped. Modifies state.training_tiles in-place.

        Must be called while on the stat selection screen.
        """
        tiles_to_scan = [
            t for t in state.training_tiles
            if t.is_rainbow and not t.stat_gains
        ]

        if not tiles_to_scan:
            logger.debug("No rainbow tiles to scan")
            return

        logger.info(
            "Scanning stat gains for %d rainbow tile(s): %s",
            len(tiles_to_scan),
            [t.stat_type.value for t in tiles_to_scan],
        )

        # Settle time after tapping a tile before capturing
        TILE_SETTLE_TIME = 0.6

        for tile in tiles_to_scan:
            # Tap the tile to select it
            self.injector.tap(tile.tap_coords[0], tile.tap_coords[1])
            time.sleep(TILE_SETTLE_TIME)

            # Capture and read gains
            try:
                frame = capture.grab_frame()
                gains = assembler.read_stat_gains(frame)
                if gains:
                    tile.stat_gains = gains
                    logger.info(
                        "Tile %s gains: %s (total=%d)",
                        tile.stat_type.value,
                        gains,
                        sum(gains.values()),
                    )
                else:
                    logger.warning(
                        "No gains read for tile %s", tile.stat_type.value
                    )
            except Exception as e:
                logger.warning(
                    "Failed to scan tile %s: %s", tile.stat_type.value, e
                )

    def attempt_error_recovery(self) -> None:
        """Generic recovery: try back → home → wait."""
        logger.info("Attempting error recovery: back → wait")
        self.injector.back()
        time.sleep(2.0)
        # Try tapping confirm in case a dialog is blocking
        self.injector.tap(*COORDS["confirm_btn"])
        time.sleep(1.0)
