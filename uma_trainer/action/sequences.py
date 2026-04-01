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
    from uma_trainer.perception.ocr import OCREngine
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

        # Read bond meter levels from portrait gauge bars.
        # Bond bars are authoritative for support card count — they filter out
        # NPC portraits (Director Akikawa, Reporter) which don't have bars.
        try:
            from uma_trainer.perception.pixel_analysis import read_bond_levels
            bonds = read_bond_levels(frame)
            tile.bond_levels = bonds
            if bonds:
                # Use bond bar count as the true support card count,
                # since panel portrait detection may include NPCs.
                tile.support_cards = [f"card_{j}" for j in range(len(bonds))]
                logger.info(
                    "Tile %s: %d support cards, bond levels %s",
                    tile.stat_type.value, len(bonds), bonds,
                )
        except Exception as e:
            logger.debug(
                "Failed to read bond levels for %s: %s",
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

    # ------------------------------------------------------------------
    # Item bag usage
    # ------------------------------------------------------------------

    # Item bag layout (1080x1920 portrait, Trackblazer scenario)
    # Calibrated from pixel scanning of green + button circles.
    # The bag is a scrollable list with "Training Items" header at top.
    ITEM_BAG_BTN = (870, 1120)          # "Training Items" button on career home
    ITEM_BAG_ROW_FIRST_Y = 271          # Center Y of first row's + button
    ITEM_BAG_ROW_HEIGHT = 204           # Spacing between rows
    ITEM_BAG_VISIBLE_ROWS = 7           # Max visible rows without scrolling
    ITEM_BAG_PLUS_X = 1000              # X coordinate of "+" button center
    ITEM_BAG_NAME_REGION_X = (80, 700)  # X range for item name OCR
    ITEM_BAG_NAME_REGION_H = 80         # Height of name region per row
    ITEM_BAG_CLOSE = (110, 1750)        # "Close" button
    ITEM_BAG_CONFIRM = (880, 1750)      # "Confirm Use" button

    def execute_item_use(
        self,
        item_key: str,
        item_name: str,
        capture: "CaptureBackend",
        ocr: "OCREngine",
    ) -> bool:
        """Open item bag, find item by name, select it, and confirm use.

        Args:
            item_key: Internal key (e.g. "vita_40")
            item_name: Display name to OCR-match (e.g. "Vita 40")
            capture: Screen capture backend
            ocr: OCR engine for reading item names

        Returns True if the item was successfully used.
        """
        from rapidfuzz import fuzz

        # Step 1: Open item bag
        logger.info("Opening item bag...")
        self.injector.tap(*self.ITEM_BAG_BTN)
        time.sleep(2.0)

        # Step 2: OCR visible rows to find the target item
        frame = capture.grab_frame()
        target_lower = item_name.lower()

        found_row = self._find_item_row(frame, ocr, target_lower)

        # Scroll and retry if not found
        if found_row is None:
            for scroll_attempt in range(3):
                logger.info("Item not visible — scrolling down (attempt %d)", scroll_attempt + 1)
                self.injector.swipe(540, 1200, 540, 400, duration_ms=500)
                time.sleep(0.8)
                self.injector.tap(540, 350)  # halt scroll momentum
                time.sleep(1.0)
                frame = capture.grab_frame()
                found_row = self._find_item_row(frame, ocr, target_lower)
                if found_row is not None:
                    break

        if found_row is None:
            logger.warning("Could not find item '%s' in bag — closing", item_name)
            self.injector.tap(*self.ITEM_BAG_CLOSE)
            time.sleep(1.0)
            return False

        # Step 3: Tap the "+" button for this item's row
        plus_y = self.ITEM_BAG_ROW_FIRST_Y + (found_row * self.ITEM_BAG_ROW_HEIGHT)
        logger.info(
            "Found '%s' at row %d — tapping + at (%d, %d)",
            item_name, found_row, self.ITEM_BAG_PLUS_X, plus_y,
        )
        self.injector.tap(self.ITEM_BAG_PLUS_X, plus_y)
        time.sleep(0.8)

        # Step 4: Tap "Confirm Use"
        logger.info("Tapping Confirm Use")
        self.injector.tap(*self.ITEM_BAG_CONFIRM)
        time.sleep(2.0)

        # Step 5: Handle the "Use training item(s)?" confirmation popup.
        # After Confirm Use, a second popup appears with Cancel / Use Training Items.
        # The green "Use Training Items" button is at ~(810, 1785).
        frame = capture.grab_frame()
        confirm_text = ocr.read_region(frame, (0, 1500, 1080, 1900)).lower()
        if "use" in confirm_text or "training item" in confirm_text:
            logger.info("Confirming 'Use training item(s)?' popup")
            self.injector.tap(810, 1785)
            time.sleep(2.0)

        # Step 6: Close the item bag so career home is visible for state reads
        logger.info("Closing item bag")
        self.injector.tap(*self.ITEM_BAG_CLOSE)
        time.sleep(1.5)

        logger.info("Item '%s' used successfully", item_name)
        return True

    def execute_item_batch(
        self,
        items: list[tuple[str, str]],
        capture: "CaptureBackend",
        ocr: "OCREngine",
    ) -> list[str]:
        """Use multiple items in a single bag session.

        Args:
            items: List of (item_key, item_name) tuples to use.
            capture: Screen capture backend
            ocr: OCR engine for reading item names

        Returns list of item_keys that were successfully selected.
        The caller should consume_item() and activate_item() for each.
        """
        if not items:
            return []

        # Step 1: Open item bag
        logger.info("Opening item bag for batch use (%d items)...", len(items))
        self.injector.tap(*self.ITEM_BAG_BTN)
        time.sleep(2.0)

        # Step 2: Find and tap + for each item
        selected = []
        for item_key, item_name in items:
            frame = capture.grab_frame()
            target_lower = item_name.lower()
            found_row = self._find_item_row(frame, ocr, target_lower)

            # Scroll and retry if not found
            if found_row is None:
                for scroll_attempt in range(3):
                    logger.info("Item '%s' not visible — scrolling (attempt %d)", item_name, scroll_attempt + 1)
                    self.injector.swipe(540, 1200, 540, 400, duration_ms=500)
                    time.sleep(0.8)
                    self.injector.tap(540, 350)  # halt scroll momentum
                    time.sleep(1.0)
                    frame = capture.grab_frame()
                    found_row = self._find_item_row(frame, ocr, target_lower)
                    if found_row is not None:
                        break

            if found_row is None:
                logger.warning("Could not find item '%s' in bag — skipping", item_name)
                continue

            plus_y = self.ITEM_BAG_ROW_FIRST_Y + (found_row * self.ITEM_BAG_ROW_HEIGHT)
            logger.info("Selecting '%s' at row %d — tapping + at (%d, %d)", item_name, found_row, self.ITEM_BAG_PLUS_X, plus_y)
            self.injector.tap(self.ITEM_BAG_PLUS_X, plus_y)
            time.sleep(0.8)
            selected.append(item_key)

        if not selected:
            logger.warning("No items selected — closing bag")
            self.injector.tap(*self.ITEM_BAG_CLOSE)
            time.sleep(1.0)
            return []

        # Step 3: Tap "Confirm Use" once for all selected items
        logger.info("Tapping Confirm Use for %d items", len(selected))
        self.injector.tap(*self.ITEM_BAG_CONFIRM)
        time.sleep(2.0)

        # Step 4: Handle the "Use training item(s)?" confirmation popup
        frame = capture.grab_frame()
        confirm_text = ocr.read_region(frame, (0, 1500, 1080, 1900)).lower()
        if "use" in confirm_text or "training item" in confirm_text:
            logger.info("Confirming 'Use training item(s)?' popup")
            self.injector.tap(810, 1785)
            time.sleep(2.0)

        # Step 5: Handle any additional confirmation popups and close bag
        # Sometimes there's a second confirmation or result popup
        for _ in range(2):
            frame = capture.grab_frame()
            popup_text = ocr.read_region(frame, (0, 1500, 1080, 1900)).lower()
            if "ok" in popup_text or "close" in popup_text:
                logger.info("Post-use popup detected — tapping OK/Close")
                self.injector.tap(540, 1785)
                time.sleep(1.5)
            else:
                break

        logger.info("Closing item bag")
        self.injector.tap(*self.ITEM_BAG_CLOSE)
        time.sleep(1.5)

        # Verify bag is actually closed
        frame = capture.grab_frame()
        header_text = ocr.read_region(frame, (0, 0, 400, 60)).lower()
        if "training item" in header_text or "item" in header_text:
            logger.warning("Item bag still open after Close — retrying")
            self.injector.tap(*self.ITEM_BAG_CLOSE)
            time.sleep(1.5)

        logger.info("Batch item use complete: %s", selected)
        return selected

    def _find_item_row(
        self,
        frame: "np.ndarray",
        ocr: "OCREngine",
        target_lower: str,
    ) -> int | None:
        """OCR visible item rows and return the row index matching target_lower.

        Returns None if not found.
        """
        from rapidfuzz import fuzz

        for row_idx in range(self.ITEM_BAG_VISIBLE_ROWS):
            row_center_y = self.ITEM_BAG_ROW_FIRST_Y + (row_idx * self.ITEM_BAG_ROW_HEIGHT)
            y_start = row_center_y - self.ITEM_BAG_NAME_REGION_H
            y_end = row_center_y + self.ITEM_BAG_NAME_REGION_H
            region = (
                self.ITEM_BAG_NAME_REGION_X[0],
                y_start,
                self.ITEM_BAG_NAME_REGION_X[1],
                y_end,
            )
            text = ocr.read_region(frame, region).lower()
            if not text.strip():
                continue

            # Check fuzzy match — item names are distinctive enough
            score = fuzz.partial_ratio(target_lower, text)
            logger.debug(
                "Item bag row %d: '%s' (match=%.0f%% vs '%s')",
                row_idx, text.strip(), score, target_lower,
            )
            if score >= 80:
                return row_idx

        return None

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

    # ------------------------------------------------------------------
    # Race list navigation
    # ------------------------------------------------------------------

    # Race list layout constants (1080x1920 portrait)
    _RACE_ROW_HEIGHT = 240
    _RACE_VISIBLE_ROWS = 2
    # After scrolling, row positions shift unpredictably. Instead of fixed
    # row Y offsets, we OCR overlapping horizontal slices and look for the
    # grade/distance/surface pattern in each slice.
    _RACE_SCAN_Y_START = 990
    _RACE_SCAN_Y_END = 1500
    _RACE_SCAN_SLICE_HEIGHT = 70
    _RACE_SCAN_STEP = 60

    def navigate_to_race(
        self,
        target_grade: str,
        target_distance: int,
        target_surface: str,
        target_name: str,
        estimated_position: int,
        capture: "CaptureBackend",
        ocr: "OCREngine",
    ) -> tuple[int, int] | None:
        """Scroll the race list to find the target race.

        Matches by grade + distance + surface from the detail line OCR,
        since race names are stylized images that don't OCR reliably.

        Returns (x, y) tap coordinates if found, None if not found.
        """
        max_scrolls = max(3, (estimated_position // self._RACE_VISIBLE_ROWS) + 2)

        for scroll in range(max_scrolls + 1):
            frame = capture.grab_frame()
            coords = self._find_race_in_frame(
                frame, ocr, target_grade, target_distance, target_surface,
                target_name,
            )
            if coords:
                return coords

            # Scroll down one page (~2 rows).
            # Use a slow swipe, tap to halt momentum, then wait.
            self.injector.swipe(540, 1300, 540, 1060, duration_ms=400)
            time.sleep(0.8)
            self.injector.tap(540, 350)  # halt scroll momentum
            time.sleep(1.0)

        logger.warning(
            "Could not find race '%s' (%s %dm %s) after %d scrolls",
            target_name, target_grade, target_distance, target_surface,
            max_scrolls,
        )
        return None

    def _find_race_in_frame(
        self,
        frame: "np.ndarray",
        ocr: "OCREngine",
        target_grade: str,
        target_distance: int,
        target_surface: str,
        target_name: str = "",
    ) -> tuple[int, int] | None:
        """Find a specific race on the race list screen.

        Strategy: OCR the full visible area in strips. For each strip,
        check if it contains the race name (primary) or the detail line
        grade/distance/surface (secondary). A race is confirmed when
        BOTH name and detail line are found near each other.
        """
        name_words = [w.lower() for w in target_name.split() if len(w) > 3]
        grade_lower = target_grade.lower()
        dist_str = f"{target_distance}m"
        surface_lower = target_surface.lower()

        # Pass 1: OCR all strips and collect name hits + detail hits
        name_hits = []    # y values where race name words appear
        detail_hits = []  # (y, y_end) where grade+dist+surface match

        y = self._RACE_SCAN_Y_START
        while y + self._RACE_SCAN_SLICE_HEIGHT <= self._RACE_SCAN_Y_END:
            y_end = y + self._RACE_SCAN_SLICE_HEIGHT
            region = (0, y, 1080, y_end)
            text = ocr.read_region(frame, region).strip()
            if not text:
                y += self._RACE_SCAN_STEP
                continue
            text_lower = text.lower()

            # Check for race name
            if name_words:
                matches = sum(1 for w in name_words if w in text_lower)
                if matches >= max(1, len(name_words) // 2):
                    name_hits.append((y, y_end, matches, text))

            # Check for detail line
            if grade_lower in text_lower and dist_str in text_lower and surface_lower in text_lower:
                detail_hits.append((y, y_end, text))

            y += self._RACE_SCAN_STEP

        # Pass 2: Match name hits to nearby detail hits (within 250px below)
        if name_words and name_hits:
            for ny, ny_end, n_matches, n_text in name_hits:
                for dy, dy_end, d_text in detail_hits:
                    # Detail line should be below or overlapping the name
                    if 0 <= dy - ny <= 250:
                        tap_y = (dy + dy_end) // 2
                        logger.info(
                            "Found race '%s': name at y=%d (%d/%d words), "
                            "detail at y=%d (OCR: '%s')",
                            target_name, ny, n_matches, len(name_words),
                            dy, d_text,
                        )
                        return (540, tap_y)

            # Name found but no nearby detail line — tap near name
            best = max(name_hits, key=lambda h: h[2])
            ny, ny_end, n_matches, n_text = best
            tap_y = ny_end + 60  # slightly below name banner
            logger.info(
                "Found race '%s' by name only at y=%d (%d/%d words, no detail line)",
                target_name, ny, n_matches, len(name_words),
            )
            return (540, tap_y)

        # Pass 3: No name match — fall back to detail line only
        if detail_hits:
            dy, dy_end, d_text = detail_hits[0]
            tap_y = (dy + dy_end) // 2
            logger.warning(
                "Race '%s' name not found — using detail match at y=%d (OCR: '%s')",
                target_name, dy, d_text,
            )
            return (540, tap_y)

        return None
