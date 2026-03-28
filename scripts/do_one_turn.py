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
CONSECUTIVE_RACES_FILE = Path("data/consecutive_races.txt")
JUST_RACED_FILE = Path("data/just_raced.txt")


def _load_consecutive_races() -> int:
    """Load persisted consecutive race count."""
    if CONSECUTIVE_RACES_FILE.exists():
        try:
            return int(CONSECUTIVE_RACES_FILE.read_text().strip())
        except (ValueError, OSError):
            return 0
    return 0


def _save_consecutive_races(count: int) -> None:
    """Persist consecutive race count between invocations."""
    CONSECUTIVE_RACES_FILE.write_text(str(count))
    logger.info("Consecutive races: %d (saved)", count)


def _load_just_raced() -> bool:
    """Load persisted just_raced flag for post-race shop visits."""
    if JUST_RACED_FILE.exists():
        try:
            return JUST_RACED_FILE.read_text().strip() == "1"
        except OSError:
            return False
    return False


def _save_just_raced(val: bool) -> None:
    """Persist just_raced flag between invocations."""
    JUST_RACED_FILE.write_text("1" if val else "0")


def _get_sp_reserve(engine) -> int:
    """Read skill_pts_reserve from strategy overrides, default 800."""
    if engine.scorer.overrides:
        raw = engine.scorer.overrides.get_strategy_raw()
        return raw.get("skill_pts_reserve", 800)
    return 800


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
    last_race_placement = None  # Track placement for post-race option choice
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

        # Shop popup (e.g. "The Shop's lineup has been refreshed!") — tap Cancel
        if state.screen == ScreenState.SKILL_SHOP:
            logger.info("Shop popup — tapping Cancel to dismiss")
            injector.tap(270, 1360)
            time.sleep(1.0)
            continue

        # Pre-race screen — tap View Results
        if state.screen == ScreenState.PRE_RACE:
            logger.info("Pre-race screen — tapping View Results")
            injector.tap(380, 1760)
            time.sleep(3.0)
            continue

        # Post-race screen — pick option based on placement, then Next
        # 1st place → option 2 (bottom/right), otherwise → option 1 (top/left)
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
            elif last_race_placement is not None:
                # We know the placement — choose the right option
                from uma_trainer.perception.regions import POST_RACE_REGIONS, get_tap_center
                if last_race_placement == 1:
                    opt = get_tap_center(POST_RACE_REGIONS["option_2"])
                    logger.info("Post-race 1st place — tapping option 2 at %s", opt)
                else:
                    opt = get_tap_center(POST_RACE_REGIONS["option_1"])
                    logger.info("Post-race %s place — tapping option 1 at %s", last_race_placement, opt)
                injector.tap(*opt)
                time.sleep(1.0)
                # Also tap Next to advance
                injector.tap(765, 1760)
                last_race_placement = None  # Reset after using it
            else:
                logger.info("Post-race screen — tapping Next")
                injector.tap(765, 1760)
            continue

        # Race list — tap Back to return to career home
        if state.screen == ScreenState.RACE_ENTRY:
            logger.info("Race list — tapping Back")
            injector.tap(75, 1870)
            continue

        # Result screen — detect placement, then tap to advance
        if state.screen == ScreenState.RESULT_SCREEN:
            # Try to read race placement from the result screen
            from uma_trainer.perception.regions import POST_RACE_REGIONS
            placement_region = POST_RACE_REGIONS["placement"]
            placement_text = assembler.ocr.read_region(frame, placement_region).lower()
            if "1st" in placement_text:
                last_race_placement = 1
                logger.info("Result screen — placement: 1st")
            elif any(p in placement_text for p in ("2nd", "3rd", "4th", "5th", "6th")):
                import re
                pm = re.search(r"(\d+)", placement_text)
                last_race_placement = int(pm.group(1)) if pm else 2
                logger.info("Result screen — placement: %s", last_race_placement)
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

        # OCR full screen for unknown screen detection
        unknown_text = assembler.ocr.read_region(frame, (0, 0, 1080, 960)).lower()
        unknown_text_lower = assembler.ocr.read_region(frame, (0, 960, 1080, 1920)).lower()

        # Full Stats / Umamusume Details screen — tap Close at bottom
        if "umamusume" in unknown_text or "details" in unknown_text:
            logger.info("Full Stats screen — tapping Close at (540, 1775)")
            injector.tap(540, 1775)
            time.sleep(1.5)
            continue

        # Trackblazer Inspiration GO! screen — big gold GO! button at center
        if "go" in unknown_text_lower and "skip" in unknown_text_lower:
            logger.info("Inspiration GO! screen — tapping GO! at (540, 1350)")
            injector.tap(540, 1350)
            time.sleep(3.0)
            continue

        # Inspiration result ("spark activated", "inspiration strikes") — tap to dismiss
        if "inspiration" in unknown_text_lower or "spark" in unknown_text_lower:
            logger.info("Inspiration result — tapping to dismiss")
            injector.tap(540, 960)
            time.sleep(2.0)
            continue

        # Check for Claw Machine minigame (Go Out special event) — pause for human
        if "claw" in unknown_text or "crane" in unknown_text:
            logger.warning("CLAW MACHINE MINIGAME detected — pausing for human input")
            print("\n*** CLAW MACHINE MINIGAME — please play manually, then press Enter ***")
            input()
            continue

        # Claw Machine results screen ("BIG WIN", "Cuties Obtained") — tap OK
        if "cuties" in unknown_text or "big win" in unknown_text:
            logger.info("Claw Machine results — tapping OK at (540, 1810)")
            injector.tap(540, 1810)
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


