"""Named action macros: multi-tap sequences for common UI flows."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from pathlib import Path

from uma_trainer.action.input_injector import InputInjector

if TYPE_CHECKING:
    import numpy as np
    from uma_trainer.capture.base import CaptureBackend
    from uma_trainer.perception.assembler import StateAssembler
    from uma_trainer.types import GameState, TrainingTile

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
        """Scan all training tiles for stat gains + failure rate.

        IMPORTANT: Tapping an already-raised tile CONFIRMS that training.
        So we first detect which tile is currently raised, read its gains
        directly, then only tap the OTHER tiles to preview them.

        Modifies state.training_tiles in-place.
        Must be called while on the stat selection screen.
        """
        if not state.training_tiles:
            return

        logger.info(
            "Scanning all %d training tiles for stat gains + failure rate",
            len(state.training_tiles),
        )

        # Settle time after tapping a tile before capturing.
        # The tile raise animation takes ~0.4s; 0.7s gives margin.
        TILE_SETTLE_TIME = 1.2  # allow gain animation to fully render

        # Clear stale data on all tiles
        for tile in state.training_tiles:
            tile.stat_gains = {}
            tile.failure_rate = 0.0

        # Step 1: Detect which tile is currently raised and read its gains.
        # This avoids tapping it again (which would confirm the training).
        try:
            frame = capture.grab_frame()
        except Exception as e:
            logger.warning("Initial frame capture failed: %s", e)
            return

        currently_raised = assembler.detect_selected_tile(frame)
        if currently_raised is not None:
            logger.info(
                "Currently raised tile: %d (%s)",
                currently_raised,
                state.training_tiles[currently_raised].stat_type.value
                if currently_raised < len(state.training_tiles) else "?",
            )

        # Read gains from the currently displayed preview (already raised tile)
        if currently_raised is not None and currently_raised < len(state.training_tiles):
            self._read_tile_data(
                state.training_tiles[currently_raised], frame, assembler,
            )

        # Step 2: Tap each OTHER tile to preview its gains.
        scanned_count = sum(
            1 for t in state.training_tiles if t.stat_gains
        )

        for tile in state.training_tiles:
            if tile.position == currently_raised:
                continue  # Already scanned above — do NOT re-tap

            self.injector.tap(tile.tap_coords[0], tile.tap_coords[1])
            time.sleep(TILE_SETTLE_TIME)

            try:
                frame = capture.grab_frame()
            except Exception as e:
                logger.warning(
                    "Frame capture failed for tile %s: %s",
                    tile.stat_type.value, e,
                )
                continue

            if self._read_tile_data(tile, frame, assembler):
                scanned_count += 1

        logger.info(
            "Scan complete: %d/%d tiles got stat gains",
            scanned_count, len(state.training_tiles),
        )

    def _read_tile_data(
        self,
        tile: "TrainingTile",
        frame: "np.ndarray",
        assembler: "StateAssembler",
    ) -> bool:
        """Read stat gains + failure rate from the current frame for a tile.

        Returns True if gains were successfully read.
        """
        # Read stat gains
        ok = False
        try:
            gains = assembler.read_stat_gains(frame)
            if gains:
                tile.stat_gains = gains
                ok = True
                logger.info(
                    "Tile %s gains: %s (total=%d)",
                    tile.stat_type.value,
                    gains,
                    sum(gains.values()),
                )
                # Save a debug screenshot if any single gain looks suspicious
                # (individual stat gains rarely exceed 50 in normal play)
                if any(v > 50 for v in gains.values()):
                    self._save_suspicious_frame(
                        frame, tile.stat_type.value, gains,
                    )
            else:
                logger.warning(
                    "No gains read for tile %s", tile.stat_type.value
                )
        except Exception as e:
            logger.warning(
                "OCR failed for tile %s gains: %s",
                tile.stat_type.value, e,
            )

        # Read failure rate
        try:
            failure = assembler.read_failure_rate(frame)
            if failure is not None:
                tile.failure_rate = failure
                logger.debug(
                    "Tile %s failure rate: %.0f%%",
                    tile.stat_type.value, failure * 100,
                )
        except Exception as e:
            logger.debug(
                "Failed to read failure rate for %s: %s",
                tile.stat_type.value, e,
            )

        # Read support card count from right panel portraits
        try:
            from uma_trainer.perception.pixel_analysis import count_panel_portraits
            from uma_trainer.perception.regions import STAT_SELECTION_REGIONS

            panel_region = STAT_SELECTION_REGIONS.get("support_panel")
            if panel_region is not None:
                card_count = count_panel_portraits(frame, panel_region)
                tile.support_cards = [f"card_{j}" for j in range(card_count)]
                logger.info(
                    "Tile %s: %d support cards (panel portraits)",
                    tile.stat_type.value, card_count,
                )
        except Exception as e:
            logger.debug(
                "Failed to read panel portraits for %s: %s",
                tile.stat_type.value, e,
            )

        return ok

    @staticmethod
    def _save_suspicious_frame(
        frame: "np.ndarray",
        stat_name: str,
        gains: dict[str, int],
    ) -> None:
        """Save a screenshot when OCR returns a suspiciously high gain value."""
        try:
            from PIL import Image

            out_dir = Path("screenshots/suspicious_ocr")
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            filename = f"{ts}_{stat_name}_{'_'.join(f'{s}{v}' for s, v in gains.items())}.png"
            Image.fromarray(frame[:, :, ::-1]).save(out_dir / filename)
            logger.warning(
                "Suspicious gain for %s: %s — saved %s",
                stat_name, gains, filename,
            )
        except Exception as e:
            logger.debug("Failed to save suspicious frame: %s", e)

    def attempt_error_recovery(self) -> None:
        """Generic recovery: tap common button positions to advance.

        Tries multiple tap targets that cover common blocking screens:
        - Bottom center for TAP prompts (post-race)
        - Close button position for popups
        - Green Next/confirm button for result screens
        - Back button as last resort
        """
        logger.info("Attempting error recovery: tapping common positions")
        # TAP prompt (post-race result/rival screens)
        self.injector.tap(540, 1675)
        time.sleep(1.5)
        # Close button on popups (result pts, etc.)
        self.injector.tap(520, 1250)
        time.sleep(1.5)
        # Green Next/confirm button (standings, rewards)
        self.injector.tap(765, 1760)
        time.sleep(1.5)
        # Generic confirm button
        self.injector.tap(*COORDS["confirm_btn"])
        time.sleep(1.0)
        # Android back button — escapes photo mode, stuck dialogs
        self.injector.back()
        time.sleep(1.5)
        # OK on "return to previous screen?" confirm that back may trigger
        self.injector.tap(775, 1245)
        time.sleep(1.0)
