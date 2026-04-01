"""High-level game action execution.

Extracted from scripts/do_one_turn.py — these are the multi-step UI flows
for training, racing, resting, shopping, skill buying, etc. Both the
single-turn script and the FSM use this shared implementation.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from uma_trainer.types import (
    ActionType,
    BotAction,
    Condition,
    GameState,
    ScreenState,
)

if TYPE_CHECKING:
    from uma_trainer.action.input_injector import InputInjector
    from uma_trainer.action.sequences import ActionSequences
    from uma_trainer.decision.strategy import DecisionEngine
    from uma_trainer.perception.assembler import StateAssembler
    from uma_trainer.perception.ocr import OCREngine
    from uma_trainer.perception.screen_identifier import ScreenIdentifier
    from uma_trainer.state.provider import GameStateProvider

logger = logging.getLogger(__name__)

# Post-action screen handling constants
POST_RACE_TAP_DELAY = 2.0
MAX_POST_ACTION_SCREENS = 20


class GameActionExecutor:
    """Executes high-level game actions (training, racing, resting, etc.).

    Encapsulates the multi-step UI flows that involve multiple taps,
    screen transitions, and OCR reads. Both the single-turn script and
    the FSM delegate to this class for action execution.
    """

    def __init__(
        self,
        injector: "InputInjector",
        sequences: "ActionSequences",
        provider: "GameStateProvider",
        assembler: "StateAssembler",
        screen_id: "ScreenIdentifier",
        ocr: "OCREngine",
    ) -> None:
        self.injector = injector
        self.sequences = sequences
        self.provider = provider
        self.assembler = assembler
        self.screen_id = screen_id
        self.ocr = ocr
        self.goal_race_urgent: bool = False

    # ------------------------------------------------------------------
    # Post-action screen navigation
    # ------------------------------------------------------------------

    def wait_for_career_home(
        self,
        engine: "DecisionEngine | None" = None,
        max_screens: int = MAX_POST_ACTION_SCREENS,
    ) -> GameState | None:
        """Tap through post-action screens until we're back on career home.

        Handles: post-race flow, events, popups, training results,
        shop refresh notifications, TS Climax ranking, Try Again, etc.

        Returns the GameState once on career home, or None if safety limit hit.
        """
        last_screen = None
        post_race_repeat = 0
        last_race_placement = None
        used_try_again = False

        for i in range(max_screens):
            time.sleep(POST_RACE_TAP_DELAY)
            frame = self.provider.refresh_frame()
            state = self.assembler.assemble(frame)
            is_stat_select = self.screen_id.is_stat_selection(frame)

            logger.info("Post-action screen %d: %s (stat_sel=%s)",
                        i + 1, state.screen.value, is_stat_select)
            prev_screen = last_screen
            last_screen = state.screen

            # Career home = TRAINING screen but NOT stat selection
            if state.screen == ScreenState.TRAINING and not is_stat_select:
                logger.info("Back on career home")
                return state

            # Event screen — use EventHandler
            if state.screen == ScreenState.EVENT:
                if engine is not None and state.event_text:
                    action = engine.event_handler.decide(state)
                    choice_idx = int(action.target) if action.target.isdigit() else 0
                    logger.info("Event: '%s' → choice %d (%s)",
                                state.event_text[:60], choice_idx, action.reason)
                    self.injector.tap(*action.tap_coords)
                else:
                    logger.info("Event screen (no handler/text) — picking choice 1")
                    self.injector.tap(540, 1100)
                continue

            # Warning popup
            if state.screen == ScreenState.WARNING_POPUP:
                from uma_trainer.perception.regions import WARNING_POPUP_REGIONS, get_tap_center
                ok_btn = get_tap_center(WARNING_POPUP_REGIONS["btn_ok"])
                logger.info("Warning popup — tapping OK")
                self.injector.tap(*ok_btn)
                continue

            # Shop screen — could be popup or full shop
            if state.screen == ScreenState.SKILL_SHOP:
                # Check header to distinguish full shop from popup
                header_text = self.ocr.read_region(frame, (0, 0, 300, 80)).lower()
                if "shop" in header_text:
                    logger.info("Full shop screen — tapping Back to exit")
                    self.injector.tap(50, 1870)
                else:
                    logger.info("Shop popup — tapping Cancel to dismiss")
                    self.injector.tap(270, 1360)
                time.sleep(1.5)
                continue

            # Pre-race screen — tap View Results
            if state.screen == ScreenState.PRE_RACE:
                logger.info("Pre-race screen — tapping View Results")
                self.injector.tap(380, 1760)
                time.sleep(3.0)
                continue

            # Post-race screen
            if state.screen == ScreenState.POST_RACE:
                if prev_screen == ScreenState.POST_RACE:
                    post_race_repeat += 1
                else:
                    post_race_repeat = 0

                if post_race_repeat >= 2:
                    logger.info("Post-race stuck (%d repeats) — trying centered Next", post_race_repeat)
                    self.injector.tap(540, 1640)
                elif last_race_placement is not None:
                    from uma_trainer.perception.regions import POST_RACE_REGIONS, get_tap_center
                    if last_race_placement == 1:
                        opt = get_tap_center(POST_RACE_REGIONS["option_2"])
                        logger.info("Post-race 1st place — tapping option 2 at %s", opt)
                    else:
                        opt = get_tap_center(POST_RACE_REGIONS["option_1"])
                        logger.info("Post-race %s place — tapping option 1 at %s", last_race_placement, opt)
                    self.injector.tap(*opt)
                    time.sleep(1.0)
                    self.injector.tap(765, 1760)
                    last_race_placement = None
                else:
                    logger.info("Post-race screen — tapping Next")
                    self.injector.tap(765, 1760)
                continue

            # Race list — tap Back
            if state.screen == ScreenState.RACE_ENTRY:
                logger.info("Race list — tapping Back")
                self.injector.tap(75, 1870)
                continue

            # Result screen — detect placement
            if state.screen == ScreenState.RESULT_SCREEN:
                from uma_trainer.perception.regions import POST_RACE_REGIONS
                placement_region = POST_RACE_REGIONS["placement"]
                placement_text = self.ocr.read_region(frame, placement_region).lower()
                if "1st" in placement_text:
                    last_race_placement = 1
                    logger.info("Result screen — placement: 1st")
                elif any(p in placement_text for p in ("2nd", "3rd", "4th", "5th", "6th")):
                    pm = re.search(r"(\d+)", placement_text)
                    last_race_placement = int(pm.group(1)) if pm else 2
                    logger.info("Result screen — placement: %s", last_race_placement)
                logger.info("Result screen — tapping to advance")
                self.injector.tap(540, 960)
                time.sleep(1.0)
                self.injector.tap(540, 1675)
                continue

            # Loading / cutscene / race — just wait
            if state.screen in (ScreenState.LOADING, ScreenState.CUTSCENE, ScreenState.RACE):
                logger.info("Passive screen (%s) — waiting", state.screen.value)
                time.sleep(2.0)
                continue

            # OCR full screen for unknown screen detection
            unknown_text = self.ocr.read_region(frame, (0, 0, 1080, 960)).lower()
            unknown_text_lower = self.ocr.read_region(frame, (0, 960, 1080, 1920)).lower()
            all_text = unknown_text + " " + unknown_text_lower

            # TS Climax "Try Again" screen
            if "try again" in all_text and "cancel" in unknown_text_lower:
                placement_match = re.search(r"(\d+)(?:st|nd|rd|th)", unknown_text)
                placement = int(placement_match.group(1)) if placement_match else 99

                if placement > 3 and not used_try_again:
                    logger.info("Try Again screen — placement %s, using Try Again", placement)
                    self.injector.tap(810, 1840)
                    used_try_again = True
                    time.sleep(5.0)
                else:
                    reason = "already used" if used_try_again else f"placement {placement} is good enough"
                    logger.info("Try Again screen — tapping Cancel (%s)", reason)
                    self.injector.tap(110, 1840)
                    time.sleep(2.0)
                continue

            # TS Climax race results
            if "try again" in unknown_text_lower and "next" in unknown_text_lower:
                logger.info("TS race results — tapping Next")
                self.injector.tap(700, 1840)
                continue

            # TS Climax ranking screen
            if "twinkle" in all_text and "climax" in all_text:
                logger.info("TS Climax ranking screen — tapping Next")
                self.injector.tap(540, 1720)
                time.sleep(2.0)
                continue

            # Fan rewards / Concert
            if "watch" in unknown_text_lower or "concert" in unknown_text_lower:
                logger.info("Concert/Fan rewards — tapping Next")
                self.injector.tap(810, 1810)
                continue

            # Complete Career screen — buy all remaining skills, then finish
            if "complete" in all_text and "career" in all_text:
                logger.info("Complete Career screen detected")
                if engine is not None:
                    logger.info("Spending remaining SP before completing career")
                    self.injector.tap(270, 1630)
                    time.sleep(2.5)
                    self.execute_skill_buying(
                        state, engine,
                        sp_reserve=0, buy_all=True, already_on_skill_screen=True,
                    )
                    time.sleep(1.5)

                logger.info("Tapping Complete Career")
                self.injector.tap(810, 1630)
                time.sleep(3.0)

                for _ in range(5):
                    time.sleep(2.0)
                    frame = self.provider.refresh_frame()
                    post_text = self.ocr.read_region(frame, (0, 0, 1080, 1920)).lower()
                    if "next" in post_text:
                        self.injector.tap(540, 1720)
                        continue
                    self.injector.tap(540, 960)

                logger.info("Career complete!")
                return state

            # Race Day — mandatory race triggered (debut, goal race, etc.)
            if "race day" in all_text:
                logger.info("Race Day screen detected — handling mandatory race")
                if engine is not None:
                    self.execute_race_day(state, engine)
                else:
                    # No engine — just tap Race button and let flow continue
                    self.injector.tap(540, 1640)
                    time.sleep(3.0)
                continue

            # Full Stats / Umamusume Details screen
            # Require "umamusume" specifically — "details" alone matches too
            # many screens (e.g. tutorial has "Details" button in top-right).
            if "umamusume" in unknown_text:
                logger.info("Full Stats screen — tapping Close")
                self.injector.tap(540, 1775)
                time.sleep(1.5)
                continue

            # Trackblazer Inspiration GO!
            if "go" in unknown_text_lower and "skip" in unknown_text_lower:
                logger.info("Inspiration GO! screen — tapping GO!")
                self.injector.tap(540, 1350)
                time.sleep(3.0)
                continue

            # Inspiration result
            if "inspiration" in unknown_text_lower or "spark" in unknown_text_lower:
                logger.info("Inspiration result — tapping to dismiss")
                self.injector.tap(540, 960)
                time.sleep(2.0)
                continue

            # Claw Machine minigame — tap center to drop claw, then wait
            if "claw" in unknown_text or "crane" in unknown_text:
                logger.info("CLAW MACHINE MINIGAME — auto-playing (tap center)")
                self.injector.tap(540, 960)
                time.sleep(5.0)
                self.injector.tap(540, 960)
                time.sleep(3.0)
                continue

            # Claw Machine results
            if "cuties" in unknown_text or "big win" in unknown_text:
                logger.info("Claw Machine results — tapping OK")
                self.injector.tap(540, 1810)
                continue

            # Insufficient Goal Race Result Pts popup — tap Race
            if "requirement" in unknown_text_lower or ("result" in unknown_text_lower and "race" in unknown_text_lower):
                logger.info("Goal race warning popup — setting goal_race_urgent flag")
                self.goal_race_urgent = True
                # Persist flag to disk for cross-invocation continuity
                from pathlib import Path
                Path("data/goal_race_urgent.txt").write_text("1")
                self.injector.tap(810, 1370)
                time.sleep(2.0)
                continue

            # Tutorial slide — has "Back" + "Next" or "Close" in bottom area
            if "back" in unknown_text_lower and ("next" in unknown_text_lower or "close" in unknown_text_lower):
                if "next" in unknown_text_lower:
                    logger.info("Tutorial slide — tapping Next")
                    self.injector.tap(900, 1850)
                else:
                    logger.info("Tutorial slide — tapping Close")
                    self.injector.tap(180, 1850)
                time.sleep(1.0)
                continue

            # Unknown screen — tap to advance
            logger.info("Unknown screen (%s) — tapping to advance", state.screen.value)
            self.injector.tap(540, 1675)
            time.sleep(1.5)
            self.injector.tap(540, 400)

        logger.warning("Hit max post-action screens (%d) — giving up", max_screens)
        return None

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def execute_training(self, state: GameState, engine: "DecisionEngine") -> BotAction:
        """Scan tiles, score, select best, and confirm training.

        Expects to be on the stat selection screen already.
        Returns the BotAction that was executed.
        """
        from uma_trainer.decision.scorer import ESTIMATED_TRAINING_GAINS

        logger.info("Scanning all training tiles...")
        self.sequences.scan_training_gains(state, self.provider.capture, self.assembler)

        scored = engine.scorer.score_tiles(state)
        action = engine.scorer.best_action(state)

        if action.action_type == ActionType.REST:
            logger.info("Scorer says REST: %s", action.reason)
            return action

        best_tile, best_score = scored[0]

        # Select and confirm the tile
        frame = self.provider.refresh_frame()
        currently_raised = self.assembler.detect_selected_tile(frame)
        if currently_raised != best_tile.position:
            logger.info("Selecting %s tile", best_tile.stat_type.value)
            self.injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
            time.sleep(0.8)

        logger.info("Confirming %s", best_tile.stat_type.value)
        self.injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
        time.sleep(3.0)
        return action

    def display_training_scores(self, state: GameState, engine: "DecisionEngine") -> list:
        """Display training tile scores in a table. Returns scored list."""
        from uma_trainer.decision.scorer import ESTIMATED_TRAINING_GAINS
        scored = engine.scorer.score_tiles(state)
        print()
        print("=" * 80)
        print(f"{'Tile':<10} {'Gains':<35} {'Total':>5} {'Fail%':>6} {'Bond':>12} {'Score':>8}")
        print("-" * 84)

        for tile, score in scored:
            if tile.stat_gains:
                gains_str = ", ".join(f"{s}:+{g}" for s, g in tile.stat_gains.items() if g > 0)
                total = sum(tile.stat_gains.values())
            else:
                gains_str = "(estimated)"
                total = sum(ESTIMATED_TRAINING_GAINS.get(tile.stat_type.value, {}).values())
            fail_pct = f"{tile.failure_rate * 100:.0f}%" if tile.failure_rate > 0 else "0%"
            bond_str = ",".join(f"{b}%" for b in tile.bond_levels) if tile.bond_levels else "-"
            print(f"{tile.stat_type.value:<10} {gains_str:<35} {total:>5} {fail_pct:>6} {bond_str:>12} {score:>8.1f}")

        print("=" * 80)
        return scored

    # ------------------------------------------------------------------
    # Racing
    # ------------------------------------------------------------------

    def execute_race_entry(
        self,
        race_selector=None,
    ) -> bool:
        """Navigate into the race list, find the pre-selected race, and enter it."""
        from uma_trainer.perception.regions import TURN_ACTION_REGIONS, RACE_LIST_REGIONS, get_tap_center

        races_btn = get_tap_center(TURN_ACTION_REGIONS["btn_races"])
        logger.info("Tapping Races button at %s", races_btn)
        self.injector.tap(*races_btn)
        time.sleep(2.5)

        frame = self.provider.refresh_frame()
        state = self.assembler.assemble(frame)

        # Handle popups before race list
        for _ in range(3):
            if state.screen == ScreenState.WARNING_POPUP:
                from uma_trainer.perception.regions import WARNING_POPUP_REGIONS
                ok_btn = get_tap_center(WARNING_POPUP_REGIONS["btn_ok"])
                logger.info("Warning popup before race list — tapping OK")
                self.injector.tap(*ok_btn)
                time.sleep(2.0)
                frame = self.provider.refresh_frame()
                state = self.assembler.assemble(frame)
            elif state.screen == ScreenState.SKILL_SHOP:
                logger.info("Shop popup before race list — tapping Cancel")
                self.injector.tap(100, 1150)
                time.sleep(2.0)
                frame = self.provider.refresh_frame()
                state = self.assembler.assemble(frame)
            else:
                break

        if state.screen != ScreenState.RACE_ENTRY:
            logger.warning("Expected RACE_ENTRY, got %s — aborting race", state.screen.value)
            return False

        pre_selected = race_selector._pre_selected if race_selector else None

        if pre_selected and self.sequences:
            from uma_trainer.decision.race_selector import GRADE_SORT_ORDER
            estimated_pos = GRADE_SORT_ORDER.get(pre_selected.grade, 0) * 3
            logger.info("Looking for pre-selected race '%s' (grade=%s, est. position=%d)",
                        pre_selected.name, pre_selected.grade, estimated_pos)

            tap_coords = self.sequences.navigate_to_race(
                target_grade=pre_selected.grade,
                target_distance=pre_selected.distance,
                target_surface=pre_selected.surface,
                target_name=pre_selected.name,
                estimated_position=estimated_pos,
                capture=self.provider.capture,
                ocr=self.ocr,
            )

            if tap_coords:
                logger.info("Found race — tapping at %s", tap_coords)
                self.injector.tap(*tap_coords)
                time.sleep(1.0)
            else:
                logger.warning("Could not find '%s' in race list — aborting race (going back)",
                               pre_selected.name)
                self.injector.tap(75, 1870)
                time.sleep(1.5)
                return False
        elif race_selector and state.available_races:
            action = race_selector.decide(state)
            if action.action_type == ActionType.WAIT:
                logger.warning("RaceSelector: no suitable race — %s. Going back.", action.reason)
                self.injector.tap(75, 1870)
                time.sleep(1.5)
                return False

            if action.tap_coords and action.tap_coords != (0, 0):
                logger.info("Selecting race at %s (legacy)", action.tap_coords)
                self.injector.tap(*action.tap_coords)
                time.sleep(1.0)
        else:
            logger.info("No race selector — entering first visible race")

        # Tap Race button at bottom
        race_btn = get_tap_center(RACE_LIST_REGIONS["btn_race"])
        logger.info("Tapping Race button at %s", race_btn)
        self.injector.tap(*race_btn)
        time.sleep(2.0)

        # Confirm race popup
        logger.info("Tapping race confirmation")
        self.injector.tap(810, 1370)
        time.sleep(3.0)
        return True

    def execute_race_day(self, state: GameState, engine: "DecisionEngine") -> bool:
        """Handle TS Climax mandatory Race Day."""
        logger.info("RACE DAY detected (TS Climax %d/%d) — entering race",
                     state.ts_climax_races_done, state.ts_climax_races_total)

        self.injector.tap(540, 1640)
        time.sleep(3.0)

        frame = self.provider.refresh_frame()
        race_state = self.assembler.assemble(frame)
        logger.info("After Race Day tap: screen=%s", race_state.screen.value)

        logger.info("Tapping Race on list")
        self.injector.tap(540, 1640)
        time.sleep(3.0)

        logger.info("Confirming race")
        self.injector.tap(810, 1370)
        time.sleep(3.0)

        logger.info("Handling post-race flow")
        result_state = self.wait_for_career_home(engine=engine)
        if result_state:
            logger.info("Race Day complete. Energy: %d, Mood: %s",
                        result_state.energy, result_state.mood.value)
        return True

    # ------------------------------------------------------------------
    # Rest / Go Out / Infirmary
    # ------------------------------------------------------------------

    def execute_rest(self) -> None:
        """Tap Rest and confirm."""
        from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
        rest_btn = get_tap_center(TURN_ACTION_REGIONS["btn_rest"])
        logger.info("Tapping Rest at %s", rest_btn)
        self.injector.tap(*rest_btn)
        time.sleep(2.0)
        logger.info("Confirming rest")
        self.injector.tap(810, 1260)
        time.sleep(2.0)

    def execute_go_out(self) -> None:
        """Tap Recreation (Go Out) and confirm."""
        from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
        go_out_btn = get_tap_center(TURN_ACTION_REGIONS["btn_recreation"])
        logger.info("Tapping Go Out at %s", go_out_btn)
        self.injector.tap(*go_out_btn)
        time.sleep(2.0)
        logger.info("Confirming Go Out")
        self.injector.tap(810, 1260)
        time.sleep(2.0)

    def execute_infirmary(self) -> None:
        """Tap Infirmary and confirm."""
        from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
        infirmary_btn = get_tap_center(TURN_ACTION_REGIONS["btn_infirmary"])
        logger.info("Tapping Infirmary at %s", infirmary_btn)
        self.injector.tap(*infirmary_btn)
        time.sleep(2.0)
        logger.info("Confirming Infirmary")
        self.injector.tap(810, 1260)
        time.sleep(2.0)

    # ------------------------------------------------------------------
    # Conditions
    # ------------------------------------------------------------------

    def check_conditions(self) -> tuple[list[Condition], dict[str, str]]:
        """Open Full Stats, read conditions + aptitudes, close.

        Returns (conditions, aptitudes) where aptitudes is a dict like
        {"turf": "A", "dirt": "E", "short": "D", "mile": "A", ...}.
        """
        logger.info("Checking conditions + aptitudes via Full Stats")
        self.injector.tap(1015, 1120)
        time.sleep(2.0)

        # Wait for Full Stats screen to load — verify header says "Umamusume"
        frame = self.provider.refresh_frame()
        header_text = self.ocr.read_region(frame, (0, 40, 600, 120)).lower()
        if "umamusume" not in header_text:
            logger.info("Full Stats not loaded yet ('%s') — waiting and retrying", header_text.strip())
            time.sleep(2.0)
            frame = self.provider.refresh_frame()
            header_text = self.ocr.read_region(frame, (0, 40, 600, 120)).lower()
            if "umamusume" not in header_text:
                logger.warning("Full Stats still not visible ('%s') — reading anyway", header_text.strip())

        # --- Conditions ---
        condition_text = self.ocr.read_region(frame, (0, 950, 1080, 1250)).lower()
        logger.info("Conditions OCR: '%s'", condition_text)

        conditions = []
        condition_keywords = {
            "skin outbreak": Condition.SKIN_OUTBREAK,
            "migraine": Condition.MIGRAINE,
            "night owl": Condition.NIGHT_OWL,
            "slacker": Condition.SLACKER,
            "practice poor": Condition.PRACTICE_POOR,
            "overweight": Condition.OVERWEIGHT,
            "sharp": Condition.SHARP,
            "charming": Condition.CHARMING,
        }
        for keyword, cond in condition_keywords.items():
            if keyword in condition_text:
                conditions.append(cond)

        # --- Aptitudes ---
        aptitudes = self._read_aptitudes(frame)

        for close_attempt in range(3):
            self.injector.tap(540, 1775)
            time.sleep(1.5)
            check_frame = self.provider.refresh_frame()
            header_text = self.ocr.read_region(check_frame, (0, 0, 300, 80)).lower()
            if "career" in header_text:
                break
            logger.info("Full Stats still open (attempt %d) — retrying Close", close_attempt + 1)

        return conditions, aptitudes

    def _read_aptitudes(self, frame) -> dict[str, str]:
        """Parse aptitude grades from the Full Stats screen frame."""
        import cv2
        from uma_trainer.perception.regions import FULL_STATS_REGIONS

        valid_grades = {"S", "A", "B", "C", "D", "E", "F", "G"}

        def _parse_row(text: str, pairs: list[tuple[str, str]]) -> dict[str, str]:
            result = {}
            for label, key in pairs:
                m = re.search(rf"{label}\s*([A-GS])\b", text, re.IGNORECASE)
                if m and m.group(1).upper() in valid_grades:
                    result[key] = m.group(1).upper()
            return result

        def _read_row_adaptive(region_crop):
            """Read a row with adaptive threshold (inverted) for better grade detection."""
            h, w = region_crop.shape[:2]
            up = cv2.resize(region_crop, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)
            gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
            binarized = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY_INV, 31, 10,
            )
            bgr = cv2.cvtColor(binarized, cv2.COLOR_GRAY2BGR)
            return self.ocr.read_text(bgr).strip()

        aptitudes: dict[str, str] = {}

        row_defs = [
            ("track_row", [("turf", "turf"), ("dirt", "dirt")]),
            ("distance_row", [("sprint", "short"), ("mile", "mile"),
                              ("medium", "medium"), ("long", "long")]),
        ]

        # Pass 1: raw OCR on each row
        for row_name, pairs in row_defs:
            text = self.ocr.read_region(frame, FULL_STATS_REGIONS[row_name])
            logger.debug("%s OCR: '%s'", row_name, text.strip())
            aptitudes.update(_parse_row(text, pairs))

        # Style row (informational logging only)
        style_text = self.ocr.read_region(frame, FULL_STATS_REGIONS["style_row"])
        logger.debug("Style row OCR: '%s'", style_text)

        # Pass 2: retry missing with adaptive_inv binarization + shifted y
        expected_keys = {"turf", "dirt", "short", "mile", "medium", "long"}
        missing = expected_keys - aptitudes.keys()
        if missing:
            logger.info("Aptitude retry for missing: %s", missing)
            row_key_map = {
                "track_row": {"turf", "dirt"},
                "distance_row": {"short", "mile", "medium", "long"},
            }
            for row_name, pairs in row_defs:
                row_keys = row_key_map[row_name]
                if not (missing & row_keys):
                    continue
                x1, y1, x2, y2 = FULL_STATS_REGIONS[row_name]
                for dy in [0, -5, 5, -10, 10]:
                    crop = frame[y1 + dy:y2 + dy, x1:x2]
                    text = _read_row_adaptive(crop)
                    logger.debug("Retry %s adaptive dy=%d: '%s'", row_name, dy, text)
                    for k, v in _parse_row(text, pairs).items():
                        if k not in aptitudes:
                            aptitudes[k] = v
                    if not (missing & row_keys - aptitudes.keys()):
                        break

        logger.info("Trainee aptitudes: %s", aptitudes)
        return aptitudes

    # ------------------------------------------------------------------
    # Shop
    # ------------------------------------------------------------------

    def execute_shop_visit(self, engine: "DecisionEngine") -> None:
        """Navigate to shop, buy priority items, and exit."""
        from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
        from uma_trainer.decision.shop_manager import ITEM_CATALOGUE, ItemTier

        shop_btn = get_tap_center(TURN_ACTION_REGIONS["btn_shop"])
        logger.info("Visiting shop at %s", shop_btn)
        self.injector.tap(*shop_btn)
        time.sleep(2.5)

        frame = self.provider.refresh_frame()
        shop_text = self.ocr.read_region(frame, (0, 0, 300, 80)).lower()
        if "shop" not in shop_text:
            logger.warning("May not be in shop (header: '%s') — tapping Back", shop_text[:40])
            self.injector.tap(50, 1870)
            time.sleep(2.0)
            return

        coins = self._get_shop_coins(frame)
        logger.info("Shop coins: %s", coins)

        if coins is not None and coins < 15:
            logger.info("Not enough coins — exiting shop")
            self.injector.tap(50, 1870)
            time.sleep(2.0)
            return

        # Build want-list
        inventory = engine.shop_manager.inventory
        tier_order = {ItemTier.SS: 0, ItemTier.S: 1, ItemTier.A: 2, ItemTier.B: 3}
        buyable = []
        for key, item in ITEM_CATALOGUE.items():
            if item.tier == ItemTier.NEVER:
                continue
            owned = inventory.get(key, 0)
            if owned >= item.max_stock:
                continue
            buyable.append((tier_order[item.tier], item.cost, key))

        buyable.sort()
        want_keys = [key for _, _, key in buyable]

        if not want_keys:
            logger.info("Nothing to buy — exiting shop")
            self.injector.tap(50, 1870)
            time.sleep(2.0)
            return

        logger.info("Want list: %s", want_keys[:10])

        name_to_key = self._build_shop_name_matcher()
        selected_counts: dict[str, int] = {}
        selected_keys: list[str] = []
        tapped_positions: list[tuple[str, int, int]] = []
        spent = 0
        scroll_offset = 0
        max_scrolls = 4
        SWIPE_PX = 350

        for scroll in range(max_scrolls + 1):
            if scroll > 0:
                self.injector.swipe(540, 1100, 540, 750, duration_ms=400)
                time.sleep(0.8)
                self.injector.tap(540, 350)
                time.sleep(1.5)
                scroll_offset += SWIPE_PX

            frame = self.provider.refresh_frame()
            visible = self._scan_shop_items(frame, name_to_key)

            for item_key, name_y, is_purchased in visible:
                if is_purchased or item_key not in want_keys:
                    continue

                absolute_y = name_y + scroll_offset
                already_tapped = False
                for prev_key, prev_y, prev_scroll in tapped_positions:
                    if prev_key != item_key:
                        continue
                    prev_absolute_y = prev_y + prev_scroll * SWIPE_PX
                    if abs(absolute_y - prev_absolute_y) < 200:
                        already_tapped = True
                        break
                if already_tapped:
                    continue

                item = ITEM_CATALOGUE[item_key]
                owned = inventory.get(item_key, 0)
                already_selected = selected_counts.get(item_key, 0)
                if owned + already_selected >= item.max_stock:
                    continue

                if coins is not None and (spent + item.cost) > coins:
                    continue

                checkbox_x = 950
                checkbox_y = name_y + 15
                logger.info("  Selecting %s at (%d, %d)", item.name, checkbox_x, checkbox_y)
                self.injector.tap(checkbox_x, checkbox_y)
                time.sleep(0.5)

                tapped_positions.append((item_key, name_y, scroll))
                selected_keys.append(item_key)
                selected_counts[item_key] = already_selected + 1
                spent += item.cost

        if selected_keys:
            logger.info("Confirming purchase of %d items (%d coins): %s",
                         len(selected_keys), spent, selected_keys)
            self.injector.tap(540, 1640)
            time.sleep(2.0)
            logger.info("Tapping Exchange")
            self.injector.tap(810, 1780)
            time.sleep(2.0)
            logger.info("Tapping Close on Exchange Complete")
            self.injector.tap(270, 1780)
            time.sleep(2.0)

            for key in selected_keys:
                engine.shop_manager.add_item(key)
            engine.shop_manager.save_inventory()
            logger.info("Inventory updated: %s", engine.shop_manager.inventory)
        else:
            logger.info("No items selected for purchase")

        # Exit shop — verify we actually left
        for exit_attempt in range(3):
            self.injector.tap(50, 1870)
            time.sleep(2.0)
            frame = self.provider.refresh_frame()
            header_text = self.ocr.read_region(frame, (0, 0, 300, 80)).lower()
            if "shop" not in header_text:
                logger.info("Shop exit confirmed (attempt %d)", exit_attempt + 1)
                break
            logger.warning("Still in shop after Back tap (attempt %d) — retrying",
                           exit_attempt + 1)
        else:
            logger.error("Could not exit shop after 3 attempts")

    # ------------------------------------------------------------------
    # Skill buying
    # ------------------------------------------------------------------

    def execute_skill_buying(
        self,
        state: GameState,
        engine: "DecisionEngine",
        sp_reserve: int = 800,
        buy_all: bool = False,
        already_on_skill_screen: bool = False,
    ) -> None:
        """Open skill screen and buy affordable skills above the SP reserve."""
        from uma_trainer.knowledge.skill_matcher import SkillMatcher

        sp = state.skill_pts
        if buy_all:
            sp_reserve = 0
        if not buy_all and sp <= sp_reserve:
            logger.info("Skill pts %d <= reserve %d — skipping", sp, sp_reserve)
            return

        spendable = sp - sp_reserve
        logger.info("Skill pts %d (reserve %d, spendable %d)%s",
                     sp, sp_reserve, spendable, " — buy_all mode" if buy_all else "")

        if not already_on_skill_screen:
            from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
            skills_btn = get_tap_center(TURN_ACTION_REGIONS["btn_skills"])
            self.injector.tap(*skills_btn)
            time.sleep(2.5)

        frame = self.provider.refresh_frame()
        header_text = self.ocr.read_region(frame, (0, 0, 400, 100)).lower()
        if "skill" not in header_text and "learn" not in header_text:
            logger.warning("Not on skill screen (header: '%s') — aborting", header_text[:40])
            self.injector.tap(50, 1870)
            time.sleep(2.0)
            return

        sp_text = self.ocr.read_region(frame, (700, 580, 1050, 650)).strip()
        sp_match = re.search(r"(\d+)", sp_text)
        if sp_match:
            sp = int(sp_match.group(1))
            spendable = sp - sp_reserve
            logger.info("Skill screen SP: %d (spendable: %d)", sp, spendable)

        # Get priority/blacklist from strategy
        priority_names = []
        blacklist_names = []
        if engine.scorer.overrides:
            strategy = engine.scorer.overrides.get_strategy_raw()
            raw_priority = strategy.get("skill_priority_list", [])
            for entry in raw_priority:
                if isinstance(entry, str):
                    priority_names.append(entry.lower())
                elif isinstance(entry, dict) and "name" in entry:
                    priority_names.append(entry["name"].lower())
            blacklist_names = [n.lower() for n in strategy.get("skill_blacklist", [])]

        logger.info("Priority skills: %s", priority_names)
        matcher = SkillMatcher()

        added_any = False
        spent = 0
        bought_names: set[str] = set()
        max_scrolls = 8 if buy_all else 5

        for buy_pass in range(2 if buy_all else 1):
            pass_label = "priority" if buy_pass == 0 else "remaining"
            if buy_pass == 1:
                logger.info("Pass 2: buying all remaining affordable skills")
                for _ in range(max_scrolls):
                    self.injector.swipe(540, 800, 540, 1400, duration_ms=300)
                    time.sleep(0.5)
                time.sleep(1.0)

            for scroll in range(max_scrolls + 1):
                frame = self.provider.refresh_frame()
                skills = self._parse_skill_rows(frame, matcher)

                for skill in skills:
                    if skill["obtained"] or skill["cost"] is None:
                        continue

                    name_lower = skill["matched_name"].lower()
                    cost = skill["cost"]

                    if name_lower in bought_names:
                        continue
                    if any(bl in name_lower for bl in blacklist_names):
                        continue
                    if skill.get("is_legacy", False):
                        logger.info("  Skipping Legacy skill '%s'", skill["matched_name"])
                        continue
                    if cost > (spendable - spent):
                        continue

                    is_priority = any(p in name_lower for p in priority_names)
                    is_hint_cheap = skill["hint_level"] > 0 and cost <= 120

                    if buy_pass == 0 and not (is_priority or is_hint_cheap):
                        continue

                    logger.info("  BUYING '%s' for %d SP (%s, hint=%d)",
                                skill["matched_name"], cost, pass_label, skill["hint_level"])
                    self.injector.tap(980, skill["plus_y"])
                    time.sleep(0.8)
                    spent += cost
                    bought_names.add(name_lower)
                    added_any = True

                    if (spendable - spent) <= 0:
                        logger.info("  Budget exhausted")
                        break

                if (spendable - spent) <= 0:
                    break

                if scroll < max_scrolls:
                    self.injector.swipe(540, 1300, 540, 1020, duration_ms=400)
                    time.sleep(2.0)

            if (spendable - spent) <= 0:
                break

        if added_any:
            logger.info("Confirming skill purchases (spent %d SP, bought %d skills)",
                         spent, len(bought_names))
            self.injector.tap(540, 1800)
            time.sleep(2.5)
            logger.info("Tapping Learn on confirmation popup")
            self.injector.tap(810, 1830)
            time.sleep(2.5)
            logger.info("Tapping Close on Skills Learned popup")
            self.injector.tap(540, 1200)
            time.sleep(1.5)

        logger.info("Tapping Back on skill screen")
        self.injector.tap(40, 1830)
        time.sleep(2.0)
        # Handle "Exit without learning skills?" popup
        self.injector.tap(810, 1260)
        time.sleep(2.0)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_skill_rows(self, frame, skill_matcher=None) -> list[dict]:
        """OCR visible skill rows and return list of parsed skill dicts."""
        skills = []
        y = 680
        while y < 1550:
            name_text = self.ocr.read_region(frame, (70, y, 800, y + 50)).strip()
            if not name_text or len(name_text) < 3:
                y += 30
                continue

            lower = name_text.lower()
            skip_prefixes = [
                "increase", "decrease", "slightly", "moderately", "very",
                "move", "recover", "in ", "of ", "the ", "after", "on ",
                "out ", "when", "gap", "late", "over", "corner", "close",
                "back", "(", "non-", "straight", "slig", "verv", "sligh",
            ]
            if any(lower.startswith(w) for w in skip_prefixes):
                y += 30
                continue
            if len(name_text) > 40:
                y += 30
                continue

            matched_name = name_text
            if skill_matcher:
                result = skill_matcher.match(name_text)
                if result:
                    matched_name, score = result
                    if matched_name != name_text:
                        logger.info("  Fuzzy: '%s' → '%s' (%d%%)", name_text, matched_name, score)
                else:
                    y += 30
                    continue

            hint_level = 0
            hint_text = self.ocr.read_region(frame, (70, y + 35, 500, y + 85)).strip().lower()
            hint_match = re.search(r"hint\s*(?:lv|lvl?)\.?\s*(\d)", hint_text)
            if hint_match:
                hint_level = int(hint_match.group(1))
            elif "hint" in hint_text or "off" in hint_text:
                hint_level = 1

            obtained_text = self.ocr.read_region(frame, (800, y + 50, 1050, y + 130)).strip().lower()
            is_obtained = "obtained" in obtained_text or "obt" in obtained_text

            cost = None
            if not is_obtained:
                cost_text = self.ocr.read_region(frame, (820, y + 80, 940, y + 140)).strip()
                cost_match = re.search(r"(\d{2,4})", cost_text)
                if cost_match:
                    cost = int(cost_match.group(1))

            is_legacy = False
            if skill_matcher:
                entry = skill_matcher.get_entry(matched_name)
                if entry:
                    rarity = entry.get("rarity", "normal")
                    if rarity in ("rare", "special", "evolved", "unique"):
                        is_legacy = True

            plus_y = y + 110
            skill = {
                "name": name_text,
                "matched_name": matched_name,
                "cost": cost,
                "obtained": is_obtained,
                "hint_level": hint_level,
                "plus_y": plus_y,
                "is_legacy": is_legacy,
            }
            skills.append(skill)
            hint_str = f" hint_lvl={hint_level}" if hint_level else ""
            legacy_str = " LEGACY" if is_legacy else ""
            logger.info("  Skill: '%s' cost=%s%s%s%s",
                        matched_name, cost, hint_str,
                        " (obtained)" if is_obtained else "", legacy_str)
            y += 240

        return skills

    def _build_shop_name_matcher(self) -> dict[str, str]:
        """Build a fuzzy matcher for shop item names."""
        from uma_trainer.decision.shop_manager import ITEM_CATALOGUE, ItemTier
        name_to_key: dict[str, str] = {}
        for key, item in ITEM_CATALOGUE.items():
            if item.tier == ItemTier.NEVER:
                continue
            name_to_key[item.name] = key
        return name_to_key

    def _scan_shop_items(self, frame, name_to_key) -> list[tuple[str, int, bool]]:
        """Scan visible shop items. Returns list of (item_key, name_y, purchased)."""
        from rapidfuzz import fuzz, process
        items = []
        y = 700
        while y < 1450:
            name_text = self.ocr.read_region(frame, (130, y, 700, y + 45)).strip()
            if not name_text or len(name_text) < 3:
                y += 30
                continue

            lower = name_text.lower()
            if any(lower.startswith(w) for w in ("cost", "effect", "choose", "x1", "xl")):
                y += 30
                continue

            # Fuzzy match
            names = list(name_to_key.keys())
            result = process.extractOne(name_text, names, scorer=fuzz.token_sort_ratio, score_cutoff=65)
            if result is None:
                y += 30
                continue

            matched_name, score, _idx = result
            item_key = name_to_key[matched_name]

            right_text = self.ocr.read_region(frame, (700, y + 20, 1050, y + 80)).strip().lower()
            is_purchased = "purchased" in right_text or "purch" in right_text

            status = " PURCHASED" if is_purchased else ""
            logger.info("  Shop item: '%s' → %s%s (y=%d)", name_text, item_key, status, y)
            items.append((item_key, y, is_purchased))
            y += 150

        return items

    def _get_shop_coins(self, frame) -> int | None:
        """Read coin balance from shop screen header."""
        coins_text = self.ocr.read_region(frame, (780, 590, 1060, 650)).strip()
        match = re.search(r"(\d+)", coins_text)
        return int(match.group(1)) if match else None
