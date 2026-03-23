"""State assembler: combines YOLO detections and OCR into a GameState."""

from __future__ import annotations

import logging
import time

import numpy as np

from uma_trainer.config import AppConfig
from uma_trainer.perception.class_map import (
    MOOD_CLASS_TO_MOOD,
    STAT_BOX_TO_STAT,
    TRAIN_BTN_TO_STAT,
)
from uma_trainer.perception.detector import Detection, YOLODetector
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.types import (
    EventChoice,
    GameState,
    Mood,
    ScreenState,
    SkillOption,
    StatType,
    TraineeStats,
    TrainingTile,
)

logger = logging.getLogger(__name__)

# Fixed stat box regions on the 1280×720 normalized frame (x1, y1, x2, y2).
# These are approximate and should be calibrated against real screenshots.
# They are used as a fallback when YOLO doesn't detect a stat_box class.
FALLBACK_STAT_REGIONS: dict[StatType, tuple[int, int, int, int]] = {
    StatType.SPEED:   (40,  80, 120, 110),
    StatType.STAMINA: (40, 115, 120, 145),
    StatType.POWER:   (40, 150, 120, 180),
    StatType.GUTS:    (40, 185, 120, 215),
    StatType.WIT:     (40, 220, 120, 250),
}

# Approximate Y-center for the 5 training tiles row (used to filter detections)
TILE_ROW_Y_MIN = 420
TILE_ROW_Y_MAX = 680


