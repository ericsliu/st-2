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
    RACE_LIST_REGIONS,
    RACE_LIST_VISIBLE_SLOTS,
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
    RaceOption,
    Mood,
    ScreenState,
    SkillOption,
    StatType,
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
        # Ensure screen identifier has access to OCR
        if not screen_id._ocr:
            screen_id.set_ocr(ocr)

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
        elif screen == ScreenState.RACE_ENTRY:
            self._parse_race_list(frame, state)

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

        # Failure rate is read per-tile during scan_training_gains().
        # It changes per training type so we don't assign it here.

        # Parse stat gain previews and store on all tiles
        # (the gain preview shows what the selected tile would give)
        parsed_gains: dict[str, int] = {}
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
                    val = int(match.group(1))
                    parsed_gains[stat_type] = val
                    logger.debug("Gain preview %s: +%d", stat_type, val)

        # Don't pre-assign gains here — the FSM's scan_training_gains()
        # will tap each tile and OCR gains individually. Pre-assigning
        # caused the scorer to skip unscanned tiles.
        if parsed_gains:
            logger.debug(
                "Initial gain preview (selected tile only): %s", parsed_gains
            )

    def detect_selected_tile(self, frame: np.ndarray) -> int | None:
        """Detect which training tile is currently raised/selected.

        The selected tile is visually raised. We OCR the label region
        for the stat name to identify which tile it is.

        Returns the tile index (0-4) or None if detection fails.
        """
        region = STAT_SELECTION_REGIONS.get("selected_label")
        if not region:
            return None
        text = self.ocr.read_region(frame, region).lower()
        for keyword, idx in [
            ("speed", 0), ("stamina", 1), ("power", 2),
            ("guts", 3), ("wit", 4), ("wisdom", 4),
        ]:
            if keyword in text:
                return idx
        return None

    def read_stat_gains(self, frame: np.ndarray) -> dict[str, int]:
        """OCR the stat gain preview from a single frame.

        Returns {stat_name: gain_value} for all stats with visible gains.
        Called by the FSM during per-tile scanning.

        Tries two sources for redundancy:
        1. Bottom bar "+X" numbers above each stat column (gain_*)
        2. Right panel vertical gain list (panel_gain_*)
        Uses whichever source returns more stats.
        """
        # Source 1: bottom bar gain numbers
        bar_gains = self._read_gains_from_regions(frame, [
            ("speed", "gain_speed"),
            ("stamina", "gain_stamina"),
            ("power", "gain_power"),
            ("guts", "gain_guts"),
            ("wit", "gain_wit"),
        ])

        # Source 2: right panel gain list
        panel_gains = self._read_gains_from_regions(frame, [
            ("speed", "panel_gain_speed"),
            ("stamina", "panel_gain_stamina"),
            ("power", "panel_gain_power"),
            ("guts", "panel_gain_guts"),
            ("wit", "panel_gain_wit"),
        ])

        # Use whichever source got more results
        if len(panel_gains) > len(bar_gains):
            logger.debug("Using panel gains (%d stats) over bar gains (%d stats)",
                         len(panel_gains), len(bar_gains))
            gains = panel_gains
        else:
            gains = bar_gains

        # Merge: fill in any stats that only one source found
        for stat, val in panel_gains.items():
            if stat not in gains:
                gains[stat] = val
        for stat, val in bar_gains.items():
            if stat not in gains:
                gains[stat] = val

        return gains

    def _read_gains_from_regions(
        self,
        frame: np.ndarray,
        stat_region_keys: list[tuple[str, str]],
    ) -> dict[str, int]:
        """Read stat gain values from a list of (stat_name, region_key) pairs.

        Uses read_gain_region (gain-aware '+N' parsing) to handle the '+'
        symbol being misread as '4' or '$' by Apple Vision OCR.
        """
        gains: dict[str, int] = {}
        for stat_name, key in stat_region_keys:
            region = STAT_SELECTION_REGIONS.get(key)
            if not region:
                continue
            val = self.ocr.read_gain_region(frame, region)
            if val is not None and 0 < val <= 500:
                gains[stat_name] = val
        return gains

    def read_failure_rate(self, frame: np.ndarray) -> float | None:
        """OCR the failure rate percentage for the currently selected tile.

        Returns the failure rate as a fraction (0.0 to 1.0), or None on failure.
        """
        region = STAT_SELECTION_REGIONS.get("failure_rate")
        if not region:
            return None
        text = self.ocr.read_region(frame, region)
        match = re.search(r"(\d+)\s*%", text)
        if match:
            return int(match.group(1)) / 100.0
        return None

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
    # Race list screen
    # ------------------------------------------------------------------

    def _parse_race_list(self, frame: np.ndarray, state: GameState) -> None:
        """Parse visible races from the race list screen.

        OCRs race name and detail text for each visible slot.  The detail
        line typically contains grade, distance, and surface info
        (e.g. "G1 | 2400m | Turf").
        """
        races: list[RaceOption] = []

        for i in range(RACE_LIST_VISIBLE_SLOTS):
            name_key = f"race_{i}_name"
            detail_key = f"race_{i}_detail"
            tap_key = f"race_{i}_tap"

            name_region = RACE_LIST_REGIONS.get(name_key)
            detail_region = RACE_LIST_REGIONS.get(detail_key)
            tap_region = RACE_LIST_REGIONS.get(tap_key)

            if name_region is None:
                continue

            name_text = self.ocr.read_region(frame, name_region).strip()
            if not name_text:
                continue  # Empty slot = no more races visible

            race = RaceOption(
                name=name_text,
                position=i,
                tap_coords=get_tap_center(tap_region) if tap_region else (0, 0),
            )

            # The name region contains "G1 Venue Surface Distancem (Category)..."
            # Parse grade, distance, surface from it directly
            self._parse_race_detail(name_text, race)

            # Also try the detail region for additional info
            if detail_region:
                detail_text = self.ocr.read_region(frame, detail_region).strip()
                if detail_text:
                    self._parse_race_detail(detail_text, race)

            # Check if this is a career goal race
            for goal in state.career_goals:
                if goal.race_name and not goal.completed:
                    if goal.race_name.lower() in name_text.lower():
                        race.is_goal_race = True

            races.append(race)
            logger.debug(
                "Race slot %d: '%s' (%s, %dm, %s)",
                i, race.name, race.grade, race.distance, race.surface,
            )

        state.available_races = races

    @staticmethod
    def _parse_race_detail(text: str, race: RaceOption) -> None:
        """Extract grade, distance, and surface from detail text.

        Expected formats: "G1 | 2400m | Turf", "G2 2200m Dirt", etc.
        """
        # Grade
        grade_match = re.search(r"(G[123]|OP|Pre-OP)", text, re.IGNORECASE)
        if grade_match:
            race.grade = grade_match.group(1).upper()

        # Distance
        dist_match = re.search(r"(\d{4,5})\s*m", text)
        if dist_match:
            race.distance = int(dist_match.group(1))

        # Surface
        text_lower = text.lower()
        if "dirt" in text_lower:
            race.surface = "dirt"
        elif "turf" in text_lower:
            race.surface = "turf"

    # ------------------------------------------------------------------
    # Common parsers
    # ------------------------------------------------------------------

    def _parse_stats(
        self,
        frame: np.ndarray,
        regions: dict[str, tuple[int, int, int, int]],
        state: GameState,
    ) -> None:
        """OCR the 5 stat values.

        Reads the full stat block (labels + values + /max) as one region,
        then parses individual stat values from the recognized text.
        Apple Vision reads the gradient game font much more accurately when
        it has the stat label text as context alongside the numbers.
        """
        stats = TraineeStats()

        # Try bulk OCR first — read the full stat block as one unit
        bulk_parsed = self._parse_stats_bulk(frame, regions)
        if bulk_parsed and sum(1 for v in bulk_parsed.values() if v > 0) >= 3:
            for stat_type in StatType:
                val = bulk_parsed.get(stat_type.value, 0)
                if 0 <= val <= 9999:
                    setattr(stats, stat_type.value, val)
            state.stats = stats
            return

        # Fallback: OCR each stat individually
        for stat_type, region_key in STAT_REGION_KEYS.items():
            region = regions.get(region_key)
            if region is None:
                continue
            value = self.ocr.read_number_region(frame, region)
            if value is not None and 0 <= value <= 9999:
                setattr(stats, stat_type.value, value)

        state.stats = stats

    def _parse_stats_bulk(
        self,
        frame: np.ndarray,
        regions: dict[str, tuple[int, int, int, int]],
    ) -> dict[str, int] | None:
        """Parse all stat values from a single OCR pass on the full stat block.

        The game's gradient-colored italic font is poorly recognized when each
        stat is cropped individually. Reading the full block with stat labels
        provides context that dramatically improves accuracy.
        """
        # Determine the bounding box of the full stat block
        # Includes labels row + values + /max text
        stat_regions = [
            regions.get(f"stat_{s.value}") for s in StatType
        ]
        stat_regions = [r for r in stat_regions if r is not None]
        if not stat_regions:
            return None

        # Build a bounding box that covers labels + values + max.
        # Only extend 15px above to capture stat labels but NOT the gain
        # preview numbers (+N) which sit above at y≈1185-1255 on the
        # training screen. Including gains causes OCR to merge "+5" with
        # "148" → "1481".
        x1 = min(r[0] for r in stat_regions) - 80  # include grade badges for context
        y1 = min(r[1] for r in stat_regions) - 15   # labels only, not gain previews
        x2 = max(r[2] for r in stat_regions) + 20
        y2 = max(r[3] for r in stat_regions) + 50   # include /1200

        # Clamp to frame bounds
        h, w = frame.shape[:2]
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)

        text = self.ocr.read_region(frame, (x1, y1, x2, y2))
        if not text:
            return None

        # Parse stat values from the recognized text.
        # Apple Vision returns segments in reading order but may merge labels
        # across columns (e.g. "Stamina l Power 285:").  The stat values
        # always appear in left-to-right order: Speed, Stamina, Power, Guts, Wit.
        # Strategy: extract all standalone 2-4 digit numbers (not part of /1200),
        # then assign them to stats in order.
        result: dict[str, int] = {}

        # Remove "/1200" patterns so "1200" isn't picked up as a stat value
        cleaned = re.sub(r'/\s*\d{3,4}', '', text)

        # Find all 2-4 digit numbers (stat values range from ~50 to ~2000)
        numbers = [int(m.group()) for m in re.finditer(r'(?<!\d)\d{2,4}(?!\d)', cleaned)]
        # Filter to plausible stat values
        numbers = [n for n in numbers if 10 <= n <= 2000]

        stat_keys = ["speed", "stamina", "power", "guts", "wit"]
        for i, val in enumerate(numbers):
            if i < len(stat_keys):
                result[stat_keys[i]] = val
                logger.debug("Bulk stat parse: %s = %d", stat_keys[i], val)

        return result if result else None

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

        The energy bar is a rainbow gradient (blue→cyan→green→yellow→orange→red).
        Three visual zones when a training tile is selected:
        - Bright rainbow (S>150, V>200): energy remaining after training
        - Faded gradient (S>150, V~170): energy cost preview
        - Gray (S<20, V~142): energy already missing

        For Wit/recovery training, a 4th zone appears:
        - Recovery preview (S~70, V>200): energy to be regained

        Current energy = bright + faded (everything with S > 100).
        The recovery preview (S~70) is excluded so it doesn't inflate
        the reading.
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

        bar_width = x2 - x1
        col_sat = np.mean(hsv[:, :, 1], axis=0)
        col_val = np.mean(hsv[:, :, 2], axis=0)

        # Zone classification by saturation + value:
        #   Bright fill:      S > 100, V > 200  (post-training energy)
        #   Faded cost:       S > 100, V <= 200  (energy to be consumed)
        #   Recovery preview: 20 < S <= 100       (energy to be regained)
        #   Gray/empty:       S <= 20             (missing energy)
        bright = int(np.sum((col_sat > 100) & (col_val > 200)))
        faded = int(np.sum((col_sat > 100) & (col_val <= 200)))
        recovery = int(np.sum((col_sat > 20) & (col_sat <= 100)))

        # Current energy = bright + faded (everything with real fill)
        filled_cols = bright + faded
        energy = int(round(filled_cols / bar_width * 100))
        state.energy = max(0, min(100, energy))

        # Post-training = bright only (excludes faded cost preview)
        if faded > 0:
            post = int(round(bright / bar_width * 100))
            state.energy_post_training = max(0, min(100, post))
        else:
            state.energy_post_training = None

        # Recovery amount (Wit-type training extends bar with low-sat fill)
        state.energy_recovery = int(round(recovery / bar_width * 100))

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

        # Try "12 turn(s) left" format — game shows turns remaining.
        # Convert to absolute turn number: max_turns - turns_left.
        match = re.search(r"(\d+)\s*turn", text, re.IGNORECASE)
        if match:
            turns_left = int(match.group(1))
            state.current_turn = state.max_turns - turns_left
            return

        # Fallback: first number found (assume turns left)
        match = re.search(r"\d+", text)
        if match:
            turns_left = int(match.group())
            state.current_turn = state.max_turns - turns_left
