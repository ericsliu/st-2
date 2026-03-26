"""Screen identification via template matching.

Uses cv2.matchTemplate() to find distinctive UI elements (icons, buttons,
labels) in each frame.  Each screen type has a set of template images —
small crops of stable, non-animated UI elements.  If enough templates for
a screen match above a confidence threshold, that screen is identified.

Templates are stored in data/templates/<screen_name>/<template>.png and
extracted via scripts/extract_templates.py.

Falls back to pixel-anchor checks if no templates are loaded (e.g. first
run before any templates have been extracted).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from uma_trainer.types import ScreenState

logger = logging.getLogger(__name__)

# Confidence threshold for cv2.matchTemplate (TM_CCOEFF_NORMED).
# 0.8 is a good starting point — raise if false positives appear.
DEFAULT_THRESHOLD = 0.8


@dataclass
class ScreenTemplate:
    """A single template image associated with a screen state."""
    screen: ScreenState
    name: str
    image: np.ndarray          # BGR template image
    threshold: float = DEFAULT_THRESHOLD


@dataclass
class ScreenTemplateSet:
    """All templates for a single screen state."""
    screen: ScreenState
    templates: list[ScreenTemplate] = field(default_factory=list)
    min_matches: int = 1       # How many templates must match


class ScreenIdentifier:
    """Identify the current game screen from a frame using template matching."""

    def __init__(
        self,
        template_dir: str = "data/templates",
        threshold: float = DEFAULT_THRESHOLD,
        tolerance: int = 30,  # kept for pixel-anchor fallback compat
    ) -> None:
        self.template_dir = Path(template_dir)
        self.threshold = threshold
        self.tolerance = tolerance

        self._template_sets: list[ScreenTemplateSet] = []
        self._loaded = False

        self._load_templates()

    # ------------------------------------------------------------------
    # Template loading
    # ------------------------------------------------------------------

    def _load_templates(self) -> None:
        """Load all template images from data/templates/<screen>/."""
        if not self.template_dir.exists():
            logger.warning("Template dir not found: %s", self.template_dir)
            return

        screen_map = {s.value: s for s in ScreenState}

        for screen_dir in sorted(self.template_dir.iterdir()):
            if not screen_dir.is_dir():
                continue

            screen_name = screen_dir.name
            screen_state = screen_map.get(screen_name)
            if screen_state is None:
                # Also handle aliases like "stat_selection" → TRAINING
                if screen_name == "stat_selection":
                    screen_state = ScreenState.TRAINING
                else:
                    logger.debug("Skipping unknown screen dir: %s", screen_name)
                    continue

            ts = ScreenTemplateSet(screen=screen_state)

            for img_path in sorted(screen_dir.glob("*.png")):
                img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
                if img is None:
                    logger.warning("Failed to load template: %s", img_path)
                    continue

                ts.templates.append(ScreenTemplate(
                    screen=screen_state,
                    name=img_path.stem,
                    image=img,
                    threshold=self.threshold,
                ))

            if ts.templates:
                # Require at least half the templates to match (min 1)
                ts.min_matches = max(1, len(ts.templates) // 2)
                self._template_sets.append(ts)
                logger.info(
                    "Loaded %d templates for %s (need %d matches)",
                    len(ts.templates), screen_state.value, ts.min_matches,
                )

        self._loaded = bool(self._template_sets)
        if not self._loaded:
            logger.warning(
                "No templates loaded from %s — screen identification will "
                "fall back to pixel anchors. Run scripts/extract_templates.py "
                "to create templates.",
                self.template_dir,
            )

    # ------------------------------------------------------------------
    # Identification
    # ------------------------------------------------------------------

    def identify(self, frame: np.ndarray) -> ScreenState:
        """Determine which screen is currently displayed.

        Args:
            frame: BGR numpy array (H, W, 3) at 1080x1920.

        Returns:
            The detected ScreenState, or ScreenState.UNKNOWN.
        """
        if not self._loaded:
            return self._identify_pixel_anchors(frame)

        best_screen = ScreenState.UNKNOWN
        best_score = 0.0

        for ts in self._template_sets:
            matches = 0
            total = len(ts.templates)

            for tmpl in ts.templates:
                if self._match_template(frame, tmpl):
                    matches += 1

            if matches >= ts.min_matches:
                score = matches / total
                if score > best_score:
                    best_score = score
                    best_screen = ts.screen

        if best_screen == ScreenState.UNKNOWN:
            logger.debug("Template matching failed — no screen matched")
        else:
            logger.debug(
                "Screen identified: %s (%.0f%% templates matched)",
                best_screen.value, best_score * 100,
            )

        return best_screen

    def _match_template(self, frame: np.ndarray, tmpl: ScreenTemplate) -> bool:
        """Check if a single template exists in the frame."""
        th, tw = tmpl.image.shape[:2]
        fh, fw = frame.shape[:2]

        if th > fh or tw > fw:
            return False

        result = cv2.matchTemplate(frame, tmpl.image, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        matched = max_val >= tmpl.threshold
        if matched:
            logger.debug(
                "  Template '%s' matched at (%d,%d) conf=%.3f",
                tmpl.name, max_loc[0], max_loc[1], max_val,
            )
        return matched

    def identify_with_details(
        self, frame: np.ndarray
    ) -> tuple[ScreenState, dict[str, list[dict]]]:
        """Like identify(), but also returns per-template match details.

        Useful for calibration and debugging.

        Returns:
            (screen_state, details) where details maps screen names to
            lists of {name, matched, confidence, location}.
        """
        details: dict[str, list[dict]] = {}

        if not self._loaded:
            screen = self._identify_pixel_anchors(frame)
            return screen, details

        best_screen = ScreenState.UNKNOWN
        best_score = 0.0

        for ts in self._template_sets:
            matches = 0
            total = len(ts.templates)
            screen_details = []

            for tmpl in ts.templates:
                th, tw = tmpl.image.shape[:2]
                fh, fw = frame.shape[:2]
                if th > fh or tw > fw:
                    screen_details.append({
                        "name": tmpl.name, "matched": False,
                        "confidence": 0.0, "location": None,
                    })
                    continue

                result = cv2.matchTemplate(frame, tmpl.image, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                hit = max_val >= tmpl.threshold
                if hit:
                    matches += 1
                screen_details.append({
                    "name": tmpl.name,
                    "matched": hit,
                    "confidence": round(float(max_val), 4),
                    "location": (int(max_loc[0]), int(max_loc[1])) if hit else None,
                })

            details[ts.screen.value] = screen_details

            if matches >= ts.min_matches:
                score = matches / total
                if score > best_score:
                    best_score = score
                    best_screen = ts.screen

        return best_screen, details

    # ------------------------------------------------------------------
    # Stat selection sub-screen check
    # ------------------------------------------------------------------

    def is_stat_selection(self, frame: np.ndarray) -> bool:
        """Check if the current screen is the training stat selection sub-screen.

        If stat_selection templates exist, use template matching.
        Otherwise fall back to checking for the absence of the Rest button.
        """
        # Check if we have stat_selection templates
        for ts in self._template_sets:
            if ts.screen == ScreenState.TRAINING:
                # Look for templates from the stat_selection directory
                stat_templates = [
                    t for t in ts.templates
                    if t.name.startswith("back_btn")
                ]
                if stat_templates:
                    return any(self._match_template(frame, t) for t in stat_templates)

        # Fallback: check for absence of green Rest button at (187, 1525)
        h, w = frame.shape[:2]
        if h < 1900 or w < 1000:
            return False
        r, g, b = self.sample_pixel(frame, 187, 1525)
        rest_btn_green = (90 <= r <= 150 and 175 <= g <= 235 and 10 <= b <= 65)
        return not rest_btn_green

    # ------------------------------------------------------------------
    # Pixel anchor fallback
    # ------------------------------------------------------------------

    def _identify_pixel_anchors(self, frame: np.ndarray) -> ScreenState:
        """Fallback screen identification using pixel colour anchors.

        Used when no templates have been extracted yet.
        """
        from uma_trainer.perception.regions import SCREEN_ANCHORS

        best_screen = ScreenState.UNKNOWN
        best_score = 0.0

        for anchor_set in SCREEN_ANCHORS:
            matches = 0
            total = len(anchor_set.anchors)

            for anchor in anchor_set.anchors:
                h, w = frame.shape[:2]
                x = min(max(anchor.x, 0), w - 1)
                y = min(max(anchor.y, 0), h - 1)
                b, g, r = frame[y, x]
                r, g, b = int(r), int(g), int(b)
                if anchor.matches(r, g, b, self.tolerance):
                    matches += 1

            if matches >= anchor_set.min_matches:
                score = matches / total
                if score > best_score:
                    best_score = score
                    best_screen = anchor_set.screen

        if best_screen == ScreenState.UNKNOWN:
            logger.debug("Pixel anchor fallback — no screen matched")
        else:
            logger.debug("Pixel anchor fallback — %s (%.2f)", best_screen.value, best_score)

        return best_screen

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def sample_pixel(self, frame: np.ndarray, x: int, y: int) -> tuple[int, int, int]:
        """Sample a single pixel from the frame as (R, G, B)."""
        h, w = frame.shape[:2]
        x = min(max(x, 0), w - 1)
        y = min(max(y, 0), h - 1)
        b, g, r = frame[y, x]
        return (int(r), int(g), int(b))
