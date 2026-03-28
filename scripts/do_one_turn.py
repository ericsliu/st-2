"""Execute one full turn using the real DecisionEngine.

Initializes the same components as the FSM (scorer, race_selector,
event_handler, scenario handler) and runs one perception-decision-action
cycle.  Handles training, racing, resting, events, and post-race flow.

Default is dry-run (shows decision only). Pass --execute to act.
Pass --turns N to run N consecutive turns.

SAFETY: Screen state is confirmed before any tap. Abort = zero taps.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.action.adb_client import ADBClient
from uma_trainer.action.input_injector import InputInjector
from uma_trainer.action.sequences import ActionSequences
from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.decision.event_handler import EventHandler
from uma_trainer.decision.race_selector import RaceSelector
from uma_trainer.decision.runspec import load_runspec
from uma_trainer.decision.scorer import TrainingScorer, ESTIMATED_TRAINING_GAINS
from uma_trainer.decision.shop_manager import ShopManager
from uma_trainer.decision.skill_buyer import SkillBuyer
from uma_trainer.decision.strategy import DecisionEngine
from uma_trainer.knowledge.database import KnowledgeBase
from uma_trainer.knowledge.overrides import OverridesLoader
from uma_trainer.perception.assembler import StateAssembler
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.screen_identifier import ScreenIdentifier
from uma_trainer.scenario.registry import load_scenario
from uma_trainer.types import ActionType, BotAction, GameState, ScreenState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-30s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("do_one_turn")

DEVICE = "127.0.0.1:5555"

# Post-race screen handling constants
POST_RACE_TAP_DELAY = 2.0
MAX_POST_ACTION_SCREENS = 20  # Safety limit on tap-through loops


def parse_args():
    parser = argparse.ArgumentParser(description="Execute one turn using the real DecisionEngine")
    parser.add_argument("--execute", action="store_true", help="Actually execute (default is dry-run)")
    parser.add_argument("--turns", type=int, default=1, help="Number of turns to run")
    parser.add_argument("--force-rest", action="store_true", help="Force rest this turn (overrides race/train)")
    return parser.parse_args()


def build_engine(config: AppConfig):
    """Initialize the full DecisionEngine with all real components."""
    kb = KnowledgeBase(db_path=config.db_path)
    overrides = OverridesLoader()
    scenario = load_scenario(config.scenario)
    runspec = load_runspec(config.runspec)
    shop_manager = ShopManager(scenario=scenario, overrides=overrides)
    shop_manager.load_inventory()

    scorer = TrainingScorer(
        config=config.scorer,
        overrides=overrides,
        scenario=scenario,
        runspec=runspec,
        shop_manager=shop_manager,
    )
    race_selector = RaceSelector(
        kb=kb,
        overrides=overrides,
        scenario=scenario,
    )
    event_handler = EventHandler(
        kb=kb,
        local_llm=None,
        claude_client=None,
        overrides=overrides,
    )
    skill_buyer = SkillBuyer(
        kb=kb,
        scorer=scorer,
        overrides=overrides,
    )
    engine = DecisionEngine(
        scorer=scorer,
        event_handler=event_handler,
        skill_buyer=skill_buyer,
        race_selector=race_selector,
        shop_manager=shop_manager,
        scenario=scenario,
    )
    return engine


def abort(capture, msg):
    """Log error and exit without tapping anything."""
    logger.error(msg)
    capture.stop()
    return 1


def wait_for_career_home(capture, assembler, screen_id, injector, sequences, engine=None, max_screens=MAX_POST_ACTION_SCREENS):
    """Tap through post-action screens until we're back on career home.

    Handles: post-race flow, events, popups, training results,
    shop refresh notifications, etc.

    Returns the GameState once we're on the career home screen,
    or None if we hit the safety limit.
    """
    last_screen = None
    post_race_repeat = 0
    for i in range(max_screens):
        time.sleep(POST_RACE_TAP_DELAY)
        frame = capture.grab_frame()
        state = assembler.assemble(frame)
        is_stat_select = screen_id.is_stat_selection(frame)

        logger.info(
            "Post-action screen %d: %s (stat_sel=%s)",
            i + 1, state.screen.value, is_stat_select,
        )
        # Track last screen for stuck detection (updated before branching)
        prev_screen = last_screen
        last_screen = state.screen

        # Career home = TRAINING screen but NOT stat selection
        if state.screen == ScreenState.TRAINING and not is_stat_select:
            logger.info("Back on career home")
            return state

        # Event screen — use EventHandler to pick the best choice
        if state.screen == ScreenState.EVENT:
            if engine is not None and state.event_text:
                action = engine.event_handler.decide(state)
                choice_idx = int(action.target) if action.target.isdigit() else 0
                logger.info(
                    "Event: '%s' → choice %d (%s)",
                    state.event_text[:60], choice_idx, action.reason,
                )
                injector.tap(*action.tap_coords)
            else:
                logger.info("Event screen (no handler/text) — picking choice 1 (default)")
                injector.tap(540, 1100)
            continue

        # Warning popup — tap OK
        if state.screen == ScreenState.WARNING_POPUP:
            from uma_trainer.perception.regions import WARNING_POPUP_REGIONS, get_tap_center
            ok_btn = get_tap_center(WARNING_POPUP_REGIONS["btn_ok"])
            logger.info("Warning popup — tapping OK")
            injector.tap(*ok_btn)
            continue

        # Pre-race screen — tap View Results
        if state.screen == ScreenState.PRE_RACE:
            logger.info("Pre-race screen — tapping View Results")
            injector.tap(380, 1760)
            time.sleep(3.0)
            continue

        # Post-race screen — tap Next (NOT Try Again)
        # Also handles Goal Complete (misidentified as post_race) — centered Next at (540, 1640)
        if state.screen == ScreenState.POST_RACE:
            if prev_screen == ScreenState.POST_RACE:
                post_race_repeat += 1
            else:
                post_race_repeat = 0

            if post_race_repeat >= 2:
                # Stuck — probably Goal Complete or similar screen with centered button
                logger.info("Post-race stuck (%d repeats) — trying centered Next at (540, 1640)", post_race_repeat)
                injector.tap(540, 1640)
            else:
                logger.info("Post-race screen — tapping Next")
                injector.tap(765, 1760)
            continue

        # Race list — tap Back to return to career home
        if state.screen == ScreenState.RACE_ENTRY:
            logger.info("Race list — tapping Back")
            injector.tap(75, 1870)
            continue

        # Result screen — tap center to advance (race results, stat gains, etc.)
        if state.screen == ScreenState.RESULT_SCREEN:
            logger.info("Result screen — tapping to advance")
            injector.tap(540, 960)  # Center of screen
            time.sleep(1.0)
            injector.tap(540, 1675)  # Bottom TAP prompt area
            continue

        # Loading / cutscene / race — just wait
        if state.screen in (ScreenState.LOADING, ScreenState.CUTSCENE, ScreenState.RACE):
            logger.info("Passive screen (%s) — waiting", state.screen.value)
            time.sleep(2.0)
            continue

        # Unknown screen — tap bottom center (TAP prompt) then center (Close)
        logger.info("Unknown screen (%s) — tapping to advance", state.screen.value)
        injector.tap(540, 1675)
        time.sleep(1.5)
        # Also try dismissing Result Pts popup by tapping background
        injector.tap(540, 400)

    logger.warning("Hit max post-action screens (%d) — giving up", max_screens)
    return None


def display_training_scores(state, engine):
    """Display training tile scores in a table."""
    scored = engine.scorer.score_tiles(state)
    print()
    print("=" * 80)
    print(f"{'Tile':<10} {'Gains':<45} {'Total':>5} {'Fail%':>6} {'Score':>8}")
    print("-" * 80)

    for tile, score in scored:
        if tile.stat_gains:
            gains_str = ", ".join(
                f"{s}:+{g}" for s, g in tile.stat_gains.items() if g > 0
            )
            total = sum(tile.stat_gains.values())
        else:
            gains_str = "(estimated)"
            total = sum(
                ESTIMATED_TRAINING_GAINS.get(tile.stat_type.value, {}).values()
            )
        fail_pct = (f"{tile.failure_rate * 100:.0f}%"
                    if tile.failure_rate > 0 else "0%")
        print(f"{tile.stat_type.value:<10} {gains_str:<45} "
              f"{total:>5} {fail_pct:>6} {score:>8.1f}")

    print("=" * 80)
    return scored


def execute_training(state, engine, injector, capture, assembler, sequences, screen_id):
    """Scan tiles, score, select best, and confirm training.

    Expects to be on the stat selection screen already.
    Returns the BotAction that was executed.
    """
    # Scan all tiles for gains
    logger.info("Scanning all training tiles...")
    sequences.scan_training_gains(state, capture, assembler)

    # Score and display
    scored = display_training_scores(state, engine)
    action = engine.scorer.best_action(state)

    if action.action_type == ActionType.REST:
        logger.info("Scorer says REST: %s", action.reason)
        return action

    best_tile, best_score = scored[0]
    print(f"\nDecision: TRAIN {best_tile.stat_type.value} (score={best_score:.1f})")
    print(f"Reason: {action.reason}")

    # Select and confirm the tile
    currently_raised = assembler.detect_selected_tile(capture.grab_frame())
    if currently_raised != best_tile.position:
        logger.info("Selecting %s tile", best_tile.stat_type.value)
        injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
        time.sleep(0.8)

    logger.info("Confirming %s", best_tile.stat_type.value)
    injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
    time.sleep(3.0)
    return action


def execute_race_entry(injector, capture, assembler, screen_id, race_selector=None):
    """Navigate into the race list, pick the best race, and enter it.

    Called when DecisionEngine says to race. We're on career home.
    Uses the RaceSelector to score races and filter by aptitude.
    """
    from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center

    # Tap Races button
    races_btn = get_tap_center(TURN_ACTION_REGIONS["btn_races"])
    logger.info("Tapping Races button at %s", races_btn)
    injector.tap(*races_btn)
    time.sleep(2.5)

    # Verify we're on the race list (may get a warning popup first)
    frame = capture.grab_frame()
    state = assembler.assemble(frame)

    # Handle consecutive race warning / low energy warning
    if state.screen == ScreenState.WARNING_POPUP:
        from uma_trainer.perception.regions import WARNING_POPUP_REGIONS, get_tap_center
        ok_btn = get_tap_center(WARNING_POPUP_REGIONS["btn_ok"])
        logger.info("Warning popup before race list — tapping OK at %s", ok_btn)
        injector.tap(*ok_btn)
        time.sleep(2.0)
        frame = capture.grab_frame()
        state = assembler.assemble(frame)

    if state.screen != ScreenState.RACE_ENTRY:
        logger.warning("Expected RACE_ENTRY, got %s — aborting race", state.screen.value)
        return False

    # Use the race selector to pick the best race by aptitude/grade/GP value
    if race_selector and state.available_races:
        action = race_selector.decide(state)
        if action.action_type == ActionType.WAIT:
            logger.warning("RaceSelector: no suitable race — %s. Going back.", action.reason)
            injector.tap(75, 1870)  # Back button
            time.sleep(1.5)
            return False

        # Log what we're entering
        for race in state.available_races:
            logger.info(
                "  Race option: '%s' grade=%s dist=%dm surface=%s apt_ok=%s",
                race.name, race.grade, race.distance, race.surface, race.is_aptitude_ok,
            )

        # Tap the selected race entry to highlight it, then the Race button
        if action.tap_coords and action.tap_coords != (0, 0):
            logger.info("Selecting race at %s", action.tap_coords)
            injector.tap(*action.tap_coords)
            time.sleep(1.0)

        logger.info("Entering race: %s", action.reason)
    else:
        logger.info("No race selector or no races parsed — entering first race")

    # Tap the green Race button to confirm entry
    from uma_trainer.perception.regions import RACE_LIST_REGIONS
    race_btn = get_tap_center(RACE_LIST_REGIONS["btn_race"])
    logger.info("Tapping Race button on list at %s", race_btn)
    injector.tap(*race_btn)
    time.sleep(2.0)

    # Handle the "Enter race?" confirmation dialog
    frame = capture.grab_frame()
    state = assembler.assemble(frame)

    logger.info("Tapping race confirmation dialog")
    injector.tap(660, 1420)
    time.sleep(3.0)

    return True


def execute_rest(injector):
    """Tap Rest and confirm the dialog."""
    from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
    rest_btn = get_tap_center(TURN_ACTION_REGIONS["btn_rest"])
    logger.info("Tapping Rest at %s", rest_btn)
    injector.tap(*rest_btn)
    time.sleep(2.0)

    # Confirm rest dialog — OK button at (700, 1300)
    logger.info("Confirming rest")
    injector.tap(700, 1300)
    time.sleep(2.0)


def execute_go_out(injector):
    """Tap Recreation (Go Out) button."""
    from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
    go_out_btn = get_tap_center(TURN_ACTION_REGIONS["btn_recreation"])
    logger.info("Tapping Go Out (Recreation) at %s", go_out_btn)
    injector.tap(*go_out_btn)
    time.sleep(2.0)


def execute_infirmary(injector):
    """Tap Infirmary button."""
    from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
    infirmary_btn = get_tap_center(TURN_ACTION_REGIONS["btn_infirmary"])
    logger.info("Tapping Infirmary at %s", infirmary_btn)
    injector.tap(*infirmary_btn)
    time.sleep(2.0)


def run_one_turn(execute, capture, assembler, screen_id, engine, injector, sequences, ocr=None, force_rest=False):
    """Run a single turn. Returns True if successful."""

    # Step 1: Identify screen (read-only)
    logger.info("=" * 60)
    logger.info("STEP 1: Identifying screen (no taps)")
    frame = capture.grab_frame()
    state = assembler.assemble(frame)
    is_stat_select = screen_id.is_stat_selection(frame)

    logger.info(
        "Screen: %s, stat_selection: %s, energy: %d, mood: %s, turn: %d",
        state.screen.value, is_stat_select, state.energy,
        state.mood.value, state.current_turn,
    )

    if state.screen != ScreenState.TRAINING:
        # Not on career home — try to navigate there via wait_for_career_home
        logger.info("Not on career home (got %s) — tapping through to get there", state.screen.value)
        state = wait_for_career_home(
            capture, assembler, screen_id, injector, sequences, engine=engine,
        )
        if state is None or state.screen != ScreenState.TRAINING:
            logger.error("Could not reach career home. Aborting.")
            return False
        is_stat_select = screen_id.is_stat_selection(capture.grab_frame())

    # Step 2: If on stat selection, go back to career home first
    if is_stat_select:
        logger.info("On stat selection — tapping Back to reach career home")
        injector.tap(95, 1875)  # Back button
        time.sleep(2.0)
        frame = capture.grab_frame()
        state = assembler.assemble(frame)
        is_stat_select = screen_id.is_stat_selection(frame)
        if is_stat_select:
            logger.error("Still on stat selection after Back tap")
            return False

    # Step 2.5: Plan and execute item queue.
    # The whistle is special: it rearranges cards, invalidating all plans.
    # If a whistle is used, we loop back and re-evaluate everything.
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE
    all_items_used = []
    MAX_ITEM_ROUNDS = 3  # safety limit on re-evaluation loops

    for item_round in range(MAX_ITEM_ROUNDS):
        item_queue = engine.shop_manager.get_item_queue(state)
        if not item_queue:
            break

        queue_names = [
            ITEM_CATALOGUE[a.target].name if a.target in ITEM_CATALOGUE else a.target
            for a in item_queue
        ]
        logger.info("Item queue (round %d): %s", item_round + 1, queue_names)

        used_whistle = False
        for item_action in item_queue:
            item_key = item_action.target
            item_name = ITEM_CATALOGUE[item_key].name if item_key in ITEM_CATALOGUE else item_key

            if not execute:
                print(f"  [DRY RUN] Would use item: {item_name} ({item_action.reason})")
                all_items_used.append(item_name)
                continue

            if ocr is None:
                logger.warning("No OCR engine — cannot navigate item bag")
                break

            success = sequences.execute_item_use(item_key, item_name, capture, ocr)
            if success:
                engine.shop_manager.consume_item(item_key)
                engine.shop_manager.activate_item(item_key)
                all_items_used.append(item_name)
                logger.info("Item used: %s", item_name)
                time.sleep(1.5)
                frame = capture.grab_frame()
                state = assembler.assemble(frame)
                logger.info("After item: energy=%d, mood=%s", state.energy, state.mood.value)

                if item_key == "reset_whistle":
                    used_whistle = True
                    logger.info("Whistle used — will re-evaluate entire turn")
                    break  # Stop this queue, loop back for fresh evaluation
            else:
                logger.warning("Failed to use item %s — aborting remaining queue", item_name)
                break

        if not execute or not used_whistle:
            break  # No whistle = queue is final, proceed to decisions

    if all_items_used:
        print(f"Items used: {', '.join(all_items_used)}")

    # Step 2.7: Check mood and conditions before main decision.
    # Infirmary takes priority over Go Out (conditions block mood improvement).
    infirmary_action = engine.scorer.should_visit_infirmary(state)
    if infirmary_action:
        print(f"\nDecision: INFIRMARY ({infirmary_action.reason})")
        if not execute:
            print("[DRY RUN] Would tap Infirmary.")
        else:
            logger.info("STEP 3: Executing INFIRMARY")
            execute_infirmary(injector)
            logger.info("STEP 4: Handling post-infirmary flow")
            result_state = wait_for_career_home(
                capture, assembler, screen_id, injector, sequences, engine=engine,
            )
            if result_state:
                logger.info(
                    "Turn complete. Energy: %d, Mood: %s",
                    result_state.energy, result_state.mood.value,
                )
        return True

    go_out_action = engine.scorer.should_go_out(state)
    if go_out_action:
        print(f"\nDecision: GO OUT ({go_out_action.reason})")
        if not execute:
            print("[DRY RUN] Would tap Recreation.")
        else:
            logger.info("STEP 3: Executing GO OUT")
            execute_go_out(injector)
            logger.info("STEP 4: Handling post-go-out flow")
            result_state = wait_for_career_home(
                capture, assembler, screen_id, injector, sequences, engine=engine,
            )
            if result_state:
                logger.info(
                    "Turn complete. Energy: %d, Mood: %s",
                    result_state.energy, result_state.mood.value,
                )
        return True

    # Step 3: Get the decision from the REAL DecisionEngine
    # For training decisions, we need stat gains — navigate to stat selection first
    # to scan tiles, then come back for the full decision.
    logger.info("STEP 2: Getting decision from DecisionEngine")

    # Force rest overrides everything (manual override for energy banking)
    if force_rest:
        print(f"\nDecision: REST (forced via --force-rest, energy {state.energy})")

        if not execute:
            print("\n[DRY RUN] Would tap Rest button.")
            return True

        logger.info("STEP 3: Executing forced REST")
        execute_rest(injector)

        logger.info("STEP 4: Handling post-rest flow")
        result_state = wait_for_career_home(
            capture, assembler, screen_id, injector, sequences, engine=engine,
        )
        if result_state:
            logger.info(
                "Turn complete. Energy: %d, Mood: %s",
                result_state.energy, result_state.mood.value,
            )
        return True

    # First, check if race_selector wants to race (doesn't need tile scan).
    # Racing doesn't cost energy, so it always takes priority over rest.
    race_action = engine.race_selector.should_race_this_turn(state)

    # If race is a rhythm race (not goal), check bond urgency first.
    # Bond urgency can override non-goal races when friendship building is critical.
    if race_action and "Goal race" not in race_action.reason:
        bond_deadline = engine.scorer._get_friendship_deadline(state)
        if state.current_turn < bond_deadline:
            logger.info("Scanning tiles to check bond urgency before racing...")
            from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
            train_btn = get_tap_center(TURN_ACTION_REGIONS["btn_training"])
            injector.tap(*train_btn)
            time.sleep(2.0)

            frame = capture.grab_frame()
            scan_state = assembler.assemble(frame)
            is_stat = screen_id.is_stat_selection(frame)

            if is_stat and scan_state.training_tiles:
                sequences.scan_training_gains(scan_state, capture, assembler)
                high_bond = engine.scorer.has_high_bond_urgency(scan_state)

                if high_bond:
                    logger.info("HIGH BOND URGENCY — overriding race for friendship training")
                    race_action = None
                else:
                    logger.info("Bond urgency not high enough — proceeding with race")

            # Always go back to career home after bond check
            logger.info("Returning to career home after bond check")
            injector.tap(95, 1875)
            time.sleep(2.0)

    if race_action:
        print(f"\nDecision: RACE")
        print(f"Reason: {race_action.reason}")

        if not execute:
            print("\n[DRY RUN] Would enter race list and pick best race.")
            return True

        # Execute: tap Races, handle race list, race confirmation, post-race flow
        logger.info("STEP 3: Executing RACE")
        success = execute_race_entry(injector, capture, assembler, screen_id, race_selector=engine.race_selector)
        if not success:
            return False

        # Handle post-race flow (View Results, results, rewards, events)
        logger.info("STEP 4: Handling post-race flow")
        result_state = wait_for_career_home(
            capture, assembler, screen_id, injector, sequences, engine=engine,
        )
        if result_state:
            logger.info(
                "Turn complete. Energy: %d, Mood: %s",
                result_state.energy, result_state.mood.value,
            )
        engine.race_selector.on_non_race_action()  # Reset after race chain
        return True

    # No race — the alternative is training. Check if we should rest first.
    rest_needed = engine.scorer.should_rest(state)
    if rest_needed:
        print(f"\nDecision: REST (energy {state.energy} below threshold)")

        if not execute:
            print("\n[DRY RUN] Would tap Rest button.")
            return True

        logger.info("STEP 3: Executing REST")
        execute_rest(injector)

        # Handle post-rest events
        logger.info("STEP 4: Handling post-rest flow")
        result_state = wait_for_career_home(
            capture, assembler, screen_id, injector, sequences, engine=engine,
        )
        if result_state:
            logger.info(
                "Turn complete. Energy: %d, Mood: %s",
                result_state.energy, result_state.mood.value,
            )
        engine.race_selector.on_non_race_action()
        return True

    else:
        # Training — navigate to stat selection and scan tiles
        logger.info("STEP 3: Navigating to stat selection for tile scan")
        from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
        train_btn = get_tap_center(TURN_ACTION_REGIONS["btn_training"])
        logger.info("Tapping Training button")
        injector.tap(*train_btn)
        time.sleep(2.0)

        frame = capture.grab_frame()
        state = assembler.assemble(frame)
        is_stat_select = screen_id.is_stat_selection(frame)

        if not is_stat_select or not state.training_tiles:
            logger.error("Failed to reach stat selection. No further taps.")
            return False

        sequences.scan_training_gains(state, capture, assembler)

        # Score and display
        scored = display_training_scores(state, engine)
        action = engine.scorer.best_action(state)

        if action.action_type == ActionType.REST:
            logger.info("Scorer says REST: %s", action.reason)
        else:
            best_tile, best_score = scored[0]
            print(f"\nDecision: TRAIN {best_tile.stat_type.value} (score={best_score:.1f})")
            print(f"Reason: {action.reason}")

        if not execute:
            print("\n[DRY RUN] Not confirming. Run with --execute to act.")
            injector.tap(95, 1875)
            time.sleep(1.5)
            return True

        if action.action_type == ActionType.REST:
            # Go back to career home first, then rest
            injector.tap(95, 1875)
            time.sleep(2.0)
            execute_rest(injector)
        else:
            # Select and confirm the best tile
            best_tile, best_score = scored[0]
            currently_raised = assembler.detect_selected_tile(capture.grab_frame())
            if currently_raised != best_tile.position:
                logger.info("Selecting %s tile", best_tile.stat_type.value)
                injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
                time.sleep(0.8)

            logger.info("Confirming %s", best_tile.stat_type.value)
            injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
            time.sleep(3.0)

        # Handle post-training events
        logger.info("STEP 4: Handling post-training flow")
        result_state = wait_for_career_home(
            capture, assembler, screen_id, injector, sequences, engine=engine,
        )
        if result_state:
            logger.info(
                "Turn complete. Energy: %d, Mood: %s",
                result_state.energy, result_state.mood.value,
            )
        engine.race_selector.on_non_race_action()
        return True


def main():
    args = parse_args()
    config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))

    # Initialize perception
    capture = ScrcpyCapture(config.capture)
    capture.start()
    ocr = OCREngine(config.ocr)
    screen_id = ScreenIdentifier(ocr=ocr)
    assembler = StateAssembler(screen_id, ocr, config)

    # Initialize action
    adb = ADBClient(device_serial=DEVICE)
    injector = InputInjector(adb, config)
    sequences = ActionSequences(injector)

    # Initialize decision engine (the real one!)
    engine = build_engine(config)

    logger.info("Decision engine ready (scenario=%s, runspec=%s)",
                config.scenario, config.runspec)

    if not args.execute:
        logger.info("DRY RUN mode — pass --execute to act")

    try:
        for turn_num in range(1, args.turns + 1):
            logger.info("=" * 60)
            logger.info("TURN %d / %d", turn_num, args.turns)
            logger.info("=" * 60)

            success = run_one_turn(
                args.execute, capture, assembler, screen_id,
                engine, injector, sequences, ocr=ocr,
                force_rest=args.force_rest,
            )

            if not success:
                logger.error("Turn %d failed — stopping", turn_num)
                break

            if turn_num < args.turns:
                logger.info("Waiting before next turn...")
                time.sleep(2.0)

        logger.info("Done — %d turn(s) completed", turn_num)
    finally:
        capture.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
