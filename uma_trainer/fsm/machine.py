"""GameFSM: the main orchestration loop."""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING

from uma_trainer.fsm.states import FSMState, TRANSITIONS, InvalidTransitionError
from uma_trainer.decision.logger import DecisionLogger
from uma_trainer.types import (
    ActionType,
    GameState,
    RunResult,
    ScreenState,
)

if TYPE_CHECKING:
    from uma_trainer.action.input_injector import InputInjector
    from uma_trainer.action.sequences import ActionSequences
    from uma_trainer.capture.base import CaptureBackend
    from uma_trainer.config import AppConfig
    from uma_trainer.decision.strategy import DecisionEngine
    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.perception.assembler import StateAssembler

logger = logging.getLogger(__name__)

# How long to wait for a screen animation to settle after a tap (seconds)
ANIMATION_SETTLE_TIME = 1.5
# How many consecutive unknown screens trigger error recovery
UNKNOWN_SCREEN_THRESHOLD = 5
# Max error recovery attempts before pausing
MAX_RECOVERY_ATTEMPTS = 3


class BotStatus:
    """Thread-safe snapshot of the current bot state for the web dashboard."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.fsm_state: FSMState = FSMState.IDLE
        self.game_state: GameState | None = None
        self.last_action: str = ""
        self.last_action_tier: int = 1
        self.tile_scores: list[dict] = []   # Populated by FSM after each scoring
        self.error_count: int = 0
        self.runs_completed: int = 0
        self.paused: bool = False

    def update(
        self,
        fsm: FSMState,
        game: GameState | None = None,
        action: str = "",
        tier: int = 1,
        tile_scores: list[dict] | None = None,
    ) -> None:
        with self._lock:
            self.fsm_state = fsm
            if game is not None:
                self.game_state = game
            if action:
                self.last_action = action
                self.last_action_tier = tier
            if tile_scores is not None:
                self.tile_scores = tile_scores

    def snapshot(self) -> dict:
        with self._lock:
            gs = self.game_state
            # Build training tile data for the UI
            tiles = []
            if gs:
                for tile in gs.training_tiles:
                    score_entry = next(
                        (s for s in self.tile_scores if s.get("stat") == tile.stat_type.value),
                        None,
                    )
                    tiles.append({
                        "stat": tile.stat_type.value,
                        "score": score_entry["score"] if score_entry else 0.0,
                        "is_rainbow": tile.is_rainbow,
                        "is_gold": tile.is_gold,
                        "has_hint": tile.has_hint,
                        "has_director": tile.has_director,
                        "card_count": len(tile.support_cards),
                        "position": tile.position,
                    })

            # Event choices
            choices = []
            if gs and gs.event_choices:
                choices = [{"index": c.index, "text": c.text} for c in gs.event_choices]

            return {
                "fsm_state": self.fsm_state.value,
                "game_screen": gs.screen.value if gs else "unknown",
                "energy": gs.energy if gs else 0,
                "turn": gs.current_turn if gs else 0,
                "max_turns": gs.max_turns if gs else 72,
                "mood": gs.mood.value if gs else "normal",
                "stats": dataclasses.asdict(gs.stats) if gs else {},
                "support_cards": [
                    {"name": c.name, "bond": c.bond_level, "is_friend": c.is_friend}
                    for c in (gs.support_cards if gs else [])
                ],
                "training_tiles": tiles,
                "event_text": gs.event_text if gs else "",
                "event_choices": choices,
                "last_action": self.last_action,
                "last_action_tier": self.last_action_tier,
                "error_count": self.error_count,
                "runs_completed": self.runs_completed,
                "paused": self.paused,
                "scenario": gs.scenario if gs else "",
            }


class GameFSM:
    """Drives the main perception–decision–action loop.

    Thread safety: run() is expected to run on a dedicated thread.
    BotStatus is thread-safe for reading from the web dashboard thread.
    """

    def __init__(
        self,
        capture: "CaptureBackend",
        assembler: "StateAssembler",
        engine: "DecisionEngine",
        injector: "InputInjector",
        sequences: "ActionSequences",
        kb: "KnowledgeBase",
        config: "AppConfig",
    ) -> None:
        self.capture = capture
        self.assembler = assembler
        self.engine = engine
        self.injector = injector
        self.sequences = sequences
        self.kb = kb
        self.config = config

        self.state: FSMState = FSMState.IDLE
        self.status = BotStatus()
        self.decision_logger = DecisionLogger(kb, scenario=engine.scenario)
        self._stop_requested = threading.Event()
        self._consecutive_unknown = 0
        self._recovery_attempts = 0
        self._current_run_id: str = ""
        self._run_start_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the main bot loop. Blocks until shutdown."""
        logger.info("GameFSM starting")
        try:
            self._transition(FSMState.INITIALIZING)
            self._initialize()

            self._transition(FSMState.WAITING_FOR_GAME)
            self._wait_for_game()

            while not self._stop_requested.is_set():
                if self.state == FSMState.SHUTDOWN:
                    break
                if self.state == FSMState.PAUSED or self.status.paused:
                    time.sleep(1.0)
                    continue

                self._tick()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt — shutting down")
        except Exception as e:
            logger.exception("Fatal error in FSM: %s", e)
        finally:
            self._shutdown()

    def pause(self) -> None:
        """Pause the bot (can be resumed via resume())."""
        self.status.paused = True
        logger.info("Bot paused")

    def resume(self) -> None:
        """Resume a paused bot."""
        self.status.paused = False
        logger.info("Bot resumed")

    def stop(self) -> None:
        """Request a clean shutdown."""
        self._stop_requested.set()

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """One iteration of the perception–decision–action loop."""
        try:
            frame = self.capture.grab_frame()
        except Exception as e:
            logger.warning("Capture failed: %s", e)
            time.sleep(1.0)
            return

        game_state = self.assembler.assemble(frame)
        self.status.update(self.state, game_state)

        if game_state.screen == ScreenState.LOADING:
            self._sleep(1.0 / self.config.capture.fps_passive)
            return

        if game_state.screen == ScreenState.UNKNOWN:
            self._handle_unknown_screen(game_state)
            return

        self._consecutive_unknown = 0
        self._recovery_attempts = 0

        # Route to the right handler based on current FSM state
        if self.state in (FSMState.RUNNING_TURN, FSMState.WAITING_ANIMATION):
            self._handle_running_turn(game_state)
        elif self.state == FSMState.WAITING_FOR_GAME:
            self._handle_waiting_for_game(game_state)
        elif self.state == FSMState.STARTING_CAREER:
            self._handle_starting_career(game_state)
        elif self.state == FSMState.ERROR_RECOVERY:
            self._handle_error_recovery(game_state)

        self._sleep(1.0 / self.config.capture.fps_decision)

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _handle_running_turn(self, game_state: GameState) -> None:
        """Make and execute a decision for the current game state."""
        screen = game_state.screen

        if screen in (ScreenState.RACE, ScreenState.CUTSCENE):
            if self.state != FSMState.IN_RACE:
                self._transition(FSMState.IN_RACE)
            self._sleep(1.0 / self.config.capture.fps_passive)
            return
        elif self.state == FSMState.IN_RACE:
            # Race just finished — notify scenario handler
            if self.engine.scenario:
                self.engine.scenario.on_race_completed()
            self._transition(FSMState.RUNNING_TURN)

        if screen == ScreenState.CAREER_SETUP:
            self._transition(FSMState.STARTING_CAREER)
            return

        if screen == ScreenState.RESULT_SCREEN:
            self.sequences.dismiss_result_screen()
            return

        if screen == ScreenState.PRE_RACE:
            # Tap "View Results" to skip race animation
            logger.info("Pre-race screen: tapping View Results")
            self.injector.tap(380, 1760)
            time.sleep(2.0)
            return

        if screen == ScreenState.POST_RACE:
            self._handle_post_race()
            return

        if screen == ScreenState.WARNING_POPUP:
            self._handle_warning_popup(game_state)
            return

        if screen == ScreenState.SKILL_SHOP:
            if self.state != FSMState.SKILL_SHOPPING:
                self._transition(FSMState.SKILL_SHOPPING)
            self._handle_skill_shopping(game_state)
            return
        elif self.state == FSMState.SKILL_SHOPPING:
            self._transition(FSMState.RUNNING_TURN)

        # Standard training/event decisions
        # Tick active item effects at the start of each training turn
        if game_state.screen == ScreenState.TRAINING and self.engine.shop_manager:
            self.engine.shop_manager.tick_effects(game_state.current_turn)

        # On the stat selection screen, scan ALL training tiles for stat gains
        # before scoring. The preview only shows gains for the currently
        # selected (raised) tile, so we must tap each one to compare.
        if game_state.screen == ScreenState.TRAINING and game_state.training_tiles:
            self.sequences.scan_training_gains(
                game_state, self.capture, self.assembler,
            )

            # Compute tile scores for the dashboard and decision log
            try:
                scored = self.engine.scorer.score_tiles(game_state)
                tile_scores = [
                    {"stat": t.stat_type.value, "score": round(s, 1)}
                    for t, s in scored
                ]
                self.status.update(self.state, tile_scores=tile_scores)
            except Exception:
                tile_scores = []
        else:
            tile_scores = []

        action = self.engine.decide(game_state)
        self.status.update(
            self.state,
            action=f"{action.action_type.value}: {action.reason}",
            tier=action.tier_used,
        )
        logger.info(
            "Action: %s (tier=%d) → %s",
            action.action_type.value,
            action.tier_used,
            action.reason,
        )

        # Log every non-WAIT decision for training data
        if action.action_type != ActionType.WAIT:
            self.decision_logger.log_decision(
                run_id=self._current_run_id,
                state=game_state,
                action=action,
                tile_scores=tile_scores,
            )

        if action.action_type == ActionType.WAIT:
            return

        # Track consecutive races for fatigue management
        if action.action_type != ActionType.RACE:
            self.engine.race_selector.on_non_race_action()

        self._transition(FSMState.EXECUTING_ACTION)
        try:
            self.injector.tap_action(action)
        except Exception as e:
            logger.error("Tap failed: %s", e)
            self._transition(FSMState.ERROR_RECOVERY)
            return

        # Register item training effects when a shop item is used
        if action.action_type == ActionType.USE_ITEM and self.engine.shop_manager:
            self.engine.shop_manager.activate_item(action.target)

        # Race entry confirmation: after tapping the Race button on the
        # race list, a "Race Details / Enter race?" dialog appears.
        # Tap the green confirm button on the dialog.
        if screen == ScreenState.RACE_ENTRY and action.action_type == ActionType.RACE:
            time.sleep(1.5)
            # Green "Race" confirm button on the dialog at ~(730, 1385)
            self.injector.tap(730, 1385)
            logger.debug("Tapped race confirmation dialog")
            time.sleep(ANIMATION_SETTLE_TIME)

        self._transition(FSMState.WAITING_ANIMATION)
        time.sleep(ANIMATION_SETTLE_TIME)
        self._transition(FSMState.RUNNING_TURN)

        self.injector.wait_random_pause()

    def _handle_waiting_for_game(self, game_state: GameState) -> None:
        """Wait until the game reaches a playable state.

        Handles mid-career resume: if the bot starts and the game is
        already on any in-career screen, jump straight to RUNNING_TURN.
        """
        screen = game_state.screen

        if screen == ScreenState.MAIN_MENU:
            logger.info("Game detected on main menu")
            self._transition(FSMState.STARTING_CAREER)
            return

        # Any in-career screen → resume mid-career
        in_career_screens = {
            ScreenState.TRAINING,
            ScreenState.EVENT,
            ScreenState.SKILL_SHOP,
            ScreenState.RACE_ENTRY,
            ScreenState.RESULT_SCREEN,
            ScreenState.PRE_RACE,
            ScreenState.POST_RACE,
            ScreenState.WARNING_POPUP,
            ScreenState.RACE,
            ScreenState.CUTSCENE,
        }
        if screen in in_career_screens:
            logger.info("Game detected mid-career on %s screen", screen.value)
            self._current_run_id = str(uuid.uuid4())
            self._run_start_time = time.time()
            self._transition(FSMState.RUNNING_TURN)

    def _handle_starting_career(self, game_state: GameState) -> None:
        """Handle Career Mode setup screens."""
        if game_state.screen == ScreenState.TRAINING:
            logger.info("Career run started — turn %d", game_state.current_turn)
            self._current_run_id = str(uuid.uuid4())
            self._run_start_time = time.time()
            self._transition(FSMState.RUNNING_TURN)
        else:
            # Tap confirm/start button
            self.sequences.confirm_dialog()

    def _handle_skill_shopping(self, game_state: GameState) -> None:
        """Process the skill shop: buy/skip skills."""
        actions = self.engine.get_skill_actions(game_state)
        for action in actions:
            if action.action_type == ActionType.BUY_SKILL:
                logger.info("Buying skill: %s", action.target)
                self.injector.tap_action(action)
                time.sleep(0.8)
                self.sequences.confirm_dialog()
                time.sleep(0.5)
        # Tap done
        self.sequences.confirm_dialog()

    def _handle_warning_popup(self, game_state: GameState) -> None:
        """Handle a warning popup (e.g. consecutive race warning).

        OCRs the warning text to understand what it's about, then decides
        whether to proceed (OK) or cancel based on game state.

        For consecutive-race warnings: proceed if mood is GOOD/GREAT,
        cancel if mood is BAD/TERRIBLE or energy is critically low.
        """
        from uma_trainer.types import Mood
        from uma_trainer.perception.regions import WARNING_POPUP_REGIONS, get_tap_center

        # OCR the warning text to understand it
        try:
            frame = self.capture.grab_frame()
            text_region = WARNING_POPUP_REGIONS["text"]
            x1, y1, x2, y2 = text_region
            roi = frame[y1:y2, x1:x2]
            warning_text = self.assembler.ocr.read_text(roi).lower()
        except Exception as e:
            logger.warning("Failed to OCR warning popup: %s", e)
            warning_text = ""

        logger.info("Warning popup text: '%s'", warning_text)

        # Decide: proceed or cancel
        proceed = True

        if "consecutive" in warning_text or "in a row" in warning_text:
            # Consecutive race warning — check mood
            mood = game_state.mood
            if mood in (Mood.BAD, Mood.TERRIBLE):
                logger.info("Warning: cancelling race — mood is %s", mood.value)
                proceed = False
            else:
                logger.info(
                    "Warning: proceeding with consecutive race (mood=%s)",
                    mood.value,
                )
        else:
            # Unknown warning — default to proceeding
            logger.info("Warning: unknown type, proceeding (OK)")

        if proceed:
            ok_btn = get_tap_center(WARNING_POPUP_REGIONS["btn_ok"])
            self.injector.tap(*ok_btn)
            logger.debug("Tapped OK on warning popup")
        else:
            cancel_btn = get_tap_center(WARNING_POPUP_REGIONS["btn_cancel"])
            self.injector.tap(*cancel_btn)
            logger.debug("Tapped Cancel on warning popup")

        time.sleep(ANIMATION_SETTLE_TIME)

    def _handle_post_race(self) -> None:
        """Tap through post-race screens (results, rival, pts, standings, rewards).

        Post-race flow has multiple screens that all need a tap to advance:
        1. Race result placement — TAP prompt at bottom center
        2. Rival comparison — TAP prompt at bottom center
        3. Result Pts popup — Close button at ~(520, 1250)
        4. Race standings — Next button (green) at ~(765, 1760)
        5. Race rewards/fans — Next button (green) at ~(765, 1760)

        Rather than identifying each sub-screen, we tap both the center
        (for TAP prompts and Close) and the green Next button area.
        The POST_RACE anchor detects the green Next button, so when we
        land here we're usually on standings or rewards.
        """
        logger.info("Post-race screen: tapping Next")
        # Tap green Next button (right side, not Try Again on left)
        self.injector.tap(765, 1760)
        time.sleep(2.0)

    def _handle_unknown_screen(self, game_state: GameState) -> None:
        """Handle consecutive unknown screen readings.

        When we've recently been in a race (IN_RACE state or just came from
        a race action), unknown screens are likely post-race transitional
        screens (TAP prompts, Close popups, rival comparisons). In that case,
        aggressively tap through them instead of waiting for the threshold.
        """
        self._consecutive_unknown += 1
        logger.debug("Unknown screen #%d (fsm_state=%s)", self._consecutive_unknown, self.state.value)

        # Post-race tap-through: if we were recently in a race, tap through
        # unknown screens aggressively. These are likely TAP prompts, Close
        # popups, or other post-race transitional screens.
        if self._consecutive_unknown <= UNKNOWN_SCREEN_THRESHOLD:
            # Tap bottom-center (for TAP prompts) and mid-center (for Close)
            logger.debug("Tapping through unknown screen (possible post-race)")
            self.injector.tap(540, 1675)  # Bottom center — TAP prompt area
            time.sleep(1.5)
            return

        if self._consecutive_unknown >= UNKNOWN_SCREEN_THRESHOLD:
            logger.warning("Too many unknown screens — entering error recovery")
            if self.state != FSMState.ERROR_RECOVERY:
                self._transition(FSMState.ERROR_RECOVERY)
            self._handle_error_recovery(game_state)
            self._consecutive_unknown = 0

    def _handle_error_recovery(self, game_state: GameState) -> None:
        """Attempt to recover from an unrecognized screen."""
        self._recovery_attempts += 1
        logger.warning("Error recovery attempt %d/%d", self._recovery_attempts, MAX_RECOVERY_ATTEMPTS)

        if self._recovery_attempts > MAX_RECOVERY_ATTEMPTS:
            logger.error("Recovery failed after %d attempts — pausing bot", MAX_RECOVERY_ATTEMPTS)
            self.pause()
            self._transition(FSMState.PAUSED)
            self.status.error_count += 1
            return

        self.sequences.attempt_error_recovery()
        time.sleep(3.0)

        # Re-evaluate
        try:
            frame = self.capture.grab_frame()
            new_state = self.assembler.assemble(frame)
            if new_state.screen != ScreenState.UNKNOWN:
                logger.info("Recovery succeeded: screen=%s", new_state.screen.value)
                self._transition(FSMState.RUNNING_TURN)
                self._recovery_attempts = 0
        except Exception as e:
            logger.error("Recovery capture failed: %s", e)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        """Connect ADB and initialize subsystems."""
        logger.info("Initializing perception pipeline...")

        logger.info("Connecting ADB...")
        if not self.injector.adb.connect():
            logger.warning("ADB not connected — input injection will fail")

        logger.info("Initialization complete")

    def _wait_for_game(self) -> None:
        """Poll until the game screen is detected.

        If we see too many consecutive UNKNOWN screens, start tapping
        to try to advance past transitional screens (post-race, popups).
        """
        logger.info("Waiting for game to reach main menu...")
        poll_interval = 1.0 / self.config.capture.fps_passive
        unknown_count = 0

        while not self._stop_requested.is_set():
            try:
                frame = self.capture.grab_frame()
                state = self.assembler.assemble(frame)
                self.status.update(self.state, state)

                if state.screen not in (ScreenState.UNKNOWN, ScreenState.LOADING):
                    logger.info("Game detected: %s", state.screen.value)
                    return

                if state.screen == ScreenState.UNKNOWN:
                    unknown_count += 1
                    if unknown_count >= 5:
                        logger.info("Tapping to advance past unknown screen during wait")
                        self.injector.tap(540, 1675)
                        unknown_count = 0
                else:
                    unknown_count = 0
            except Exception as e:
                logger.warning("Waiting for game — capture/assemble error: %s", e)

            time.sleep(poll_interval)

    def _shutdown(self) -> None:
        """Clean up resources."""
        logger.info("GameFSM shutting down")
        try:
            self.capture.stop()
        except Exception:
            pass
        self.state = FSMState.SHUTDOWN

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _transition(self, new_state: FSMState) -> None:
        if new_state not in TRANSITIONS.get(self.state, frozenset()):
            raise InvalidTransitionError(self.state, new_state)
        logger.debug("FSM: %s → %s", self.state.value, new_state.value)
        self.state = new_state
        self.status.update(new_state)

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop_requested.is_set():
                return
            time.sleep(min(0.2, end - time.monotonic()))
