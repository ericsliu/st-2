"""Screen identification via OCR text matching.

Uses Apple Vision OCR to read text from key screen regions, then matches
against known text patterns for each screen type.  This is more robust
than pixel-anchor or template-matching approaches because it works
regardless of colour theme, brightness, or minor UI layout changes.

Apple Vision OCR is fast (~20-30ms per region on M1 Neural Engine) and
highly accurate on the game's English UI text.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from uma_trainer.perception.ocr import OCREngine
from uma_trainer.types import ScreenState

logger = logging.getLogger(__name__)


# ── OCR regions for screen identification ────────────────────────────────────
# Each region is (x1, y1, x2, y2) at 1080×1920 resolution.
# We read 3 strategic zones that together distinguish every screen.

# Top header / title bar — captures screen titles like "Shop", "Race List"
HEADER_REGION = (0, 0, 600, 120)

# Left side of bottom button area — captures "Rest", "Back", etc.
BUTTON_LEFT_REGION = (0, 1380, 550, 1770)

# Right side / center of bottom button area — "Training", "Confirm", "Next", etc.
BUTTON_RIGHT_REGION = (300, 1600, 1080, 1830)

# Popup header area — captures "Warning" text on popup dialogs
POPUP_REGION = (0, 550, 1080, 700)

# Mid-screen area — captures event text markers, "View Results", etc.
MID_REGION = (0, 1000, 1080, 1500)


# ── Screen matching rules ────────────────────────────────────────────────────
# Each rule is: (ScreenState, required_keywords, forbidden_keywords, region_hint)
# A screen matches if ALL required keywords are found (case-insensitive) and
# NONE of the forbidden keywords are found.
#
# Rules are evaluated in order; first match wins.  More specific rules first.

ScreenRule = tuple[ScreenState, list[str], list[str]]

SCREEN_RULES: list[ScreenRule] = [
    # Warning popup — "Warning" in the popup header region is very distinctive
    (ScreenState.WARNING_POPUP, ["warning"], []),

    # Shop screen — "Shop" in header + "Confirm" or "Shop Coins" in buttons
    (ScreenState.SKILL_SHOP, ["shop"], ["race", "training"]),

    # Race list — "Race" in header area alongside list-style content
    (ScreenState.RACE_ENTRY, ["race list"], []),

    # Pre-race — has "View Results" button + race info. OCR sometimes
    # misreads "Results" as "Resulte" or "Result", so match loosely.
    (ScreenState.PRE_RACE, ["view result"], []),

    # Post-race — "Next" button, often with "Try Again" or placement text.
    # Exclude "effect" so post-race events (which have "Effects" button and
    # may contain "next" in choice text) are detected as EVENT instead.
    (ScreenState.POST_RACE, ["next"], ["race list", "view results", "rest",
                                       "training", "shop", "effect"]),

    # Event popup — choice buttons with event text
    (ScreenState.EVENT, ["effect"], ["rest", "training", "shop", "race list"]),

    # Skill shop / Learn screen
    (ScreenState.SKILL_SHOP, ["learn", "skill"], []),

    # Stat selection sub-screen — has "Back" button but no "Rest"
    # (handled via is_stat_selection, but we detect TRAINING for both)

    # Career home / turn action — has "Rest" and "Training" buttons
    (ScreenState.TRAINING, ["rest"], []),
    (ScreenState.TRAINING, ["training"], ["race list"]),

    # Main menu — game home screen (not in career)
    (ScreenState.MAIN_MENU, ["home"], []),

    # Result screen — but NOT pre-race which also contains "result" in
    # "View Results". Exclude "view result" and "race" to avoid overlap.
    (ScreenState.RESULT_SCREEN, ["result"], ["race list", "rest", "view result", "race"]),
]


class ScreenIdentifier:
    """Identify the current game screen by OCR-ing key text regions.

    Primary: Apple Vision OCR on 3-5 small regions, matched against
    known text patterns per screen.
    """

    def __init__(
        self,
        ocr: OCREngine | None = None,
        # Legacy params kept for backward compat — ignored
        template_dir: str = "data/templates",
        threshold: float = 0.8,
        tolerance: int = 30,
    ) -> None:
        self._ocr = ocr

    def set_ocr(self, ocr: OCREngine) -> None:
        """Set the OCR engine (for deferred initialization)."""
        self._ocr = ocr

    # ------------------------------------------------------------------
    # Identification
    # ------------------------------------------------------------------

    def identify(self, frame: np.ndarray) -> ScreenState:
        """Determine which screen is currently displayed via OCR.

        OCRs several key regions and matches text against known patterns.
        Fast: ~50-80ms total on M1 with Apple Vision.
        """
        if self._ocr is None:
            logger.error("OCR engine not set — cannot identify screen")
            return ScreenState.UNKNOWN

        t0 = time.monotonic()

        # Check for loading screen first (mostly dark = no text to read)
        if self._is_loading_screen(frame):
            logger.debug("Screen identified: loading (dark frame)")
            return ScreenState.LOADING

        # OCR the key regions
        all_text = self._ocr_regions(frame)
        all_lower = all_text.lower()

        # Match against rules
        for screen, required, forbidden in SCREEN_RULES:
            if all(kw in all_lower for kw in required):
                if not any(kw in all_lower for kw in forbidden):
                    elapsed = (time.monotonic() - t0) * 1000
                    logger.debug(
                        "Screen identified: %s (%.1fms) text='%s'",
                        screen.value, elapsed, all_text[:100],
                    )
                    return screen

        elapsed = (time.monotonic() - t0) * 1000
        logger.debug(
            "Screen UNKNOWN (%.1fms) text='%s'",
            elapsed, all_text[:120],
        )
        return ScreenState.UNKNOWN

    def is_stat_selection(self, frame: np.ndarray) -> bool:
        """Check if the current TRAINING screen is the stat selection sub-screen.

        The stat selection screen has a "Back" button at bottom-left and
        shows training tiles.  The career home screen has "Rest" at bottom-left.
        """
        if self._ocr is None:
            return False

        # OCR the bottom-left button area where "Rest" or "Back" appears
        btn_text = self._ocr.read_region(frame, BUTTON_LEFT_REGION).lower()

        # "back" present and "rest" absent → stat selection
        if "back" in btn_text and "rest" not in btn_text:
            return True

        # "rest" present → career home, not stat selection
        if "rest" in btn_text:
            return False

        # Check both button area and mid-screen for "Failure" text (failure rate
        # display) which only appears on stat selection. OCR sometimes truncates
        # to "Fail" so match the shorter form.
        mid_text = self._ocr.read_region(frame, MID_REGION).lower()
        all_text = btn_text + " " + mid_text

        if "fail" in all_text or "lvl" in all_text:
            return True

        return False

    def identify_with_details(
        self, frame: np.ndarray,
    ) -> tuple[ScreenState, dict[str, str]]:
        """Like identify(), but returns the OCR text per region for debugging."""
        if self._ocr is None:
            return ScreenState.UNKNOWN, {}

        details: dict[str, str] = {}

        if self._is_loading_screen(frame):
            return ScreenState.LOADING, {"note": "dark frame detected"}

        regions = {
            "header": HEADER_REGION,
            "button_left": BUTTON_LEFT_REGION,
            "button_right": BUTTON_RIGHT_REGION,
            "popup": POPUP_REGION,
            "mid": MID_REGION,
        }

        all_text_parts = []
        for name, region in regions.items():
            text = self._ocr.read_region(frame, region)
            details[name] = text
            all_text_parts.append(text)

        all_lower = " ".join(all_text_parts).lower()

        for screen, required, forbidden in SCREEN_RULES:
            if all(kw in all_lower for kw in required):
                if not any(kw in all_lower for kw in forbidden):
                    return screen, details

        return ScreenState.UNKNOWN, details

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ocr_regions(self, frame: np.ndarray) -> str:
        """OCR the key identification regions and concatenate the text."""
        parts = []
        for region in (
            HEADER_REGION,
            BUTTON_LEFT_REGION,
            BUTTON_RIGHT_REGION,
            POPUP_REGION,
            MID_REGION,
        ):
            text = self._ocr.read_region(frame, region)
            if text:
                parts.append(text)
        return " ".join(parts)

    @staticmethod
    def _is_loading_screen(frame: np.ndarray) -> bool:
        """Quick check: if the frame is mostly dark, it's a loading screen.

        Samples a grid of pixels; if >80% are very dark, assume loading.
        This avoids wasting OCR calls on blank frames.
        """
        h, w = frame.shape[:2]
        dark_count = 0
        total = 0

        for y in range(100, h - 100, 200):
            for x in range(100, w - 100, 200):
                b, g, r = frame[y, x]
                total += 1
                if int(r) + int(g) + int(b) < 120:
                    dark_count += 1

        return total > 0 and (dark_count / total) > 0.80

    # ------------------------------------------------------------------
    # Deprecated — kept for backward compat, not used
    # ------------------------------------------------------------------

    def sample_pixel(self, frame: np.ndarray, x: int, y: int) -> tuple[int, int, int]:
        """Sample a single pixel from the frame as (R, G, B)."""
        h, w = frame.shape[:2]
        x = min(max(x, 0), w - 1)
        y = min(max(y, 0), h - 1)
        b, g, r = frame[y, x]
        return (int(r), int(g), int(b))
