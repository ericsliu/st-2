"""TurnExecutor — shared single-turn logic used by both script and FSM.

Encapsulates the full perception-decision-action cycle for one turn:
  1. Identify screen, ensure career home
  2. Handle Race Day
  3. Check conditions
  4. Prepare item queue
  5. Check skill points, visit shop if due
  6. Check mood/conditions for infirmary/go-out
  7. Get race decision
  8. If no race, check rest threshold
  9. Otherwise, execute training (with whistle/boost item logic)
  10. Post-action: wait_for_career_home
  11. Post-turn: skill buying if SP above reserve
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from uma_trainer.types import ActionType, BotAction, ScreenState

if TYPE_CHECKING:
    from uma_trainer.action.game_actions import GameActionExecutor
    from uma_trainer.core.run_context import RunContext
    from uma_trainer.decision.strategy import DecisionEngine
    from uma_trainer.state.provider import GameStateProvider

logger = logging.getLogger(__name__)


def _get_sp_reserve(engine: "DecisionEngine") -> int:
    """Read skill_pts_reserve from strategy overrides, default 800."""
    if engine.scorer.overrides:
        raw = engine.scorer.overrides.get_strategy_raw()
        return raw.get("skill_pts_reserve", 800)
    return 800


class TurnExecutor:
    """Executes one full turn of the game.

    Both the single-turn script and the FSM create a TurnExecutor and
    call execute_turn(). The only difference is which GameStateProvider
    is used (OCR vs Tracked).
    """

    def __init__(
        self,
        provider: "GameStateProvider",
        actions: "GameActionExecutor",
        engine: "DecisionEngine",
        context: "RunContext",
    ) -> None:
        self.provider = provider
        self.actions = actions
        self.engine = engine
        self.context = context

    def execute_turn(self, execute: bool = True, force_rest: bool = False) -> bool:
        """Run a single turn. Returns True if successful.

        Args:
            execute: If False, dry-run mode (shows decision, no taps).
            force_rest: Override all decisions and rest.
        """
        self.context.reset_turn()

        # Step 1: Identify screen
        logger.info("=" * 60)
        logger.info("STEP 1: Identifying screen (no taps)")
        state = self.provider.get_state()
        is_stat_select = self.provider.is_stat_selection()

        logger.info("Screen: %s, stat_selection: %s, energy: %d, mood: %s, turn: %d",
                     state.screen.value, is_stat_select, state.energy,
                     state.mood.value, state.current_turn)

        if state.screen != ScreenState.TRAINING:
            logger.info("Not on career home (got %s) — tapping through", state.screen.value)
            state = self.actions.wait_for_career_home(engine=self.engine)
            if state is None or state.screen != ScreenState.TRAINING:
                logger.error("Could not reach career home. Aborting.")
                return False
            self.provider.invalidate()
            is_stat_select = self.provider.is_stat_selection()

        # Step 2: If on stat selection, go back to career home
        if is_stat_select:
            logger.info("On stat selection — tapping Back to reach career home")
            self.actions.injector.tap(95, 1875)
            time.sleep(2.0)
            state = self.provider.get_state()
            is_stat_select = self.provider.is_stat_selection()
            if is_stat_select:
                logger.error("Still on stat selection after Back tap")
                return False

        # Step 2.1: Race Day — skip mood/rest/training
        if state.is_race_day:
            print(f"\nDecision: RACE (Race Day — mandatory)")
            if not execute:
                print("[DRY RUN] Would enter TS Climax Race.")
                return True
            self.actions.execute_race_day(state, self.engine)
            self.context.on_race_completed(self.engine.race_selector.scenario)
            return True

        # Step 2.3: Check conditions + aptitudes
        if execute:
            conditions, aptitudes = self.actions.check_conditions()
            if conditions:
                state.active_conditions = conditions
                logger.info("Active conditions: %s", [c.value for c in conditions])
            if aptitudes:
                self.provider.assembler.trainee_aptitudes = aptitudes
                state.trainee_aptitudes = aptitudes

        # Step 2.5: Prepare item queue
        from uma_trainer.decision.shop_manager import ITEM_CATALOGUE
        deferred_boost_items = []
        has_whistle = False

        item_queue = self.engine.shop_manager.get_item_queue(state)
        if item_queue:
            for a in item_queue:
                if a.target == "reset_whistle":
                    has_whistle = True
                else:
                    item_key = a.target
                    item_name = ITEM_CATALOGUE[item_key].name if item_key in ITEM_CATALOGUE else item_key
                    deferred_boost_items.append((item_key, item_name))
                    if not execute:
                        print(f"  [DRY RUN] Would use item: {item_name} (deferred)")
                        self.context.items_used_this_turn.append(item_name)

        # Log skill points
        if state.skill_pts > 0:
            sp_reserve = _get_sp_reserve(self.engine)
            logger.info("Skill pts: %d (reserve: %d, spendable: %d)",
                         state.skill_pts, sp_reserve, max(0, state.skill_pts - sp_reserve))

        # Step 2.6: Visit shop if due
        if self.engine.shop_manager.should_visit_shop(state):
            if execute:
                logger.info("Shop visit due — visiting before main action")
                self.actions.execute_shop_visit(self.engine)
                self.context.clear_just_raced()
                state = self.provider.get_state()
            else:
                logger.info("[DRY RUN] Would visit shop this turn")

        # Step 2.7: Check mood/conditions for infirmary/go-out
        infirmary_action = self.engine.scorer.should_visit_infirmary(state)
        if infirmary_action:
            print(f"\nDecision: INFIRMARY ({infirmary_action.reason})")
            if not execute:
                print("[DRY RUN] Would tap Infirmary.")
            else:
                logger.info("STEP 3: Executing INFIRMARY")
                self.actions.execute_infirmary()
                logger.info("STEP 4: Handling post-infirmary flow")
                result_state = self.actions.wait_for_career_home(engine=self.engine)
                if result_state:
                    logger.info("Turn complete. Energy: %d, Mood: %s",
                                result_state.energy, result_state.mood.value)
            return True

        go_out_action = self.engine.scorer.should_go_out(state)
        if go_out_action:
            print(f"\nDecision: GO OUT ({go_out_action.reason})")
            if not execute:
                print("[DRY RUN] Would tap Recreation.")
            else:
                logger.info("STEP 3: Executing GO OUT")
                self.actions.execute_go_out()
                logger.info("STEP 4: Handling post-go-out flow")
                result_state = self.actions.wait_for_career_home(engine=self.engine)
                if result_state:
                    logger.info("Turn complete. Energy: %d, Mood: %s",
                                result_state.energy, result_state.mood.value)
            return True

        # Step 3: Decision
        logger.info("STEP 2: Getting decision from DecisionEngine")

        # Force rest
        if force_rest:
            print(f"\nDecision: REST (forced, energy {state.energy})")
            if not execute:
                print("[DRY RUN] Would tap Rest button.")
                return True
            logger.info("STEP 3: Executing forced REST")
            self.actions.execute_rest()
            logger.info("STEP 4: Handling post-rest flow")
            result_state = self.actions.wait_for_career_home(engine=self.engine)
            if result_state:
                logger.info("Turn complete. Energy: %d, Mood: %s",
                            result_state.energy, result_state.mood.value)
            return True

        # Check if goal race warning popup was seen (forces racing)
        goal_urgent = self.actions.goal_race_urgent or self.context.goal_race_urgent
        if goal_urgent:
            logger.info("Goal race urgent flag set — forcing race this turn")
            from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
            races_btn = get_tap_center(TURN_ACTION_REGIONS["btn_races"])
            race_action = BotAction(
                action_type=ActionType.RACE,
                tap_coords=races_btn,
                reason="Goal race urgent (popup warning)",
                tier_used=1,
            )
            self.actions.goal_race_urgent = False
            self.context.goal_race_urgent = False
            self.context.save_to_disk()
        else:
            # Check if we should race
            race_action = self.engine.race_selector.should_race_this_turn(state)

        if race_action:
            pre = self.engine.race_selector.pre_select_race(state)
            if pre:
                logger.info("Pre-selected: %s (%s, %dm)", pre.name, pre.grade, pre.distance)

        # Bond urgency / extraordinary training can override non-mandatory races
        is_mandatory = race_action and ("Goal race" in race_action.reason or "urgent" in race_action.reason)
        is_g1 = race_action and "G1 available" in race_action.reason
        if race_action and not is_mandatory:
            need_scan = False
            # Bond urgency check — but NOT for G1 races (G1 > bonds)
            bond_deadline = self.engine.scorer._get_friendship_deadline(state)
            if state.current_turn < bond_deadline and not is_g1:
                need_scan = True
            # G1 races can be overridden by extraordinary training (70+ total)
            if is_g1:
                need_scan = True

            if need_scan:
                logger.info("Scanning tiles to check overrides before racing...")
                from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
                train_btn = get_tap_center(TURN_ACTION_REGIONS["btn_training"])
                self.actions.injector.tap(*train_btn)
                time.sleep(2.0)

                self.provider.invalidate()
                scan_state = self.provider.get_state()
                is_stat = self.provider.is_stat_selection()

                if is_stat and scan_state.training_tiles:
                    self.actions.sequences.scan_training_gains(
                        scan_state, self.provider.capture, self.actions.assembler)

                    # Extraordinary training override for G1 races
                    if "G1 available" in race_action.reason:
                        best_gain = max(t.total_stat_gain for t in scan_state.training_tiles)
                        if best_gain >= 70:
                            logger.info(
                                "EXTRAORDINARY TRAINING (%d stats) — overriding G1 race",
                                best_gain,
                            )
                            race_action = None

                    # Bond urgency override — skip for G1 races
                    if race_action and state.current_turn < bond_deadline and not is_g1:
                        high_bond = self.engine.scorer.has_high_bond_urgency(scan_state)
                        if high_bond:
                            logger.info("HIGH BOND URGENCY — overriding race")
                            race_action = None
                        else:
                            logger.info("Bond urgency not high — proceeding with race")

                logger.info("Returning to career home after override check")
                self.actions.injector.tap(95, 1875)
                time.sleep(2.0)

        if race_action:
            pre = self.engine.race_selector._pre_selected
            print(f"\nDecision: RACE")
            print(f"Reason: {race_action.reason}")
            if pre:
                print(f"Target: {pre.name} ({pre.grade}, {pre.distance}m, {pre.surface})")

            if not execute:
                print("[DRY RUN] Would enter race list.")
                return True

            logger.info("STEP 3: Executing RACE")
            success = self.actions.execute_race_entry(race_selector=self.engine.race_selector)
            if not success:
                logger.warning("Race entry failed (button inactive?) — falling back to training")
                return self._execute_training_flow(
                    state, execute, has_whistle, deferred_boost_items,
                )

            logger.info("STEP 4: Handling post-race flow")
            result_state = self.actions.wait_for_career_home(engine=self.engine)
            if result_state:
                logger.info("Turn complete. Energy: %d, Mood: %s",
                            result_state.energy, result_state.mood.value)
            self.context.on_race_completed(self.engine.race_selector.scenario)
            return True

        # No race — check if rest is needed
        rest_needed = self.engine.scorer.should_rest(state)
        if rest_needed:
            print(f"\nDecision: REST (energy {state.energy} below threshold)")
            if not execute:
                print("[DRY RUN] Would tap Rest.")
                return True
            logger.info("STEP 3: Executing REST")
            self.actions.execute_rest()
            logger.info("STEP 4: Handling post-rest flow")
            result_state = self.actions.wait_for_career_home(engine=self.engine)
            if result_state:
                logger.info("Turn complete. Energy: %d, Mood: %s",
                            result_state.energy, result_state.mood.value)
            self.context.on_non_race_action(self.engine.race_selector)
            return True

        # Training flow
        return self._execute_training_flow(
            state, execute, has_whistle, deferred_boost_items,
        )

    def _execute_training_flow(
        self,
        state,
        execute: bool,
        has_whistle: bool,
        deferred_boost_items: list[tuple[str, str]],
    ) -> bool:
        """Handle the training sub-flow (scan, whistle, boost items, confirm)."""
        from uma_trainer.decision.shop_manager import ITEM_CATALOGUE
        from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center

        SUMMER_MIN_STATS = 40

        logger.info("STEP 3: Navigating to stat selection")
        train_btn = get_tap_center(TURN_ACTION_REGIONS["btn_training"])
        self.actions.injector.tap(*train_btn)
        time.sleep(2.0)

        self.provider.invalidate()
        state = self.provider.get_state()
        is_stat_select = self.provider.is_stat_selection()

        if not is_stat_select or not state.training_tiles:
            logger.error("Failed to reach stat selection.")
            return False

        self.actions.sequences.scan_training_gains(
            state, self.provider.capture, self.actions.assembler)

        # Check if whistle should be used
        is_summer = self.engine.scorer._is_summer_camp(state)
        if is_summer and has_whistle and execute:
            best_total = max(
                (sum(t.stat_gains.values()) if t.stat_gains else 0)
                for t in state.training_tiles
            )
            if best_total < SUMMER_MIN_STATS:
                logger.info("Summer camp: best tile only %d — using Reset Whistle", best_total)
                self.actions.injector.tap(95, 1875)
                time.sleep(2.0)

                whistle_name = ITEM_CATALOGUE["reset_whistle"].name
                success = self.actions.sequences.execute_item_use(
                    "reset_whistle", whistle_name, self.provider.capture, self.actions.ocr)
                if success:
                    self.engine.shop_manager.consume_item("reset_whistle")
                    self.engine.shop_manager.activate_item("reset_whistle")
                    self.context.items_used_this_turn.append(whistle_name)
                    has_whistle = False

                self.actions.injector.tap(*train_btn)
                time.sleep(2.0)
                self.provider.invalidate()
                state = self.provider.get_state()
                is_stat_select = self.provider.is_stat_selection()
                if is_stat_select and state.training_tiles:
                    self.actions.sequences.scan_training_gains(
                        state, self.provider.capture, self.actions.assembler)
                else:
                    logger.error("Failed to re-enter stat selection after whistle")
                    return False

        # Score and display
        scored = self.actions.display_training_scores(state, self.engine)
        action = self.engine.scorer.best_action(state)

        if action.action_type == ActionType.REST:
            logger.info("Scorer says REST: %s", action.reason)
        else:
            best_tile, best_score = scored[0]
            print(f"\nDecision: TRAIN {best_tile.stat_type.value} (score={best_score:.1f})")
            print(f"Reason: {action.reason}")

        if not execute:
            print("[DRY RUN] Not confirming. Run with --execute to act.")
            self.actions.injector.tap(95, 1875)
            time.sleep(1.5)
            return True

        if action.action_type == ActionType.REST:
            self.actions.injector.tap(95, 1875)
            time.sleep(2.0)
            self.actions.execute_rest()
        else:
            # Use deferred boost items before confirming training
            if deferred_boost_items:
                logger.info("Using %d boost items in batch", len(deferred_boost_items))
                self.actions.injector.tap(95, 1875)
                time.sleep(2.0)

                used_keys = self.actions.sequences.execute_item_batch(
                    deferred_boost_items, self.provider.capture, self.actions.ocr)
                for key in used_keys:
                    self.engine.shop_manager.consume_item(key)
                    self.engine.shop_manager.activate_item(key)
                    name = ITEM_CATALOGUE[key].name if key in ITEM_CATALOGUE else key
                    self.context.items_used_this_turn.append(name)

                if self.context.items_used_this_turn:
                    print(f"Items used: {', '.join(self.context.items_used_this_turn)}")

                # Verify item bag is closed before re-entering training
                for close_attempt in range(3):
                    frame = self.provider.refresh_frame()
                    bag_text = self.actions.ocr.read_region(frame, (0, 0, 400, 60)).lower()
                    if "training item" in bag_text or "item" in bag_text:
                        logger.warning("Item bag still open (attempt %d) — tapping Close",
                                       close_attempt + 1)
                        self.actions.injector.tap(110, 1750)
                        time.sleep(1.5)
                    else:
                        break

                self.actions.injector.tap(*train_btn)
                time.sleep(2.0)

            # Select and confirm training
            best_tile, best_score = scored[0]
            frame = self.provider.refresh_frame()
            currently_raised = self.actions.assembler.detect_selected_tile(frame)
            if currently_raised != best_tile.position:
                logger.info("Selecting %s tile", best_tile.stat_type.value)
                self.actions.injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
                time.sleep(0.8)

            logger.info("Confirming %s", best_tile.stat_type.value)
            self.actions.injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
            time.sleep(3.0)

        # Handle post-training events
        logger.info("STEP 4: Handling post-training flow")
        result_state = self.actions.wait_for_career_home(engine=self.engine)
        if result_state:
            logger.info("Turn complete. Energy: %d, Mood: %s",
                        result_state.energy, result_state.mood.value)
        self.context.on_non_race_action(self.engine.race_selector)
        return True

    def post_turn_skill_check(self) -> None:
        """After a turn, buy skills if SP is above reserve threshold."""
        state = self.provider.get_state()
        sp_reserve = _get_sp_reserve(self.engine)
        if state.skill_pts > sp_reserve:
            self.actions.execute_skill_buying(
                state, self.engine, sp_reserve=sp_reserve,
            )