def _build_shop_name_matcher():
    """Build a fuzzy matcher for shop item names from ITEM_CATALOGUE."""
    from rapidfuzz import fuzz, process
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE, ItemTier

    # Map display name -> item_key for all purchasable items
    name_to_key: dict[str, str] = {}
    for key, item in ITEM_CATALOGUE.items():
        if item.tier == ItemTier.NEVER:
            continue
        name_to_key[item.name] = key
    return name_to_key


def _match_shop_item(ocr_text, name_to_key):
    """Fuzzy match OCR text against shop item names. Returns item_key or None."""
    from rapidfuzz import fuzz, process

    if not ocr_text or len(ocr_text) < 3:
        return None

    names = list(name_to_key.keys())
    result = process.extractOne(
        ocr_text, names,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=65,
    )
    if result is None:
        return None

    matched_name, score, _idx = result
    return name_to_key[matched_name]


def _scan_shop_items(frame, ocr, name_to_key):
    """Scan visible shop items. Returns list of (item_key, name_y, purchased)."""
    import re
    items = []
    y = 700
    while y < 1450:
        name_text = ocr.read_region(frame, (130, y, 700, y + 45)).strip()
        if not name_text or len(name_text) < 3:
            y += 30
            continue

        # Skip cost/effect/UI lines
        lower = name_text.lower()
        if any(lower.startswith(w) for w in ("cost", "effect", "choose", "x1", "xl")):
            y += 30
            continue

        item_key = _match_shop_item(name_text, name_to_key)
        if item_key is None:
            y += 30
            continue

        # Check for "Purchased" label on right side of row
        right_text = ocr.read_region(frame, (700, y + 20, 1050, y + 80)).strip().lower()
        is_purchased = "purchased" in right_text or "purch" in right_text

        logger.info("  Shop item: '%s' → %s%s (y=%d)",
                     name_text, item_key,
                     " PURCHASED" if is_purchased else "", y)
        items.append((item_key, y, is_purchased))
        y += 150  # skip past this row

    return items


def _get_shop_coins(frame, ocr):
    """Read coin balance from shop screen header."""
    import re
    coins_text = ocr.read_region(frame, (780, 590, 1060, 650)).strip()
    match = re.search(r"(\d+)", coins_text)
    return int(match.group(1)) if match else None


