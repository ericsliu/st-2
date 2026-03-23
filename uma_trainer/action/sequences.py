"""Named action macros: multi-tap sequences for common UI flows."""

from __future__ import annotations

import logging
import time

from uma_trainer.action.input_injector import InputInjector

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

    def attempt_error_recovery(self) -> None:
        """Generic recovery: try back → home → wait."""
        logger.info("Attempting error recovery: back → wait")
        self.injector.back()
        time.sleep(2.0)
        # Try tapping confirm in case a dialog is blocking
        self.injector.tap(*COORDS["confirm_btn"])
        time.sleep(1.0)
