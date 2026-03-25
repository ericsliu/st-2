"""State assembler: combines screen identification, fixed-region OCR,
and pixel analysis into a GameState.

Replaces the previous YOLO-based assembler with deterministic coordinate
lookups at the canonical 1080×1920 portrait resolution.
"""

from __future__ import annotations

import logging
import re
import time

import numpy as np

from uma_trainer.config import AppConfig
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.pixel_analysis import (
    count_support_cards,
    detect_mood,
    detect_mood_from_text,
    detect_training_indicators,
)
from uma_trainer.perception.regions import (
    EVENT_REGIONS,
    STAT_REGION_KEYS,
    STAT_SELECTION_REGIONS,
    TILE_INDEX_TO_STAT,
    TRAINING_TILES,
    TURN_ACTION_REGIONS,
    get_tap_center,
)
from uma_trainer.perception.screen_identifier import ScreenIdentifier
from uma_trainer.types import (
    EventChoice,
    GameState,
    Mood,
    ScreenState,
    SkillOption,
    TraineeStats,
    TrainingTile,
)

logger = logging.getLogger(__name__)


class StateAssembler:
    """Assembles a GameState from screen identification + OCR on fixed regions."""

    def __init__(
        self,
        screen_id: ScreenIdentifier,
        ocr: OCREngine,
        config: AppConfig,
    ) -> None:
        self.screen_id = screen_id
        self.ocr = ocr
        self.config = config

    def assemble(self, frame: np.ndarray) -> GameState:
        """Full pipeline: identify screen → OCR fixed regions → build state."""
        t0 = time.monotonic()

        screen = self.screen_id.identify(frame)

        # Distinguish stat selection sub-screen from main turn action
        is_stat_select = False
        if screen == ScreenState.TRAINING:
            is_stat_select = self.screen_id.is_stat_selection(frame)

        state = GameState(
            screen=screen,
            timestamp=time.time(),
        )

        if screen == ScreenState.TRAINING:
            if is_stat_select:
                self._parse_stat_selection(frame, state)
            else:
                self._parse_turn_action(frame, state)
        elif screen == ScreenState.EVENT:
            self._parse_event_screen(frame, state)
        elif screen == ScreenState.SKILL_SHOP:
            self._parse_skill_shop(frame, state)

        # Stats, mood, energy, and turn are visible on most screens
        if screen in (
            ScreenState.TRAINING,
            ScreenState.EVENT,
            ScreenState.SKILL_SHOP,
        ):
            regions = (
                STAT_SELECTION_REGIONS if is_stat_select else TURN_ACTION_REGIONS
            )
            self._parse_stats(frame, regions, state)
            self._parse_mood(frame, regions, state)
            self._parse_energy(frame, regions, state)
            self._parse_turn(frame, regions, state)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.debug(
            "Assembled state: screen=%s energy=%d turn=%d/%d [%.1fms]",
            state.screen.value,
            state.energy,
            state.current_turn,
            state.max_turns,
            elapsed_ms,
        )
        return state

    # ------------------------------------------------------------------
    # Turn action screen (main career menu)
    # ------------------------------------------------------------------

    def _parse_turn_action(self, frame: np.ndarray, state: GameState) -> None:
        """Parse the main turn action screen (Rest/Training/Skills/etc.)."""
        # Nothing screen-specific beyond stats/mood/energy/turn (parsed in assemble)
        pass

    # ------------------------------------------------------------------
    # Training stat selection screen
    # ------------------------------------------------------------------

    def _parse_stat_selection(self, frame: np.ndarray, state: GameState) -> None:
        """Parse the 5 training tiles and failure rate."""
        state.training_tiles = self._parse_training_tiles(frame)

        # Parse failure rate
        region = STAT_SELECTION_REGIONS.get("failure_rate")
        if region:
            text = self.ocr.read_region(frame, region)
            match = re.search(r"(\d+)\s*%", text)
            if match:
                rate = int(match.group(1))
                # Apply failure rate to all tiles (it's for the selected tile)
                for tile in state.training_tiles:
                    tile.failure_rate = rate / 100.0

        # Parse stat gain previews
        for stat_type, key_prefix in [
            ("speed", "gain_speed"),
            ("stamina", "gain_stamina"),
            ("power", "gain_power"),
            ("guts", "gain_guts"),
            ("wit", "gain_wit"),
        ]:
            region = STAT_SELECTION_REGIONS.get(key_prefix)
            if region:
                text = self.ocr.read_region(frame, region)
                match = re.search(r"\+?\s*(\d+)", text)
                if match:
                    logger.debug("Gain preview %s: +%s", stat_type, match.group(1))

    def _parse_training_tiles(self, frame: np.ndarray) -> list[TrainingTile]:
        """Build TrainingTile list from fixed tile regions."""
        tiles: list[TrainingTile] = []

        for i, tile_region in enumerate(TRAINING_TILES):
            stat_type = TILE_INDEX_TO_STAT[i]

            # Detect indicators (rainbow/gold/hint/director)
            indicators = detect_training_indicators(frame, tile_region.indicator)

            # Count support cards on this tile
            card_count = count_support_cards(frame, tile_region.support_cards)
            support_cards = [f"card_{j}" for j in range(card_count)]

            tiles.append(
                TrainingTile(
                    stat_type=stat_type,
                    support_cards=support_cards,
                    is_rainbow=indicators["is_rainbow"],
                    is_gold=indicators["is_gold"],
                    has_hint=indicators["has_hint"],
                    has_director=indicators["has_director"],
                    position=i,
                    tap_coords=get_tap_center(tile_region.tap_target),
                )
            )

        return tiles

    # ------------------------------------------------------------------
    # Event screen
    # ------------------------------------------------------------------

    def _parse_event_screen(self, frame: np.ndarray, state: GameState) -> None:
        """Parse event text and choices."""
        # OCR the event description
        region = EVENT_REGIONS.get("event_text")
        if region:
            state.event_text = self.ocr.read_region(frame, region)

        # Parse choice buttons
        choices: list[EventChoice] = []
        for i in range(3):
            key = f"choice_{i}"
            region = EVENT_REGIONS.get(key)
            if region is None:
                continue
            text = self.ocr.read_region(frame, region)
            if text.strip():
                choices.append(
                    EventChoice(
                        index=i,
                        text=text.strip(),
                        tap_coords=get_tap_center(region),
                    )
                )

        state.event_choices = choices

    # ------------------------------------------------------------------
    # Skill shop
    # ------------------------------------------------------------------

    def _parse_skill_shop(self, frame: np.ndarray, state: GameState) -> None:
        """Parse available skills from the skill shop screen.

        TODO: The skill list is scrollable so fixed regions only capture
        the visible portion.  For now we parse what's visible.
        """
        # Parse available skill points
        from uma_trainer.perception.regions import SKILL_SHOP_REGIONS

        pts_region = SKILL_SHOP_REGIONS.get("skill_pts")
        if pts_region:
            pts = self.ocr.read_number_region(frame, pts_region)
            if pts is not None:
                logger.debug("Skill points available: %d", pts)

        # Individual skill parsing requires scrollable list handling
        # which is deferred to a later phase.
        state.available_skills = []

    # ------------------------------------------------------------------
    # Common parsers
    # ------------------------------------------------------------------

    def _parse_stats(
        self,
        frame: np.ndarray,
        regions: dict[str, tuple[int, int, int, int]],
        state: GameState,
    ) -> None:
        """OCR the 5 stat values from fixed regions."""
        stats = TraineeStats()

        for stat_type, region_key in STAT_REGION_KEYS.items():
            region = regions.get(region_key)
            if region is None:
                continue
            value = self.ocr.read_number_region(frame, region)
            if value is not None and 0 <= value <= 9999:
                setattr(stats, stat_type.value, value)

        state.stats = stats

    def _parse_mood(
        self,
        frame: np.ndarray,
        regions: dict[str, tuple[int, int, int, int]],
        state: GameState,
    ) -> None:
        """Detect mood from the mood indicator region."""
        # Try OCR on the mood text label first (e.g. "NORMAL")
        mood_region = regions.get("mood_label")
        if mood_region:
            text = self.ocr.read_region(frame, mood_region)
            mood = detect_mood_from_text(text)
            if mood != Mood.NORMAL or "NORMAL" in text.upper():
                state.mood = mood
                return

        # Fall back to pixel colour analysis
        mood_icon_region = regions.get("mood_icon")
        if mood_icon_region:
            state.mood = detect_mood(frame, mood_icon_region)
        else:
            state.mood = Mood.NORMAL

    def _parse_energy(
        self,
        frame: np.ndarray,
        regions: dict[str, tuple[int, int, int, int]],
        state: GameState,
    ) -> None:
        """Parse energy from the energy bar region.

        The energy bar is a coloured bar; we estimate energy as the
        proportion of the bar that is filled (coloured vs grey).
        """
        region = regions.get("energy_bar")
        if region is None:
            return

        x1, y1, x2, y2 = region
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return

        try:
            import cv2
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        except ImportError:
            return

        # The filled portion of the energy bar is saturated (coloured).
        # The empty portion is grey (low saturation).
        bar_width = x2 - x1
        sat = hsv[:, :, 1]
        # Average saturation per column
        col_sat = np.mean(sat, axis=0)
        # Count columns above a saturation threshold as "filled"
        filled_cols = int(np.sum(col_sat > 50))
        energy = int(round(filled_cols / bar_width * 100))
        state.energy = max(0, min(100, energy))

    def _parse_turn(
        self,
        frame: np.ndarray,
        regions: dict[str, tuple[int, int, int, int]],
        state: GameState,
    ) -> None:
        """Parse the turn counter (e.g. '12 turn(s) left')."""
        region = regions.get("turn_counter")
        if region is None:
            return

        text = self.ocr.read_region(frame, region)

        # Try "12 turn(s) left" format
        match = re.search(r"(\d+)\s*turn", text, re.IGNORECASE)
        if match:
            state.current_turn = int(match.group(1))
            return

        # Fallback: first number found
        match = re.search(r"\d+", text)
        if match:
            state.current_turn = int(match.group())