def execute_shop_visit(injector, capture, assembler, screen_id, sequences, engine):
    """Navigate to shop, buy priority items, and exit.

    Purchase priority (from user guidance):
    1. Empowering Megaphone (2-turn) — until 2 stockpiled for summer
    2. Motivating Megaphone (3-turn) — for good random training days
    3. Ankle Weights — for stats with support cards
    4. Vita drinks — energy recovery
    5. Good-Luck Charm — up to 2
    6. Mood items — a couple
    7. Miracle Cure — 1 to have
    8. Rich Hand Cream — for 3-race strings
    """
    from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE, ItemTier

    shop_btn = get_tap_center(TURN_ACTION_REGIONS["btn_shop"])
    logger.info("Visiting shop at %s", shop_btn)
    injector.tap(*shop_btn)
    time.sleep(2.5)

    # Verify we're in the shop
    frame = capture.grab_frame()
    shop_text = assembler.ocr.read_region(frame, (0, 0, 300, 80)).lower()
    if "shop" not in shop_text:
        logger.warning("May not be in shop (header: '%s') — tapping Back", shop_text[:40])
        injector.tap(50, 1870)
        time.sleep(2.0)
        return

    coins = _get_shop_coins(frame, assembler.ocr)
    logger.info("Shop coins: %s", coins)

    if coins is not None and coins < 15:
        logger.info("Not enough coins to buy anything — exiting")
        injector.tap(50, 1870)
        time.sleep(2.0)
        return

    # Build purchase want-list: items we'd like to buy, in priority order
    # Filter by tier and max_stock
    inventory = engine.shop_manager.inventory
    want_keys: list[str] = []

    # Priority order based on tier then manual ordering
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
        logger.info("Nothing to buy (all at max stock) — exiting")
        injector.tap(50, 1870)
        time.sleep(2.0)
        return

    logger.info("Want list: %s", want_keys[:10])

    # Scan shop and select items.
    # Strategy: on page 0 scan full screen, after each scroll only process
    # items in the lower half (y > 1000) to avoid re-tapping items that
    # were already visible and tapped on the previous scroll position.
    name_to_key = _build_shop_name_matcher()
    selected_counts: dict[str, int] = {}  # item_key -> count selected
    selected_keys: list[str] = []
    spent = 0
    max_scrolls = 4

    for scroll in range(max_scrolls + 1):
        if scroll > 0:
            injector.swipe(540, 1100, 540, 750, duration_ms=400)
            time.sleep(2.0)

        frame = capture.grab_frame()
        visible = _scan_shop_items(frame, assembler.ocr, name_to_key)

        # After scrolling, only consider items in the lower portion
        min_y = 700 if scroll == 0 else 1000

        for item_key, name_y, is_purchased in visible:
            if name_y < min_y:
                continue
            if is_purchased:
                continue
            if item_key not in want_keys:
                continue

            item = ITEM_CATALOGUE[item_key]

            # Check max_stock: owned + already selected this visit
            owned = inventory.get(item_key, 0)
            already_selected = selected_counts.get(item_key, 0)
            if owned + already_selected >= item.max_stock:
                continue

            if coins is not None and (spent + item.cost) > coins:
                logger.info("  Can't afford %s (%d coins, %d remaining)",
                            item.name, item.cost, coins - spent)
                continue

            # Tap the checkbox on the right side of the row
            checkbox_x = 950
            checkbox_y = name_y + 15
            logger.info("  Selecting %s at (%d, %d)", item.name, checkbox_x, checkbox_y)
            injector.tap(checkbox_x, checkbox_y)
            time.sleep(0.5)

            selected_keys.append(item_key)
            selected_counts[item_key] = already_selected + 1
            spent += item.cost

    if selected_keys:
        logger.info("Confirming purchase of %d items (total %d coins): %s",
                     len(selected_keys), spent, selected_keys)
        # Tap Confirm button (green, center bottom)
        injector.tap(540, 1640)
        time.sleep(2.0)

        # Handle Exchange confirmation dialog — tap Exchange (green button, right side)
        # The dialog shows "Confirm Exchange" header and Cancel/Exchange buttons
        frame = capture.grab_frame()
        confirm_text = assembler.ocr.read_region(frame, (0, 0, 1080, 200)).lower()
        if "confirm" in confirm_text or "exchange" in confirm_text or "purchase" in confirm_text:
            logger.info("Tapping Exchange to confirm purchase")
            # Exchange button is bottom-right of the dialog
            injector.tap(810, 1530)
            time.sleep(2.0)
        else:
            logger.warning("Exchange dialog not detected — OCR: '%s'", confirm_text[:60])
            # Try tapping Exchange anyway
            injector.tap(810, 1530)
            time.sleep(2.0)

        # Update inventory
        for key in selected_keys:
            engine.shop_manager.add_item(key)
        engine.shop_manager.save_inventory()
        logger.info("Inventory updated: %s", engine.shop_manager.inventory)
    else:
        logger.info("No items selected for purchase")

    # Exit shop
    injector.tap(50, 1870)
    time.sleep(2.0)