class StateAssembler:
    """Assembles a GameState from raw YOLO detections and OCR on the current frame."""

    def __init__(
        self,
        detector: YOLODetector,
        ocr: OCREngine,
        config: AppConfig,
    ) -> None:
        self.detector = detector
        self.ocr = ocr
        self.config = config

    def assemble(self, frame: np.ndarray) -> GameState:
        """Full pipeline: detect → infer screen → parse screen-specific data."""
        t0 = time.monotonic()
        detections = self.detector.detect(frame)
        screen = self.detector.detect_screen_state(detections)

        state = GameState(
            screen=screen,
            raw_detections=detections,
            timestamp=time.time(),
        )

        if screen == ScreenState.TRAINING:
            self._parse_training_screen(frame, detections, state)
        elif screen == ScreenState.EVENT:
            self._parse_event_screen(frame, detections, state)
        elif screen == ScreenState.SKILL_SHOP:
            self._parse_skill_shop(frame, detections, state)

        # Stats and turn counter are visible on most screens
        if screen in (ScreenState.TRAINING, ScreenState.EVENT, ScreenState.SKILL_SHOP):
            self._parse_stats(frame, detections, state)
            self._parse_mood(detections, state)
            self._parse_energy(frame, detections, state)
            self._parse_turn(frame, detections, state)

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
    # Screen-specific parsers
    # ------------------------------------------------------------------

    def _parse_training_screen(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        state: GameState,
    ) -> None:
        """Parse the main training selection screen."""
        state.training_tiles = self._parse_training_tiles(frame, detections)

    def _parse_event_screen(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        state: GameState,
    ) -> None:
        """Parse event text and choices from the event popup screen."""
        # Event popup region (approximate for 1280×720)
        event_popup = self.detector.get_best(detections, "event_popup")
        if event_popup:
            # OCR the top portion of the popup for event description text
            x1, y1, x2, y2 = event_popup.bbox
            text_region = (x1, y1, x2, y1 + (y2 - y1) // 2)
            state.event_text = self.ocr.read_region(frame, text_region)
        else:
            # Fallback: upper-center region
            state.event_text = self.ocr.read_region(frame, (200, 100, 1080, 400))

        # Parse choice buttons
        choices: list[EventChoice] = []
        for i in range(3):
            choice_det = self.detector.get_best(detections, f"event_choice_{i}")
            if choice_det:
                choice_text = self.ocr.read_region(frame, choice_det.bbox)
                choices.append(
                    EventChoice(
                        index=i,
                        text=choice_text,
                        tap_coords=choice_det.center,
                    )
                )

        # Fallback: derive choice positions from popup bounds
        if not choices and event_popup:
            x1, y1, x2, y2 = event_popup.bbox
            popup_h = y2 - y1
            num_choices = 2
            for i in range(num_choices):
                cy = y1 + popup_h * (0.6 + i * 0.2)
                region = (x1 + 20, int(cy) - 20, x2 - 20, int(cy) + 20)
                text = self.ocr.read_region(frame, region)
                choices.append(
                    EventChoice(
                        index=i,
                        text=text,
                        tap_coords=(int((x1 + x2) / 2), int(cy)),
                    )
                )

        state.event_choices = choices

    def _parse_skill_shop(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        state: GameState,
    ) -> None:
        """Parse available skills and their costs from the skill shop screen."""
        skill_cards = self.detector.filter_by_class(detections, "skill_card")
        skills: list[SkillOption] = []

        for i, card in enumerate(skill_cards[:8]):  # Cap at 8 skills
            x1, y1, x2, y2 = card.bbox

            # OCR skill name (top portion of card)
            name_region = (x1, y1, x2, y1 + (y2 - y1) // 2)
            name = self.ocr.read_region(frame, name_region)

            # OCR cost (bottom portion of card)
            cost_region = (x1, y1 + (y2 - y1) // 2, x2, y2)
            cost = self.ocr.read_number_region(frame, cost_region) or 0

            skills.append(
                SkillOption(
                    skill_id=f"unknown_{i}",
                    name=name,
                    cost=cost,
                    tap_coords=card.center,
                )
            )

        state.available_skills = skills

    # ------------------------------------------------------------------
    # Common parsers (stats, mood, energy, turn)
    # ------------------------------------------------------------------

    def _parse_stats(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        state: GameState,
    ) -> None:
        """OCR the 5 stat values."""
        stats = TraineeStats()

        for class_name, stat_type in STAT_BOX_TO_STAT.items():
            det = self.detector.get_best(detections, class_name)
            if det:
                value = self.ocr.read_number_region(frame, det.bbox)
            else:
                # Use fallback fixed regions
                value = self.ocr.read_number_region(
                    frame, FALLBACK_STAT_REGIONS[stat_type]
                )

            if value is not None and 0 <= value <= 9999:
                setattr(stats, stat_type.value, value)

        state.stats = stats

    def _parse_mood(
        self, detections: list[Detection], state: GameState
    ) -> None:
        """Detect mood from mood icon class detections."""
        for class_name, mood in MOOD_CLASS_TO_MOOD.items():
            if self.detector.get_best(detections, class_name) is not None:
                state.mood = mood
                return
        state.mood = Mood.NORMAL

    def _parse_energy(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        state: GameState,
    ) -> None:
        """Parse current energy value (0–100)."""
        det = self.detector.get_best(detections, "energy_bar")
        if det:
            # Energy bar: OCR the number to the right of the bar
            x1, y1, x2, y2 = det.bbox
            # Number is typically to the right of the bar graphic
            num_region = (x2, y1, x2 + 60, y2)
            value = self.ocr.read_number_region(frame, num_region)
            if value is not None and 0 <= value <= 100:
                state.energy = value

    def _parse_turn(
        self,
        frame: np.ndarray,
        detections: list[Detection],
        state: GameState,
    ) -> None:
        """Parse current turn number (e.g. '12/72')."""
        det = self.detector.get_best(detections, "turn_counter")
        if det:
            text = self.ocr.read_region(frame, det.bbox)
        else:
            # Fallback: top-right corner region
            text = self.ocr.read_region(frame, (1100, 10, 1270, 50))

        # Parse "12/72" or "12" format
        import re
        match = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if match:
            state.current_turn = int(match.group(1))
            state.max_turns = int(match.group(2))
        else:
            match = re.search(r"\d+", text)
            if match:
                state.current_turn = int(match.group())

    def _parse_training_tiles(
        self, frame: np.ndarray, detections: list[Detection]
    ) -> list[TrainingTile]:
        """Build TrainingTile list from YOLO detections."""
        tiles: list[TrainingTile] = []

        # Find training buttons (one per tile)
        tile_buttons: list[Detection] = []
        for class_name in TRAIN_BTN_TO_STAT:
            btn = self.detector.get_best(detections, class_name)
            if btn:
                tile_buttons.append(btn)

        # Sort left to right by x-center
        tile_buttons.sort(key=lambda d: d.center[0])

        for i, btn in enumerate(tile_buttons):
            stat_type = TRAIN_BTN_TO_STAT[btn.class_name]

            # Expand the tile region upward to capture indicators above the button
            x1, y1, x2, y2 = btn.bbox
            tile_region_y1 = max(0, y1 - 200)
            tile_bbox = (x1, tile_region_y1, x2, y2)

            # Check for indicators within this tile's column
            is_rainbow = self._has_class_in_region(detections, "indicator_rainbow", tile_bbox)
            is_gold = self._has_class_in_region(detections, "indicator_gold", tile_bbox)
            has_hint = self._has_class_in_region(detections, "indicator_hint", tile_bbox)
            has_director = self._has_class_in_region(detections, "indicator_director", tile_bbox)

            # Count support cards in this tile column
            support_cards: list[str] = []
            for slot_i in range(6):
                slot_det = self.detector.get_best(detections, f"support_card_slot_{slot_i}")
                if slot_det and slot_det.contains_point(*btn.center):
                    support_cards.append(f"slot_{slot_i}")

            tiles.append(
                TrainingTile(
                    stat_type=stat_type,
                    support_cards=support_cards,
                    is_rainbow=is_rainbow,
                    is_gold=is_gold,
                    has_hint=has_hint,
                    has_director=has_director,
                    position=i,
                    tap_coords=btn.center,
                )
            )

        return tiles

    def _has_class_in_region(
        self,
        detections: list[Detection],
        class_name: str,
        region: tuple[int, int, int, int],
    ) -> bool:
        """Return True if any detection of class_name has its center within region."""
        rx1, ry1, rx2, ry2 = region
        for det in detections:
            if det.class_name == class_name:
                cx, cy = det.center
                if rx1 <= cx <= rx2 and ry1 <= cy <= ry2:
                    return True
        return False