def _parse_skill_rows(frame, ocr, skill_matcher=None):
    """OCR visible skill rows and return list of parsed skill dicts.

    Each skill card is ~280px tall. Returns list of dicts with keys:
        name, matched_name, cost, obtained, hint_level, plus_y
    """
    import re
    skills = []
    y = 680
    while y < 1550:
        # Read skill name area (left side, above description)
        name_text = ocr.read_region(frame, (70, y, 800, y + 50)).strip()
        if not name_text or len(name_text) < 3:
            y += 30
            continue

        # Skip description lines (they start with common verbs/prepositions)
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

        # Fuzzy match against known skill names
        matched_name = name_text
        if skill_matcher:
            result = skill_matcher.match(name_text)
            if result:
                matched_name, score = result
                if matched_name != name_text:
                    logger.info("  Fuzzy: '%s' → '%s' (%d%%)", name_text, matched_name, score)
            else:
                # No match at all — likely garbage OCR or a description line
                logger.debug("  No match for '%s' — skipping", name_text)
                y += 30
                continue

        # Check for hint level badge ("Hint Lvl N" / "30% OFF!" near skill name)
        hint_level = 0
        # Badge can appear below name or to the right — scan both areas
        hint_text = ocr.read_region(frame, (70, y + 35, 500, y + 85)).strip().lower()
        hint_match = re.search(r"hint\s*(?:lv|lvl?)\.?\s*(\d)", hint_text)
        if hint_match:
            hint_level = int(hint_match.group(1))
        elif "hint" in hint_text or "off" in hint_text:
            hint_level = 1  # Hint present but level not parsed

        # Check if this row shows "Obtained" (already purchased)
        obtained_text = ocr.read_region(frame, (800, y + 50, 1050, y + 130)).strip().lower()
        is_obtained = "obtained" in obtained_text or "obt" in obtained_text

        # Read cost (number between -/+ buttons, right side)
        cost = None
        if not is_obtained:
            cost_text = ocr.read_region(frame, (820, y + 80, 940, y + 140)).strip()
            cost_match = re.search(r"(\d{2,4})", cost_text)
            if cost_match:
                cost = int(cost_match.group(1))

        plus_y = y + 110
        skill = {
            "name": name_text,
            "matched_name": matched_name,
            "cost": cost,
            "obtained": is_obtained,
            "hint_level": hint_level,
            "plus_y": plus_y,
        }
        skills.append(skill)
        hint_str = f" hint_lvl={hint_level}" if hint_level else ""
        logger.info(
            "  Skill: '%s' cost=%s%s%s",
            matched_name, cost, hint_str,
            " (obtained)" if is_obtained else "",
        )

        # Skip past this card (~280px, but use 240 to catch tightly packed rows)
        y += 240

    return skills


def execute_skill_buying(state, engine, injector, capture, assembler, screen_id, sp_reserve=800):
    """Open skill screen and buy affordable skills above the SP reserve.

    Buys skills from the strategy priority list first, then any affordable
    hint skills (30% OFF). Exits when SP drops below the reserve threshold.
    """
    from uma_trainer.knowledge.skill_matcher import SkillMatcher
    import re

    sp = state.skill_pts
    if sp <= sp_reserve:
        logger.info("Skill pts %d <= reserve %d — skipping skill buying", sp, sp_reserve)
        return

    spendable = sp - sp_reserve
    logger.info("Skill pts %d (reserve %d, spendable %d) — opening Skills", sp, sp_reserve, spendable)

    from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
    skills_btn = get_tap_center(TURN_ACTION_REGIONS["btn_skills"])
    injector.tap(*skills_btn)
    time.sleep(2.5)

    # Verify we're on the skill screen
    frame = capture.grab_frame()
    header_text = assembler.ocr.read_region(frame, (0, 0, 400, 100)).lower()
    if "skill" not in header_text and "learn" not in header_text:
        logger.warning("Not on skill screen (header: '%s') — aborting", header_text[:40])
        injector.tap(50, 1870)
        time.sleep(2.0)
        return

    # Read current SP from skill screen header
    sp_text = assembler.ocr.read_region(frame, (700, 580, 1050, 650)).strip()
    sp_match = re.search(r"(\d+)", sp_text)
    if sp_match:
        sp = int(sp_match.group(1))
        spendable = sp - sp_reserve
        logger.info("Skill screen SP: %d (spendable: %d)", sp, spendable)

    # Get priority list from strategy overrides
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

    # Initialize fuzzy matcher for skill names
    matcher = SkillMatcher()

    # Scan visible skills, scroll, and collect all available skills
    added_any = False
    spent = 0
    max_scrolls = 5

    for scroll in range(max_scrolls + 1):
        frame = capture.grab_frame()
        skills = _parse_skill_rows(frame, assembler.ocr, skill_matcher=matcher)

        for skill in skills:
            if skill["obtained"]:
                continue
            if skill["cost"] is None:
                continue

            name_lower = skill["matched_name"].lower()
            cost = skill["cost"]

            # Skip blacklisted
            if any(bl in name_lower for bl in blacklist_names):
                continue

            # Check if affordable within spendable budget
            if cost > (spendable - spent):
                logger.info("  '%s' costs %d but only %d spendable — skip", skill["matched_name"], cost, spendable - spent)
                continue

            # Buy if priority or if it's a hint skill (discounted)
            is_priority = any(p in name_lower for p in priority_names)
            is_hint_cheap = skill["hint_level"] > 0 and cost <= 120

            if is_priority or is_hint_cheap:
                logger.info(
                    "  BUYING '%s' for %d SP (priority=%s, hint=%d)",
                    skill["matched_name"], cost, is_priority, skill["hint_level"],
                )
                injector.tap(1010, skill["plus_y"])
                time.sleep(0.8)
                spent += cost
                added_any = True

                if (spendable - spent) <= 0:
                    logger.info("  Budget exhausted")
                    break

        if (spendable - spent) <= 0:
            break

        # Scroll down to see more skills
        if scroll < max_scrolls:
            injector.swipe(540, 1300, 540, 1020, duration_ms=400)
            time.sleep(2.0)

    if added_any:
        # Tap Confirm to purchase selected skills
        logger.info("Confirming skill purchases (spent %d SP)", spent)
        injector.tap(540, 1640)
        time.sleep(2.0)
    else:
        logger.info("No skills to buy — exiting")
        injector.tap(50, 1870)
        time.sleep(2.0)


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


def execute_race_entry(injector, capture, assembler, screen_id, race_selector=None, sequences=None):
    """Navigate into the race list, find the pre-selected race, and enter it.

    Called when DecisionEngine says to race. We're on career home.
    Uses the RaceSelector's pre-selected race from the calendar, then
    navigates the in-game list (scrolling if needed) to find it.
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

    # Handle popups that can appear before the race list
    for _ in range(3):
        if state.screen == ScreenState.WARNING_POPUP:
            from uma_trainer.perception.regions import WARNING_POPUP_REGIONS, get_tap_center
            ok_btn = get_tap_center(WARNING_POPUP_REGIONS["btn_ok"])
            logger.info("Warning popup before race list — tapping OK at %s", ok_btn)
            injector.tap(*ok_btn)
            time.sleep(2.0)
            frame = capture.grab_frame()
            state = assembler.assemble(frame)
        elif state.screen == ScreenState.SKILL_SHOP:
            logger.info("Shop popup before race list — tapping Cancel")
            injector.tap(100, 1150)
            time.sleep(2.0)
            frame = capture.grab_frame()
            state = assembler.assemble(frame)
        else:
            break

    if state.screen != ScreenState.RACE_ENTRY:
        logger.warning("Expected RACE_ENTRY, got %s — aborting race", state.screen.value)
        return False

    # Use pre-selected race from calendar, navigate to find it
    pre_selected = race_selector._pre_selected if race_selector else None

    if pre_selected and sequences:
        # Use the turn from the career home (before opening race list)
        # to estimate position, since pre-selection was done there.
        # The race list header may show a different turn count.
        from uma_trainer.decision.race_selector import GRADE_SORT_ORDER
        estimated_pos = GRADE_SORT_ORDER.get(pre_selected.grade, 0) * 3
        logger.info(
            "Looking for pre-selected race '%s' (grade=%s, est. position=%d)",
            pre_selected.name, pre_selected.grade, estimated_pos,
        )

        tap_coords = sequences.navigate_to_race(
            target_grade=pre_selected.grade,
            target_distance=pre_selected.distance,
            target_surface=pre_selected.surface,
            target_name=pre_selected.name,
            estimated_position=estimated_pos,
            capture=capture,
            ocr=assembler.ocr,
        )

        if tap_coords:
            logger.info("Found race — tapping at %s", tap_coords)
            injector.tap(*tap_coords)
            time.sleep(1.0)
        else:
            logger.warning(
                "Could not find '%s' in race list — falling back to first visible",
                pre_selected.name,
            )
    elif race_selector and state.available_races:
        # Legacy fallback: score visible races via OCR
        action = race_selector.decide(state)
        if action.action_type == ActionType.WAIT:
            logger.warning("RaceSelector: no suitable race — %s. Going back.", action.reason)
            injector.tap(75, 1870)
            time.sleep(1.5)
            return False

        if action.tap_coords and action.tap_coords != (0, 0):
            logger.info("Selecting race at %s (legacy)", action.tap_coords)
            injector.tap(*action.tap_coords)
            time.sleep(1.0)
    else:
        logger.info("No race selector — entering first visible race")

    # Tap the green Race button at bottom of list
    from uma_trainer.perception.regions import RACE_LIST_REGIONS
    race_btn = get_tap_center(RACE_LIST_REGIONS["btn_race"])
    logger.info("Tapping Race button on list at %s", race_btn)
    injector.tap(*race_btn)
    time.sleep(2.0)

    # Handle the race details confirmation popup
    logger.info("Tapping race confirmation at (810, 1370)")
    injector.tap(810, 1370)
    time.sleep(3.0)

    return True


def execute_rest(injector):
    """Tap Rest and confirm the dialog."""
    from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
    rest_btn = get_tap_center(TURN_ACTION_REGIONS["btn_rest"])
    logger.info("Tapping Rest at %s", rest_btn)
    injector.tap(*rest_btn)
    time.sleep(2.0)

    # Confirm rest dialog — OK button at (810, 1260)
    logger.info("Confirming rest")
    injector.tap(810, 1260)
    time.sleep(2.0)


def execute_go_out(injector):
    """Tap Recreation (Go Out) button."""
    from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
    go_out_btn = get_tap_center(TURN_ACTION_REGIONS["btn_recreation"])
    logger.info("Tapping Go Out (Recreation) at %s", go_out_btn)
    injector.tap(*go_out_btn)
    time.sleep(2.0)

    # Confirm "Go on a fun outing?" dialog — OK button at (810, 1260)
    logger.info("Confirming Go Out")
    injector.tap(810, 1260)
    time.sleep(2.0)


def check_conditions(injector, capture, assembler):
    """Open Full Stats, read conditions, close, return list of Conditions.

    Taps Full Stats button on career home, OCRs the conditions tab area,
    taps Close, and returns detected conditions.
    """
    from uma_trainer.types import Condition

    # Tap Full Stats button (right side of career home, below Training Items)
    logger.info("Checking conditions via Full Stats")
    injector.tap(990, 1160)
    time.sleep(1.5)

    frame = capture.grab_frame()

    # OCR the conditions area (y=970-1200, below "Conditions" tab header)
    condition_text = assembler.ocr.read_region(frame, (0, 950, 1080, 1250)).lower()
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

    # Tap Close button — retry up to 3 times if screen doesn't dismiss
    for close_attempt in range(3):
        injector.tap(540, 1775)
        time.sleep(1.5)

        # Verify we're back on career home by checking for "Career" header
        check_frame = capture.grab_frame()
        header_text = assembler.ocr.read_region(check_frame, (0, 0, 300, 80)).lower()
        if "career" in header_text:
            break
        logger.info("Full Stats still open (attempt %d) — retrying Close", close_attempt + 1)

    return conditions


def execute_infirmary(injector):
    """Tap Infirmary button, then confirm the dialog."""
    from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
    infirmary_btn = get_tap_center(TURN_ACTION_REGIONS["btn_infirmary"])
    logger.info("Tapping Infirmary at %s", infirmary_btn)
    injector.tap(*infirmary_btn)
    time.sleep(2.0)

    # Confirm "Visit the infirmary?" dialog — OK button at (810, 1260)
    logger.info("Confirming Infirmary visit")
    injector.tap(810, 1260)
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

    # Step 2.3: Check conditions via Full Stats screen.
    if execute:
        conditions = check_conditions(injector, capture, assembler)
        if conditions:
            state.active_conditions = conditions
            logger.info("Active conditions: %s", [c.value for c in conditions])

    # Step 2.5: Prepare item queue.
    # Items are split into two categories:
    #   - Reset Whistle: used ONLY if training tiles are lacking (< 40 total stats
    #     during summer camp). Must be used before boost items since it reshuffles cards.
    #   - Boost items (megaphone, ankle weights, charm): batched together and used
    #     AFTER we decide what to train, right before confirming.
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE
    all_items_used = []
    deferred_boost_items = []  # [(key, name)] to batch-use after training decision
    has_whistle = False

    item_queue = engine.shop_manager.get_item_queue(state)
    if item_queue:
        for a in item_queue:
            if a.target == "reset_whistle":
                has_whistle = True
            else:
                item_key = a.target
                item_name = ITEM_CATALOGUE[item_key].name if item_key in ITEM_CATALOGUE else item_key
                deferred_boost_items.append((item_key, item_name))
                if not execute:
                    print(f"  [DRY RUN] Would use item: {item_name} (deferred to batch)")
                    all_items_used.append(item_name)

    # Log skill points
    if state.skill_pts > 0:
        sp_reserve = _get_sp_reserve(engine)
        logger.info("Skill pts: %d (reserve: %d, spendable: %d)",
                     state.skill_pts, sp_reserve, max(0, state.skill_pts - sp_reserve))

    # Step 2.6: Visit shop if due (refresh cadence or post-race).
    if engine.shop_manager.should_visit_shop(state):
        if execute:
            logger.info("Shop visit due — visiting before main action")
            execute_shop_visit(injector, capture, assembler, screen_id, sequences, engine)
            _save_just_raced(False)
            # Re-read state after returning from shop
            frame = capture.grab_frame()
            state = assembler.assemble(frame)
        else:
            logger.info("[DRY RUN] Would visit shop this turn")

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

    # Pre-select the best race from the calendar so we know what to look for
    if race_action:
        pre = engine.race_selector.pre_select_race(state)
        if pre:
            logger.info("Pre-selected: %s (%s, %dm)", pre.name, pre.grade, pre.distance)

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
        pre = engine.race_selector._pre_selected
        print(f"\nDecision: RACE")
        print(f"Reason: {race_action.reason}")
        if pre:
            print(f"Target: {pre.name} ({pre.grade}, {pre.distance}m, {pre.surface})")

        if not execute:
            print("\n[DRY RUN] Would enter race list and find target race.")
            return True

        # Execute: tap Races, handle race list, race confirmation, post-race flow
        logger.info("STEP 3: Executing RACE")
        success = execute_race_entry(injector, capture, assembler, screen_id, race_selector=engine.race_selector, sequences=sequences)
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
        engine.race_selector.scenario.on_race_completed()
        _save_consecutive_races(engine.race_selector.scenario._consecutive_races)
        _save_just_raced(True)
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
        _save_consecutive_races(0)
        return True

    else:
        # Training flow:
        # 1. Scan tiles to see what's available
        # 2. During summer camp: if best tile < 40 total stats AND we have whistle → use it, re-scan
        # 3. Use boost items in batch (megaphone, charm, etc.)
        # 4. Select and confirm training
        SUMMER_MIN_STATS = 40

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

        # Check if whistle should be used (tiles are lacking during summer camp)
        is_summer = engine.scorer._is_summer_camp(state)
        if is_summer and has_whistle and execute and ocr is not None:
            best_total = max(
                (sum(t.stat_gains.values()) if t.stat_gains else 0)
                for t in state.training_tiles
            )
            if best_total < SUMMER_MIN_STATS:
                logger.info(
                    "Summer camp: best tile only %d total (need %d) — using Reset Whistle",
                    best_total, SUMMER_MIN_STATS,
                )
                # Go back to career home to use whistle
                injector.tap(95, 1875)
                time.sleep(2.0)

                whistle_name = ITEM_CATALOGUE["reset_whistle"].name
                success = sequences.execute_item_use("reset_whistle", whistle_name, capture, ocr)
                if success:
                    engine.shop_manager.consume_item("reset_whistle")
                    engine.shop_manager.activate_item("reset_whistle")
                    all_items_used.append(whistle_name)
                    has_whistle = False
                    logger.info("Whistle used — re-scanning tiles")

                # Navigate back to stat selection and re-scan
                injector.tap(*train_btn)
                time.sleep(2.0)
                frame = capture.grab_frame()
                state = assembler.assemble(frame)
                is_stat_select = screen_id.is_stat_selection(frame)
                if is_stat_select and state.training_tiles:
                    sequences.scan_training_gains(state, capture, assembler)
                else:
                    logger.error("Failed to re-enter stat selection after whistle")
                    return False

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
            # Use deferred boost items in batch before confirming training.
            # Go back to career home, batch use, return to stat selection.
            if deferred_boost_items and ocr is not None:
                logger.info("Using %d boost items in batch", len(deferred_boost_items))
                injector.tap(95, 1875)  # Back to career home
                time.sleep(2.0)

                used_keys = sequences.execute_item_batch(deferred_boost_items, capture, ocr)
                for key in used_keys:
                    engine.shop_manager.consume_item(key)
                    engine.shop_manager.activate_item(key)
                    name = ITEM_CATALOGUE[key].name if key in ITEM_CATALOGUE else key
                    all_items_used.append(name)

                if all_items_used:
                    print(f"Items used: {', '.join(all_items_used)}")

                # Navigate back to stat selection
                injector.tap(*train_btn)
                time.sleep(2.0)

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
        _save_consecutive_races(0)
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

    # Load trainee aptitudes from strategy overrides
    from uma_trainer.knowledge.overrides import OverridesLoader
    _overrides = OverridesLoader()
    _strategy_raw = _overrides.get_strategy_raw()
    apt = _strategy_raw.get("trainee_aptitudes", {})
    if apt:
        assembler.trainee_aptitudes = apt
        logger.info("Trainee aptitudes: %s", apt)

    # Initialize action
    adb = ADBClient(device_serial=DEVICE)
    injector = InputInjector(adb, config)
    sequences = ActionSequences(injector)

    # Initialize decision engine (the real one!)
    engine = build_engine(config)

    # Restore state from previous invocations
    if engine.race_selector.scenario:
        prev_count = _load_consecutive_races()
        engine.race_selector.scenario._consecutive_races = prev_count
        if prev_count > 0:
            logger.info("Restored consecutive race count: %d", prev_count)
        if _load_just_raced():
            engine.race_selector.scenario._just_raced = True
            logger.info("Restored just_raced flag — shop visit due")

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

            # Post-turn: check if we should buy skills (SP above reserve)
            if args.execute:
                frame = capture.grab_frame()
                post_state = assembler.assemble(frame)
                sp_reserve = _get_sp_reserve(engine)
                if post_state.skill_pts > sp_reserve:
                    execute_skill_buying(
                        post_state, engine, injector, capture,
                        assembler, screen_id, sp_reserve=sp_reserve,
                    )

            if turn_num < args.turns:
                logger.info("Waiting before next turn...")
                time.sleep(2.0)

        logger.info("Done — %d turn(s) completed", turn_num)
    finally:
        capture.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
