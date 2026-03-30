"""Automated turn executor. Runs one turn at a time with full logging.

Uses uma_trainer decision components for training scoring, skill buying,
and race selection. Screen detection and tap handling remain in this script.
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.career_helper import adb, screenshot, tap
from scripts.ocr_util import ocr_region, ocr_full_screen
from PIL import Image

# uma_trainer decision components
from uma_trainer.config import ScorerConfig
from uma_trainer.decision.scorer import TrainingScorer
from uma_trainer.decision.skill_buyer import SkillBuyer
from uma_trainer.decision.shop_manager import ShopManager
from uma_trainer.decision.race_selector import RaceSelector
from uma_trainer.decision.event_handler import EventHandler
from uma_trainer.knowledge.overrides import OverridesLoader
from uma_trainer.types import (
    ActionType,
    EventChoice,
    GameState,
    Mood,
    RaceOption,
    ScreenState,
    SkillOption,
    StatType,
    TraineeStats,
    TrainingTile,
)

LOG = Path("screenshots/run_log/run_current.md")
DEVICE = "127.0.0.1:5555"

# State tracking to avoid loops
_last_result = None
# Consecutive race counter — negative effects possible after 3
_consecutive_races = 0

# --- uma_trainer component initialization ---
_overrides = OverridesLoader("data/overrides")
_scorer_config = ScorerConfig()
_scorer = TrainingScorer(_scorer_config, overrides=_overrides)
_skill_buyer = SkillBuyer(kb=None, scorer=_scorer)
_shop_manager = ShopManager(overrides=_overrides)
_race_selector = RaceSelector(kb=None, overrides=_overrides)
_event_handler = EventHandler(kb=None, local_llm=None, claude_client=None, overrides=_overrides)

# Persistent state across turns (updated as we learn more)
_current_turn = 0
_current_stats = TraineeStats()
_skill_pts = 0
_cached_aptitudes = None  # Read once from Full Stats screen, then reused


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def px(img, x, y):
    return img.getpixel((x, y))[:3]


def _parse_aptitudes_from_image(img):
    """OCR the Full Stats screen image and extract aptitude grades.

    Returns dict like {'turf': 'A', 'dirt': 'E', 'short': 'D', 'mile': 'A', 'medium': 'A', 'long': 'S'}
    """
    import re
    from scripts.ocr_util import ocr_image
    from PIL import ImageOps

    valid_grades = {"S", "A", "B", "C", "D", "E", "F", "G"}
    label_map = {
        "turf": "turf", "dirt": "dirt",
        "sprint": "short", "mile": "mile", "medium": "medium", "long": "long",
    }
    aptitudes = {}
    w, h = img.size

    def _extract_from_results(results):
        """Extract aptitudes from OCR results list."""
        # Look for combined "Label Grade" strings (e.g. "Turf A", "Mile A")
        for text, conf, bbox in results:
            if conf < 0.3:
                continue
            t = text.strip()
            m = re.match(r'^(Turf|Dirt|Sprint|Mile|Medium|Long)\s+([A-GS])\)?$', t, re.IGNORECASE)
            if m:
                label = m.group(1).lower()
                grade = m.group(2).upper()
                if label in label_map and grade in valid_grades:
                    aptitudes[label_map[label]] = grade

        # For any missing, match isolated labels to nearby grade letters
        if len(aptitudes) < 6:
            labels_found = {}
            single_letters = []
            for text, conf, bbox in results:
                if conf < 0.2:
                    continue
                t = text.strip()
                tl = t.lower()
                if tl in label_map and label_map[tl] not in aptitudes:
                    cx = bbox[0] + bbox[2] / 2
                    cy = bbox[1] + bbox[3] / 2
                    labels_found[label_map[tl]] = (cx, cy)
                if len(t) == 1 and t.upper() in valid_grades:
                    cx = bbox[0] + bbox[2] / 2
                    cy = bbox[1] + bbox[3] / 2
                    single_letters.append((t.upper(), cx, cy))

            for key, (lx, ly) in labels_found.items():
                best = None
                best_dist = 999
                for grade, gx, gy in single_letters:
                    if gx > lx and abs(gy - ly) < 0.1 and (gx - lx) < 0.15:
                        # Combined distance so y-proximity breaks x-ties
                        dist = (gx - lx) + abs(gy - ly)
                        if dist < best_dist:
                            best = grade
                            best_dist = dist
                if best:
                    aptitudes[key] = best

    # Pass 1: Wide crop covering Track + Distance rows
    y1 = int(h * 0.20)
    y2 = int(h * 0.55)
    crop = img.crop((0, y1, w, y2))
    crop.save("/tmp/aptitude_crop.png")
    _extract_from_results(ocr_image("/tmp/aptitude_crop.png"))

    # Pass 2: Tighter Track row crop (y=600-680 at 1080x1920) for small grade icons
    if len(aptitudes) < 6:
        track_crop = img.crop((0, 600, w, 680))
        track_crop.save("/tmp/aptitude_track.png")
        _extract_from_results(ocr_image("/tmp/aptitude_track.png"))

    # Pass 3: Tighter Distance row crop (y=680-760) for any missing distance aptitudes
    if len(aptitudes) < 6:
        dist_crop = img.crop((0, 680, w, 760))
        dist_crop.save("/tmp/aptitude_dist.png")
        _extract_from_results(ocr_image("/tmp/aptitude_dist.png"))

    return aptitudes


def read_aptitudes_from_fullstats():
    """Navigate to Full Stats screen, OCR aptitudes, close, return to career_home.

    Returns dict of aptitudes or None on failure.
    """
    global _cached_aptitudes
    if _cached_aptitudes:
        return _cached_aptitudes

    log("Reading aptitudes from Full Stats screen...")
    tap(990, 1160, delay=2.0)

    img = screenshot(f"full_stats_{int(time.time())}")

    # Verify we're on the full stats screen
    texts = [t.strip().lower() for t, c, y in ocr_full_screen(img) if c > 0.3]
    all_text = " ".join(texts)
    if "track" not in all_text or "distance" not in all_text:
        log("WARNING: Full Stats screen not detected, falling back to strategy.yaml")
        tap(540, 1800, delay=1.0)
        return None

    aptitudes = _parse_aptitudes_from_image(img)
    log(f"Aptitudes from Full Stats: {aptitudes}")

    # Tap Close button (bottom center of Full Stats screen)
    tap(540, 1800, delay=1.5)

    if len(aptitudes) >= 4:
        _cached_aptitudes = aptitudes
        return aptitudes
    else:
        log(f"WARNING: Only read {len(aptitudes)} aptitudes, falling back to strategy.yaml")
        return None


def build_game_state(img, screen_type: str, energy: int = -1) -> GameState:
    """Build a GameState from auto_turn's screen data.

    This bridges auto_turn.py's raw OCR/pixel data into the uma_trainer
    type system so decision components can consume it.
    """
    global _current_turn, _current_stats, _skill_pts

    # Map auto_turn screen names to ScreenState
    screen_map = {
        "career_home": ScreenState.TRAINING,
        "career_home_summer": ScreenState.TRAINING,
        "ts_climax_home": ScreenState.TRAINING,
        "ts_climax_race": ScreenState.RACE_ENTRY,
        "training": ScreenState.TRAINING,
        "event": ScreenState.EVENT,
        "race_list": ScreenState.RACE_ENTRY,
        "skill_shop": ScreenState.SKILL_SHOP,
        "complete_career": ScreenState.RESULT_SCREEN,
    }

    # Read phase text + turns left from cropped top-left area
    # Phase text: "Junior Year Pre-Debut", "Junior Year", "Classic Year", "Senior Year"
    # Turns left: number of turns remaining in the CURRENT PHASE (not total career)
    try:
        from scripts.ocr_util import ocr_image
        turn_crop = img.crop((0, 50, 300, 260))
        turn_crop.save("/tmp/turn_crop.png")
        phase_text = ""
        turns_left = -1
        for text, conf, bbox in ocr_image("/tmp/turn_crop.png"):
            t = text.strip()
            tl = t.lower()
            if conf >= 0.5 and ("junior" in tl or "classic" in tl or "senior" in tl):
                phase_text = t
            if conf >= 0.3 and t.isdigit():
                val = int(t)
                if 1 <= val <= 30:
                    turns_left = val
        # Convert phase + turns_left to absolute turn number
        # Phase approximate offsets and lengths:
        #   Junior Pre-Debut: turns 1-12 (12 turns)
        #   Junior Year: turns 13-24 (12 turns)
        #   Classic Year: turns 25-48 (24 turns)
        #   Senior Year: turns 49-72 (24 turns)
        if phase_text and turns_left > 0:
            pl = phase_text.lower()
            if "pre-debut" in pl:
                _current_turn = max(1, 13 - turns_left)
            elif "junior" in pl:
                _current_turn = max(13, 25 - turns_left)
            elif "classic" in pl:
                _current_turn = max(25, 49 - turns_left)
            elif "senior" in pl:
                _current_turn = max(49, 73 - turns_left)
            log(f"Phase: '{phase_text}' | {turns_left} turns left | Turn {_current_turn}")
    except Exception:
        pass
    # Read current stat values from the stat bar (y=1240-1360)
    # Layout: Speed | Stamina | Power | Guts | Wit | Skill Pts
    # Each shows grade letter, value, "/1200"
    try:
        from scripts.ocr_util import ocr_image as _ocr_img
        stat_crop = img.crop((0, 1240, 1080, 1360))
        stat_crop.save("/tmp/stat_bar_crop.png")
        stat_cols = [
            (0.0, 0.20, "speed"),
            (0.20, 0.37, "stamina"),
            (0.37, 0.53, "power"),
            (0.53, 0.68, "guts"),
            (0.68, 0.83, "wit"),
            (0.83, 1.0, "skill_pts"),
        ]
        for text, conf, bbox in _ocr_img("/tmp/stat_bar_crop.png"):
            if conf < 0.3:
                continue
            t = text.strip().replace(":", "").replace("|", "")
            if not t.isdigit():
                continue
            val = int(t)
            # OCR sometimes merges stat with adjacent text (e.g. "1271" = "127" + "1")
            # Stats range 50-1200; truncate 4+ digit values to first 3 digits
            if val > 1200 and len(t) >= 4:
                val = int(t[:3])
            if val < 50 or val > 1200:
                continue
            cx = bbox[0] + bbox[2] / 2
            for x_min, x_max, stat_name in stat_cols:
                if x_min <= cx < x_max:
                    if stat_name == "skill_pts":
                        _skill_pts = val
                    else:
                        setattr(_current_stats, stat_name, val)
                    break
        log(f"Stats: Spd={_current_stats.speed} Sta={_current_stats.stamina} Pow={_current_stats.power} Gut={_current_stats.guts} Wit={_current_stats.wit} SP={_skill_pts}")
    except Exception:
        pass

    # Use cached aptitudes from Full Stats screen, fall back to strategy.yaml
    aptitudes = _cached_aptitudes
    if not aptitudes:
        strategy = _overrides.get_strategy()
        aptitudes = strategy.raw.get("trainee_aptitudes", {})

    state = GameState(
        screen=screen_map.get(screen_type, ScreenState.UNKNOWN),
        stats=_current_stats,
        energy=energy if energy >= 0 else 50,
        current_turn=_current_turn,
        skill_pts=_skill_pts,
        scenario="trackblazer",
        trainee_aptitudes=aptitudes,
    )

    # Summer camp flag
    if screen_type == "career_home_summer":
        state.current_turn = max(state.current_turn, 25)  # Ensure scorer knows it's summer

    return state


def is_green(r, g, b):
    return g > 150 and g > r and g > b and (g - r) > 30


def swipe(x1, y1, x2, y2, duration_ms=300):
    """Swipe from (x1,y1) to (x2,y2)."""
    subprocess.run(
        ["adb", "-s", DEVICE, "shell", "input", "swipe",
         str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        capture_output=True, timeout=10,
    )
    time.sleep(1.5)


def press_back():
    """Press hardware back button via ADB."""
    subprocess.run(
        ["adb", "-s", DEVICE, "shell", "input", "keyevent", "BACK"],
        capture_output=True, timeout=10,
    )
    time.sleep(2)


def find_green_button(img, y_range, x_range=(300, 950)):
    """Find center of a green button in the given ranges."""
    green_ys = []
    for y in range(y_range[0], y_range[1], 5):
        green_xs = []
        for x in range(x_range[0], x_range[1], 5):
            r, g, b = px(img, x, y)
            if is_green(r, g, b):
                green_xs.append(x)
        if len(green_xs) >= 3:
            cx = (min(green_xs) + max(green_xs)) // 2
            green_ys.append((y, cx))
    if not green_ys:
        return None
    mid = len(green_ys) // 2
    return (green_ys[mid][1], green_ys[mid][0])


def detect_screen(img):
    """Detect current screen type using OCR text markers."""
    try:
        results = ocr_full_screen(img)
    except Exception as e:
        log(f"OCR failed in detect_screen: {e}")
        return "unknown"

    # Collect all text into a set for fast lookup
    all_texts = set()
    all_texts_lower = set()
    for text, conf, y_pos in results:
        if conf > 0.3:
            all_texts.add(text)
            all_texts_lower.add(text.lower())

    def has(*keywords):
        """Check if any keyword appears in any OCR text."""
        for kw in keywords:
            kw_l = kw.lower()
            for t in all_texts_lower:
                if kw_l in t:
                    return True
        return False

    # Tutorial / scenario info slides
    # Check for bottom navigation buttons (y > 1700)
    has_back_bottom = False
    has_next_bottom = False
    has_close_bottom = False
    has_help_bottom = False
    for text, conf, y_pos in results:
        if conf < 0.3:
            continue
        t = text.strip()
        if y_pos > 1700:
            if t == "Back":
                has_back_bottom = True
            if t == "Next":
                has_next_bottom = True
            if t == "Close":
                has_close_bottom = True
            if t == "Help":
                has_help_bottom = True
    # Back + Next = tutorial slide (page through)
    if has_back_bottom and has_next_bottom:
        return "tutorial_slide"
    # Back + Close + Help = scenario info overlay (dismiss)
    if has_back_bottom and has_close_bottom:
        return "tutorial_slide"

    # Victory concert — "Photo" at bottom, no game UI
    has_photo_bottom = False
    for text, conf, y_pos in results:
        if conf >= 0.5 and text.strip() == "Photo" and y_pos > 1800:
            has_photo_bottom = True
    if has_photo_bottom and not has("Training") and not has("Energy") and not has("Back"):
        return "concert"

    # Goal Complete / Goals progress screen — has Next button, tap through
    # "GOAL COMPLETE" screen has both words as large standalone text, not in dialogue
    if any("goal" in t and "complete" in t for t in all_texts_lower):
        return "goal_complete"
    if has("GOAL COMPLETE"):
        return "goal_complete"
    if has("Goals") and has("Result Pts") and has("Next"):
        return "goal_complete"

    # Skill purchase confirmation dialog: "Learn the above skills?"
    if has("Confirmation") and has("Learn the above skills"):
        return "skill_confirm_dialog"

    # Skills Learned popup: "Your trainee learned new skills!"
    if has("Skills Learned") and has("Close"):
        return "skills_learned"

    # Popup screens (checked first — they overlay other screens)
    if has("Cancel") and has("OK"):
        if has("Rest") and has("recover energy"):
            return "rest_confirm"
        if has("enter this race"):
            return "race_confirm"
        return "warning_popup"

    # Race confirm popup: has Cancel + Race + "Enter race?"
    if has("Cancel") and has("Race") and has("Enter race"):
        return "race_confirm"

    # Insufficient Result Pts warning — has Cancel + Race buttons
    if has("Insufficient") and has("Result Pts") and has("Race"):
        return "insufficient_pts"
    if has("Cancel") and has("Race") and has("Pts"):
        return "insufficient_pts"

    # Pre-race screen: has "View Results" and "Race" buttons, plus strategy info
    if has("View Results") and has("Strategy"):
        return "pre_race"

    # Post-race standings: has "Try Again" and "Next"
    if has("Try Again") and has("Next"):
        return "post_race_standings"

    # Post-race placement screen (pyramid with Next but no Try Again yet)
    if has("Placing") and has("Next") and has("Fans"):
        return "post_race_placement"

    # TS Climax standings (RANK + Next + "Twinkle Star" or "Climax")
    if has("RANK") and has("Next") and (has("Twinkle") or has("Climax")) and has("Victory Pts"):
        return "ts_climax_standings"

    # Post-race result (animation done, shows WIN/placement, no nav buttons)
    # Check for exact "WIN" token, not substring (avoid "Showing" etc.)
    has_win = any(t.strip().upper() == "WIN" for t in all_texts)
    if has_win and not has("Race List") and not has("Back") and not has("Effects"):
        return "post_race_result"

    # Fan class / post-race reward screen
    if has("Watch Concert"):
        return "fan_class"

    # Event screen — check BEFORE career_home since events overlay the Career screen
    if has("Effects"):
        return "event"
    if has("Trainee Event") or has("Main Scenario Event"):
        return "event"
    if has("Support Card Event") or has("Random Event"):
        return "event"

    # Race list: header says "Race List"
    if has("Race List"):
        return "race_list"

    # Complete Career finish dialog: "Finish this Career playthrough?"
    if has("Finish") and has("Cancel") and has("Remaining Skill Points"):
        return "complete_career_finish"

    # Career Complete: "To Home" + "Edit Team" after all results
    if has("Career Complete") and has("To Home"):
        return "career_complete_done"

    # Career Rank / Sparks / Rewards / Epithet post-career screens with Next
    if has("CAREER") and has("RANK") and has("Next"):
        return "post_career_next"
    if has("SPARKS") and has("Next"):
        return "post_career_next"
    if has("REWARDS") and has("Next"):
        return "post_career_next"
    if has("Epithet") and has("Confirm"):
        return "post_career_confirm"

    # Umamusume Details (post-career summary) with Close
    if has("Umamusume Details") and has("Close"):
        return "post_career_details"

    # Complete Career screen — end of run, can buy skills or finish
    if has("Complete Career") and has("Skills") and has("Skill Pts"):
        return "complete_career"

    # Skill shop (Learn screen) — has skill list with Confirm button
    if has("Learn") and has("Confirm") and has("Skill Points"):
        return "skill_shop"

    # Shop screen
    if has("Shop Coins") or (has("Shop") and has("Cost")):
        return "shop"

    # Training screen: has "Failure" indicator and stat tile labels
    if has("Failure") and has("Back"):
        return "training"

    # Career home: has the action buttons
    if has("Training") and has("Races") and has("Rest"):
        # Check if it's summer camp (Jul/Aug in Classic or Senior year only)
        # Junior Year does NOT have summer camp
        if (has("Jul") or has("Aug")) and (has("Classic") or has("Senior")):
            return "career_home_summer"
        # TS Climax mode — training turn vs race turn
        if has("TS CLIMAX") or has("Climax"):
            return "ts_climax_home"
        return "career_home"

    # TS Climax Race Day — no Training/Rest buttons, just Race! + Skills + Shop
    if has("Race Day") and has("Race!") and has("TS CLIMAX"):
        return "ts_climax_race"

    # Trophy won popup
    if has("TROPHY") and has("Close"):
        return "trophy_won"

    # Race lineup screen (Race! button to start race animation)
    if has("Race!") and has("Fav") and not has("Race List"):
        return "race_lineup"

    # Unlock Requirements popup (tapped locked View Results)
    if has("Unlock Requirements") and has("Close"):
        return "unlock_popup"

    # Shop refresh popup: has "refreshed" and Cancel/Shop buttons
    if has("refreshed") and has("Cancel"):
        return "shop_popup"

    # Inspiration screen: has "GO!" button
    if has("GO!"):
        return "inspiration"

    # Cutscene / animation result: has Skip/Quick but no main nav
    if has("Skip") and has("Quick") and not has("Rest") and not has("Races"):
        return "cutscene"

    # Dark overlay / TAP prompt — check pixel brightness as fallback
    total_brightness = 0
    for x in range(300, 800, 20):
        r, g, b = px(img, x, 960)
        total_brightness += r + g + b
    if total_brightness < 5000:
        return "tap_prompt"

    # Result Pts popup — white popup over dark background
    if has("Result Pts") and has("Close"):
        return "result_pts_popup"

    return "unknown"


def get_energy_level(img):
    """Estimate energy percentage from the energy bar fill.

    The bar is a gradient (blue→cyan→green) when filled, gray (117,117,117) when empty.
    Detect filled vs empty by checking if the pixel differs from the gray background.
    """
    BAR_Y = 236
    BAR_X_START = 340
    BAR_X_END = 750
    filled_count = 0
    total = 0
    for x in range(BAR_X_START, BAR_X_END, 5):
        r, g, b = px(img, x, BAR_Y)
        total += 1
        # Gray empty bar is ~(117,117,117). Filled bar has color (blue/cyan/green).
        # Also skip white pixels at the bar edges.
        is_gray = abs(r - g) < 15 and abs(g - b) < 15 and 100 < r < 140
        is_white = r > 240 and g > 240 and b > 240
        if not is_gray and not is_white:
            filled_count += 1
    return int(100 * filled_count / max(total, 1))


def has_green_aptitude_badge(img, card_y_start, card_y_end):
    """Check if a race card has green aptitude badges (B+ aptitude).

    Green aptitude badges are bright green rectangles: G>200, B<130, R>100.
    Scans the right portion of the card where surface/distance badges appear.
    """
    green_count = 0
    for y in range(card_y_start, card_y_end, 4):
        for x in range(700, 1060, 4):
            r, g, b = px(img, x, y)
            if g > 200 and b < 130 and r > 100:
                green_count += 1
    return green_count >= 8


def _ocr_race_list(img):
    """OCR the race list screen and return list of RaceOption objects."""
    results = ocr_full_screen(img)
    sorted_results = sorted(results, key=lambda r: r[2])

    # Race cards are stacked vertically. Each card has:
    # - Race name (large text)
    # - Grade badge (G1/G2/G3/OP)
    # - Distance/surface text
    # - Green aptitude badges if B+ aptitude
    # Cards are at roughly y=800-1000 (card 1), y=1100-1300 (card 2), etc.
    races = []
    CARD_REGIONS = [
        {"y_range": (800, 1050), "tap_y": 950},
        {"y_range": (1050, 1300), "tap_y": 1200},
        {"y_range": (1300, 1550), "tap_y": 1450},
    ]

    for i, region in enumerate(CARD_REGIONS):
        y_min, y_max = region["y_range"]
        card_texts = []
        for text, conf, y_pos in sorted_results:
            if conf < 0.3:
                continue
            if y_min <= y_pos <= y_max:
                card_texts.append((text.strip(), y_pos))

        if not card_texts:
            continue

        # Find race name (longest text, not a grade/number)
        name = ""
        grade = ""
        for text, y_pos in card_texts:
            tl = text.lower()
            if text in ("G1", "G2", "G3", "OP", "Pre-OP"):
                grade = text
            elif len(text) > len(name) and not text.isdigit() and len(text) > 3:
                name = text

        if not name:
            continue

        # Parse distance from card text (e.g. "1400m", "1600m")
        import re
        distance = 0
        surface = "turf"
        for text, y_pos in card_texts:
            m = re.search(r'(\d{4})m', text)
            if m:
                distance = int(m.group(1))
            tl = text.lower()
            if "dirt" in tl:
                surface = "dirt"
            elif "turf" in tl:
                surface = "turf"

        # Check aptitude from green badges
        apt_ok = has_green_aptitude_badge(img, y_min + 100, y_max - 30)

        race = RaceOption(
            name=name,
            grade=grade,
            distance=distance,
            surface=surface,
            is_aptitude_ok=apt_ok,
            position=i,
            tap_coords=(540, region["tap_y"]),
        )
        races.append(race)
        log(f"  Race {i+1}: '{name}' grade={grade} dist={distance}m {surface} apt_ok={apt_ok}")

    return races


def handle_race_list(img):
    """Handle race list screen using RaceSelector."""
    races = _ocr_race_list(img)
    if not races:
        log("No races detected on list — pressing Back")
        press_back()
        return "race_back"

    state = build_game_state(img, "race_list")
    state.available_races = races

    action = _race_selector.decide(state)
    log(f"RaceSelector: {action.reason}")

    if action.action_type == ActionType.RACE and action.tap_coords != (0, 0):
        log(f"Selecting race at {action.tap_coords}")
        tap(action.tap_coords[0], action.tap_coords[1], delay=1.5)
        # Tap the green "Race" button to confirm entry
        img2 = screenshot(f"race_confirm_{int(time.time())}")
        race_btn = find_green_button(img2, (1550, 1650))
        if race_btn:
            log(f"Confirming race at {race_btn}")
            tap(race_btn[0], race_btn[1])
        else:
            log("Race button not found — tapping expected location")
            tap(540, 1590)
        return "race_enter"

    # No good race — go back
    log("No worthwhile race — pressing Back")
    press_back()
    return "race_back"


def _ocr_event_name(img):
    """OCR the event title from the banner area."""
    try:
        texts = ocr_region(img, 0, 280, 1080, 420,
                           save_path="/tmp/event_banner.png")
        for text, conf in texts:
            if conf > 0.4 and text not in (
                "Main Scenario Event", "Trackblazer",
                "Support Card Event", "Random Event",
                "T GREAT", "Energy",
            ):
                return text
    except Exception as e:
        log(f"OCR error: {e}")
    return "unknown"


def _find_effects_button(img):
    """Find the Effects button on event screen.

    It's a small white label in the lower-right area of the event description,
    typically around x=750-820, y=1450-1510.
    Returns (x, y) center if found, else None.
    """
    # Look for small cluster of white pixels on the right side of the description
    for y in range(1420, 1540, 5):
        white_xs = []
        for x in range(700, 900, 3):
            r, g, b = px(img, x, y)
            if r > 230 and g > 230 and b > 230:
                white_xs.append(x)
        if 3 <= len(white_xs) <= 25:
            cx = sum(white_xs) // len(white_xs)
            return (cx, y)
    return None



def _is_victory_event(img):
    """Check if this is a post-race victory event (1st place).

    Victory events have text like "Did I do well?", "Solid Showing",
    "You sure did!", "You can do even better!" etc.
    """
    try:
        from scripts.ocr_util import ocr_full_screen
        all_text = ocr_full_screen(img)
        text_lower = " ".join(t.lower() for t, c, _ in all_text if c > 0.3)
        victory_phrases = [
            "did i do well", "solid showing", "you sure did",
            "you can do even better", "gave it your all",
            "well, you gave it", "let's make them",
        ]
        return any(phrase in text_lower for phrase in victory_phrases)
    except Exception:
        return False


def handle_event(img):
    """Handle event screen using EventHandler with overrides-based decisions."""
    # OCR full event screen text
    event_name = _ocr_event_name(img)
    log(f"Event: '{event_name}'")

    # Tutorial event — find the right button to dismiss
    if event_name.lower() == "tutorial":
        try:
            all_text = ocr_full_screen(img)
            for t, c, y in sorted(all_text, key=lambda r: r[2]):
                tl = t.strip().lower()
                if "all i need to know" in tl:
                    log(f"Tutorial — tapping 'That's all I need to know' at y={y:.0f}")
                    tap(540, int(y))
                    return "tutorial_dismiss"
                if tl == "yes." or tl == "yes":
                    log(f"Tutorial — tapping 'Yes' at y={y:.0f}")
                    tap(540, int(y))
                    return "tutorial_dismiss"
        except Exception:
            pass
        log("Tutorial — tapping Skip")
        tap(90, 1853)
        return "tutorial_dismiss"

    # Build full event text from OCR for override matching
    try:
        all_text = ocr_full_screen(img)
        full_text = " ".join(t for t, c, _ in all_text if c > 0.3)
    except Exception:
        full_text = event_name

    # Build event choices (typically 2 choices on screen)
    # Choice 1 at y~1120, Choice 2 at y~1250, Choice 3 at y~1380
    choices = [
        EventChoice(index=0, text="choice 1", tap_coords=(540, 1120)),
        EventChoice(index=1, text="choice 2", tap_coords=(540, 1250)),
    ]

    # Check if there's a 3rd choice visible (some events have 3)
    for t, c, y in all_text if 'all_text' in dir() else []:
        pass  # choices are position-based, no need to detect count

    # Build GameState for EventHandler
    energy = get_energy_level(img)
    state = build_game_state(img, "event", energy=energy)
    state.event_text = full_text
    state.event_choices = choices

    # Victory events (post-race 1st place): always pick choice 2
    # This is checked before the handler since it's a hard rule
    if _is_victory_event(img):
        log("Victory event detected — picking choice 2 (always best for wins)")
        tap(540, 1250)
        return "event"

    # Use EventHandler (Tier 0 overrides → fallback to choice 1)
    action = _event_handler.decide(state)
    log(f"EventHandler: {action.reason} → choice {action.target}")

    if action.tap_coords != (0, 0):
        tap(action.tap_coords[0], action.tap_coords[1])
    else:
        tap(540, 1120)
    return "event"


def _ocr_skill_list(img):
    """OCR the skill shop screen and return list of buyable SkillOption objects.

    Parses the skill list by looking for skill names (above descriptions)
    with costs (standalone 2-3 digit numbers). Skips "Obtained" skills.
    """
    results = ocr_full_screen(img)
    sorted_results = sorted(results, key=lambda r: r[2])  # Sort by y

    # First pass: find skill names, costs, "Obtained" markers, and description text
    # Skills appear as: [Name] at some y, [cost number] nearby, [description lines] below
    # "Obtained" appears right next to already-bought skills
    skills = []
    obtained_ys = set()
    cost_entries = []  # (y, cost)
    hint_ys = set()
    desc_lines = []  # (y, text_lower) — collect ALL description text for unique skill detection
    skip_words = {
        "confirm", "reset", "back", "close", "learn", "skill points",
        "full", "stats", "obtained", "10% off!", "hint lvl 1", "hint lvi 1",
    }
    # Unique/inherited skill markers in description text
    unique_markers = ["proportion", "career wins", "number of career"]

    for text, conf, y_pos in sorted_results:
        if conf < 0.3:
            continue
        t = text.strip()
        tl = t.lower()

        # Collect all description-like text for unique skill detection
        if len(t) > 15 or t[0:1].islower():
            desc_lines.append((int(y_pos), tl))

        if t == "Obtained":
            obtained_ys.add(int(y_pos))
            continue

        if "Hint" in t:
            hint_ys.add(int(y_pos))
            continue

        # Cost: standalone number 50-500
        if t.isdigit() and 50 <= int(t) <= 500:
            cost_entries.append((int(y_pos), int(t)))
            continue

        # Skip known non-skill-name text
        if tl in skip_words or len(t) <= 2:
            continue
        # Skip description lines (start lowercase, or very long)
        if len(t) > 50:
            continue
        if t[0].islower():
            continue
        # Skip stat numbers
        if t.isdigit():
            continue

    # Second pass: identify skill names by looking for capitalized text
    # that's NOT near an "Obtained" marker
    for text, conf, y_pos in sorted_results:
        if conf < 0.3:
            continue
        t = text.strip()
        tl = t.lower()
        y = int(y_pos)

        if tl in skip_words or len(t) <= 3 or t.isdigit():
            continue
        if t[0].islower() or "OFF" in t or "Hint" in t:
            continue
        # Skip description lines — only match multi-word description phrases
        # to avoid falsely catching skill names like "Corner Adept"
        desc_phrases = [
            "increase velocity", "increase acceleration", "increase performance",
            "decrease performance", "increase passing", "narrow the field",
            "improve running", "control breathing", "kick forward",
            "begin to advance", "positioned around", "slightly increase",
            "slightly decrease", "slightly improve", "slightly narrow",
            "moderately increase", "moderately decrease", "moderately narrow",
            "very slightly", "on the heels", "on a corner",
        ]
        if any(p in tl for p in desc_phrases):
            continue
        # Skip long lines (descriptions tend to be > 30 chars)
        if len(t) > 30:
            continue
        if y < 600:  # Header area
            continue

        # Check if this is near an "Obtained" marker (within 80px)
        is_obtained = any(abs(y - oy) < 80 for oy in obtained_ys)
        if is_obtained:
            continue

        # Check if this is a unique/inherited skill (description has scaling markers)
        is_unique = False
        for dy, dtl in desc_lines:
            if 0 < dy - y < 150:  # Description is below the name
                if any(m in dtl for m in unique_markers):
                    is_unique = True
                    break
        if is_unique:
            continue

        # Find the cost closest to this skill (within 100px below)
        cost = 0
        is_hint = False
        for cy, cv in cost_entries:
            if 0 < cy - y < 120:
                cost = cv
                break
        for hy in hint_ys:
            if abs(hy - y) < 40:
                is_hint = True
                break

        if cost > 0:
            skill = SkillOption(
                name=t,
                cost=cost,
                is_hint_skill=is_hint,
                hint_level=1 if is_hint else 0,
                tap_coords=(960, y + 70),  # + button is to the right, slightly below name
                priority=7 if is_hint else 5,
            )
            skills.append(skill)

    return skills


def _read_skill_pts(img):
    """Read current skill points from skill shop header."""
    try:
        results = ocr_full_screen(img)
        for text, conf, y_pos in results:
            if conf < 0.3:
                continue
            t = text.strip()
            # "Skill Points" label is at y~616, the number is near it
            if t.isdigit() and 500 < y_pos < 700:
                val = int(t)
                if val >= 50:
                    return val
    except Exception:
        pass
    return _skill_pts


# Track scroll state to avoid infinite loops in skill shop
_skill_shop_scrolled_up = False
_skill_shop_scroll_downs = 0
_skill_shop_done = False  # Set True when we've exhausted buying


def handle_skill_shop(img):
    """Buy skills from the skill shop screen.

    Strategy: buy all hint-discounted skills first, then highest-priority
    skills until we run low on pts. At Complete Career, spend everything.
    """
    global _skill_shop_scrolled_up, _skill_shop_scroll_downs

    skills = _ocr_skill_list(img)
    sp = _read_skill_pts(img)

    if not skills:
        if not _skill_shop_scrolled_up:
            # Scroll to top first — may have missed skills above
            log("Skill shop — no skills visible, scrolling to top")
            for _ in range(5):
                swipe(540, 700, 540, 1400, 300)
            _skill_shop_scrolled_up = True
            _skill_shop_scroll_downs = 0
            return "skill_shop"
        elif _skill_shop_scroll_downs < 8:
            # Scroll down to find more
            log(f"Skill shop — scrolling down ({_skill_shop_scroll_downs + 1}/8)")
            swipe(540, 1400, 540, 700, 400)
            _skill_shop_scroll_downs += 1
            return "skill_shop"
        else:
            # Exhausted all scrolling — exit
            global _skill_shop_done
            log(f"Skill shop — no more skills to buy ({sp} SP remaining), exiting")
            _skill_shop_scrolled_up = False
            _skill_shop_scroll_downs = 0
            _skill_shop_done = True
            tap(50, 1747)  # On-screen Back button
            return "skill_back"

    log(f"Skill shop — {len(skills)} skills visible, {sp} SP available")
    bought_any = False

    for skill in skills:
        if skill.cost <= 0:
            continue
        if skill.cost > sp:
            log(f"  Skip {skill.name} — cost {skill.cost} > {sp} SP remaining")
            continue
        log(f"  Buying: {skill.name} (cost={skill.cost}, hint={skill.is_hint_skill})")
        tap(skill.tap_coords[0], skill.tap_coords[1])
        time.sleep(0.5)
        sp -= skill.cost
        bought_any = True

    if bought_any:
        # Tap Confirm button (green button at y~1600) to finalize purchases
        log(f"Confirming skill purchases (~{sp} SP remaining)")
        time.sleep(0.5)
        fresh_img = screenshot(f"skill_confirm_{int(time.time())}")
        confirm_btn = find_green_button(fresh_img, (1570, 1640), (100, 500))
        if confirm_btn:
            log(f"  Found Confirm at {confirm_btn}")
            tap(confirm_btn[0], confirm_btn[1])
        else:
            log("  Confirm button not found — tapping default coords")
            tap(270, 1600)
        # The confirmation dialog + "Skills Learned" popup will be
        # handled by the main loop on subsequent iterations
        # Reset scroll state so we re-scan from top after buying
        _skill_shop_scrolled_up = False
        _skill_shop_scroll_downs = 0
    else:
        # All visible skills too expensive — scroll down for cheaper ones
        if _skill_shop_scroll_downs < 8:
            log(f"No affordable skills — scrolling down ({_skill_shop_scroll_downs + 1}/8)")
            swipe(540, 1400, 540, 700, 400)
            _skill_shop_scroll_downs += 1
        else:
            log(f"No affordable skills and done scrolling — exiting ({sp} SP remaining)")
            _skill_shop_scrolled_up = False
            _skill_shop_scroll_downs = 0
            _skill_shop_done = True
            tap(50, 1747)  # On-screen Back button
            return "skill_back"

    return "skill_shop"


# Training tile tap positions (x, y) for each stat
TRAINING_TILES = {
    "Speed":   (158, 1520),
    "Stamina": (350, 1580),
    "Power":   (541, 1580),
    "Guts":    (731, 1580),
    "Wit":     (921, 1580),
}



def _ocr_training_gains(img):
    """OCR the stat gain preview numbers from a training screen.

    Returns dict of stat_name -> gain_value for each visible "+N" indicator.
    The gains are large stylized numbers overlaid on the stat panels at the bottom.

    Layout (2 rows x 3 columns):
      Top:    Speed (x<360)  | Stamina (360-720) | Power (x>720)
      Bottom: Guts  (x<360)  | Wit     (360-720) | Skill Pts (x>720)
    """
    from scripts.ocr_util import ocr_image
    from PIL import ImageOps
    # Crop the stat area — gains appear as +N text above stat labels
    # Layout (6 columns): Speed | Stamina | Power | Guts | Wit | Skill Pts
    # Gains (+N) at y~1200, stat labels at y~1244, values at y~1284
    crop = img.crop((0, 1180, 1080, 1280))
    # Invert colors — dramatically improves "+" recognition (1.00 vs 0.50)
    inverted = ImageOps.invert(crop.convert("RGB"))
    inverted.save("/tmp/stat_gains_crop.png")
    raw = ocr_image("/tmp/stat_gains_crop.png")

    # 6-column mapping by x position (normalized 0-1)
    stat_cols = [
        (0.0, 0.20, "Speed"),
        (0.20, 0.37, "Stamina"),
        (0.37, 0.53, "Power"),
        (0.53, 0.68, "Guts"),
        (0.68, 0.83, "Wit"),
        (0.83, 1.0, "Skill Pts"),
    ]

    gains = {}
    for text, conf, bbox in raw:
        if conf < 0.2:
            continue
        t = text.strip()
        # Gains appear in the upper half of the crop (cy_top < 0.60)
        # Stat labels are at cy_top ~0.80+, so 0.60 cleanly separates
        center_y = 1.0 - (bbox[1] + bbox[3] / 2)
        if center_y > 0.60:
            continue
        # Strip "+" prefix and common OCR artifacts
        clean = t.replace("+", "").replace("$", "").replace(",", "").strip()
        # Low-confidence "4N" is likely "+N" (inversion doesn't always fix it)
        if conf < 0.6 and clean.isdigit() and len(clean) >= 2 and clean[0] == "4":
            clean = clean[1:]
        try:
            val = int(clean)
        except ValueError:
            continue
        if val > 80 or val < 1:
            continue
        center_x = bbox[0] + bbox[2] / 2
        for x_min, x_max, stat in stat_cols:
            if x_min <= center_x < x_max:
                gains[stat] = val
                break
    return gains



def handle_training():
    """Preview all 5 training tiles and pick the best using uma_trainer scorer."""
    global _consecutive_races
    log("Training — previewing all tiles")

    # Check which tile is pre-raised by reading gains before tapping
    # A pre-raised tile will show gains; tapping it again would CONFIRM training
    img_initial = screenshot(f"train_initial_{int(time.time())}")
    if detect_screen(img_initial) != "training":
        return detect_screen(img_initial)
    pre_gains = _ocr_training_gains(img_initial)
    # Identify which tile is pre-raised by checking which stat column has gains
    pre_raised_tile = None
    if pre_gains:
        # The training name banner shows which tile is selected (y~290-340)
        banner_text = ocr_region(img_initial, 0, 280, 540, 350, save_path="/tmp/train_banner.png")
        for t, c in banner_text:
            tl = t.strip().lower()
            for tn in TRAINING_TILES:
                if tn.lower() in tl:
                    pre_raised_tile = tn
                    break
            if pre_raised_tile:
                break
        if pre_raised_tile:
            log(f"  Pre-raised tile: {pre_raised_tile} (gains already visible)")
        else:
            log(f"  Gains visible but can't identify pre-raised tile")

    # Build TrainingTile objects from OCR data
    tiles = []
    for tile_name, (tx, ty) in TRAINING_TILES.items():
        if tile_name == pre_raised_tile:
            # Already raised — use the initial screenshot, don't tap
            img = img_initial
            gains = pre_gains
        else:
            tap(tx, ty, delay=1)
            img = screenshot(f"train_preview_{tile_name.lower()}_{int(time.time())}")

            # Check if an event fired during preview (events overlay training)
            screen_check = detect_screen(img)
            if screen_check != "training":
                log(f"  {tile_name}: interrupted by {screen_check} — aborting preview")
                return screen_check

            gains = _ocr_training_gains(img)

        stat_type = StatType(tile_name.lower())
        tile = TrainingTile(
            stat_type=stat_type,
            tap_coords=(tx, ty),
            stat_gains={k.lower(): v for k, v in gains.items()},
        )
        tiles.append(tile)

        gains_str = ", ".join(f"{k}+{v}" for k, v in sorted(gains.items()))
        log(f"  {tile_name}: total={tile.total_stat_gain} ({gains_str})")

    # Build GameState and let the scorer decide
    energy = get_energy_level(img)
    state = build_game_state(img, "training", energy=energy)
    state.training_tiles = tiles

    action = _scorer.best_action(state)
    log(f"Scorer decision: {action.action_type.value} — {action.reason}")

    if action.action_type == ActionType.REST:
        log("Scorer says rest — tapping Rest")
        _consecutive_races = 0
        tap(185, 1525)
        return "rest"

    # Find the tile the scorer chose and tap it
    _consecutive_races = 0
    if action.tap_coords != (0, 0):
        tx, ty = action.tap_coords
    else:
        # Fallback: pick highest total gain
        best = max(tiles, key=lambda t: t.total_stat_gain)
        tx, ty = best.tap_coords
    tap(tx, ty, delay=1)
    tap(tx, ty)
    return "training"


def run_one_turn():
    """Execute one game action. Returns screen type for logging."""
    global _last_result, _consecutive_races

    img = screenshot(f"auto_{int(time.time())}")
    screen = detect_screen(img)
    log(f"Detected: {screen}")

    if screen == "career_home_summer":
        # SUMMER CAMP: train as much as possible, never race
        # But rest if energy is critically low to avoid high failure rates
        energy = get_energy_level(img)
        if energy < 10:
            log(f"SUMMER CAMP — Energy: ~{energy}% — resting to avoid failure")
            _consecutive_races = 0
            tap(185, 1525)
            time.sleep(2)
            img2 = screenshot(f"rest_check_{int(time.time())}")
            s2 = detect_screen(img2)
            if "confirm" in s2 or "warning" in s2:
                ok = find_green_button(img2, (1150, 1350))
                if ok:
                    tap(ok[0], ok[1])
            return "rest"
        log(f"SUMMER CAMP — Energy: ~{energy}% — going to Training (NEVER race)")
        tap(540, 1480)
        return "going_to_training"

    if screen == "ts_climax_race":
        # TS Climax Race Day — must race, use cleats if available
        log("TS CLIMAX Race Day — tapping Race!")
        # TODO: use Master Cleat Hammer before racing if in inventory
        tap(540, 1590)
        return "ts_climax_racing"

    if screen == "ts_climax_home":
        # TS Climax with Training/Rest buttons visible = training turn
        # Race turns force you into race selection directly
        energy = get_energy_level(img)
        log(f"TS CLIMAX training turn — Energy: ~{energy}% — going to Training")
        tap(540, 1496)
        return "going_to_training"

    if screen == "career_home":
        energy = get_energy_level(img)
        build_game_state(img, screen, energy=energy)  # updates _current_turn, _current_stats
        log(f"Energy: ~{energy}% | Turn: {_current_turn} | Consecutive races: {_consecutive_races}")

        # Read aptitudes from Full Stats once per run
        if not _cached_aptitudes:
            read_aptitudes_from_fullstats()
            # Re-screenshot since we navigated away and back
            time.sleep(1)
            img = screenshot(f"career_home_post_stats_{int(time.time())}")
            if detect_screen(img) != "career_home":
                return "recovering"
            energy = get_energy_level(img)

        if energy < 10:
            log("Critically low energy — resting")
            _consecutive_races = 0
            tap(185, 1525)
            time.sleep(2)
            img2 = screenshot(f"rest_check_{int(time.time())}")
            s2 = detect_screen(img2)
            if "confirm" in s2 or "warning" in s2:
                ok = find_green_button(img2, (1150, 1350))
                if ok:
                    log(f"Confirming rest at {ok}")
                    tap(ok[0], ok[1])
            return "rest"

        # After 3 consecutive races, train instead to avoid negative effects
        if _consecutive_races >= 3:
            log(f"3+ consecutive races ({_consecutive_races}) — training to avoid penalty")
            _consecutive_races = 0
            tap(540, 1480)
            return "going_to_training"

        # If we just came back from race_list with no good races, train instead
        if _last_result == "race_back":
            log("No good races available — going to Training instead")
            _consecutive_races = 0
            tap(540, 1480)
            return "going_to_training"

        # Use race strategy from overrides to decide race vs train
        strategy = _overrides.get_strategy()
        race_config = strategy.raw.get("race_strategy", {})
        skip_early = race_config.get("skip_early_turns", 5)
        race_interval = race_config.get("race_interval", 2)

        # Low energy: race instead of train (races don't cost energy)
        # Only train at low energy if it's very early game
        if energy < 50 and _current_turn >= skip_early:
            log(f"Energy {energy}% too low to train safely — going to Races instead")
            tap(910, 1680)
            return "going_to_races"

        if _current_turn < skip_early:
            log(f"Turn {_current_turn} < {skip_early} — training (skip early turns)")
            tap(540, 1480)
            return "going_to_training"

        # Alternate: race every N turns based on race_interval
        if _current_turn % race_interval == 0:
            log(f"Race turn (interval={race_interval}) — tapping Races")
            tap(910, 1680)
            return "going_to_races"
        else:
            log(f"Training turn (interval={race_interval}) — tapping Training")
            tap(540, 1480)
            return "going_to_training"

    elif screen == "tutorial_slide":
        # Check for Next vs Close button
        next_btn = find_green_button(img, (1780, 1900))
        if next_btn:
            log("Tutorial slide — tapping Next")
            tap(next_btn[0], next_btn[1])
        else:
            log("Tutorial/info slide — tapping Close")
            tap(180, 1853)
        return "tutorial_slide"

    elif screen == "goal_complete":
        log("Goal complete / goals screen — tapping Next")
        next_btn = find_green_button(img, (1600, 1800))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(540, 1680)
        return "goal_complete"

    elif screen == "insufficient_pts":
        log("Insufficient Result Pts — tapping Race to earn points")
        race_btn = find_green_button(img, (1150, 1350))
        if race_btn:
            tap(race_btn[0], race_btn[1])
        else:
            tap(760, 1250)
        return "insufficient_pts_race"

    elif screen == "warning_popup":
        # If we just came from race flow, this is a race energy warning
        if _last_result in ("going_to_races", "race_enter"):
            _consecutive_races += 1
            log(f"Race warning popup — tapping OK (consecutive: {_consecutive_races})")
        else:
            log("Warning popup — tapping OK")
        ok = find_green_button(img, (1150, 1350))
        if ok:
            tap(ok[0], ok[1])
        else:
            tap(760, 1250)
        return "warning_ok"

    elif screen == "race_list":
        return handle_race_list(img)

    elif screen == "race_confirm":
        _consecutive_races += 1
        log(f"Race confirm — tapping Race button (consecutive: {_consecutive_races})")
        race_btn = find_green_button(img, (1250, 1450))
        if race_btn:
            tap(race_btn[0], race_btn[1])
        else:
            tap(730, 1360)
        return "race_confirm"

    elif screen == "rest_confirm":
        log("Rest confirm — tapping OK")
        ok = find_green_button(img, (1150, 1350))
        if ok:
            tap(ok[0], ok[1])
        else:
            tap(730, 1250)
        return "rest_confirm"

    elif screen == "pre_race":
        # Check if View Results is locked (gray button = RGB ~150,145,153)
        # vs unlocked (white/bright button). Sample the button center.
        vr_r, vr_g, vr_b = px(img, 300, 1790)
        view_results_locked = (vr_r < 170 and vr_g < 170 and vr_b < 170)
        if view_results_locked:
            log("Pre-race — View Results LOCKED, tapping Race (must watch)")
            race_btn = find_green_button(img, (1750, 1850), (500, 900))
            if race_btn:
                tap(race_btn[0], race_btn[1])
            else:
                tap(690, 1790)
        else:
            log("Pre-race — tapping View Results (skip animation)")
            tap(300, 1790)
        return "pre_race"

    elif screen == "tap_prompt":
        log("TAP prompt — tapping center")
        tap(540, 960)
        return "tap_prompt"

    elif screen == "result_pts_popup":
        log("Result Pts popup — tapping background to dismiss")
        tap(540, 400)
        return "result_pts"

    elif screen == "post_race_standings":
        # Check placement via OCR — look for "Nth" text
        import re
        placement = 99
        is_climax = False
        try:
            standings_results = ocr_full_screen(img)
            for text, conf, y_pos in standings_results:
                t = text.strip().lower()
                if "climax" in t:
                    is_climax = True
                m = re.match(r"(\d+)(st|nd|rd|th)", t)
                if m and placement == 99:
                    placement = int(m.group(1))
        except Exception:
            pass
        # TS Climax: retry if placed worse than 3rd
        if is_climax and placement > 3:
            log(f"TS Climax race — placed {placement}th, tapping Try Again")
            tap(270, 1780)
            return "retry_race"
        log(f"Standings — placed {placement}, tapping Next")
        next_btn = find_green_button(img, (1700, 1850), (500, 1000))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(750, 1780)
        return "standings_next"

    elif screen == "ts_climax_standings":
        log("TS Climax standings — tapping Next")
        next_btn = find_green_button(img, (1600, 1750), (300, 800))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(540, 1680)
        return "ts_standings_next"

    elif screen == "post_race_placement":
        log("Post-race placement — tapping Next")
        next_btn = find_green_button(img, (1700, 1850), (300, 800))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(540, 1789)
        return "placement_next"

    elif screen == "fan_class":
        log("Fan class — tapping Next")
        next_btn = find_green_button(img, (1750, 1870), (600, 1000))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(810, 1810)
        return "fan_next"

    elif screen == "unlock_popup":
        log("Unlock Requirements popup — tapping Close")
        tap(540, 1400)
        return "unlock_close"

    elif screen == "trophy_won":
        log("Trophy won! — tapping Close")
        tap(540, 1400)
        return "trophy_close"

    elif screen == "race_lineup":
        log("Race lineup — tapping Race! to start")
        race_btn = find_green_button(img, (1690, 1790))
        if race_btn:
            tap(race_btn[0], race_btn[1])
        else:
            tap(540, 1735)
        return "race_start"

    elif screen == "post_race_result":
        log("Post-race result — tapping to continue")
        tap(540, 960)
        return "post_race_result"

    elif screen == "inspiration":
        log("Inspiration screen — tapping GO!")
        tap(540, 1530)
        return "inspiration"

    elif screen == "concert":
        log("Victory concert — opening menu and skipping")
        tap(1040, 1880, delay=1.5)
        tap(1000, 1585, delay=2.0)
        # Confirmation dialog may appear — tap OK
        img3 = screenshot(f"concert_skip_{int(time.time())}")
        screen3 = detect_screen(img3)
        if screen3 == "warning_popup" or any("skip" in t.lower() for t, c, y in ocr_full_screen(img3) if c > 0.3):
            ok_btn = find_green_button(img3, (1150, 1350))
            if ok_btn:
                tap(ok_btn[0], ok_btn[1])
            else:
                tap(730, 1250)
        return "concert_skip"

    elif screen == "cutscene":
        log("Cutscene — tapping Skip")
        tap(135, 1853)
        return "cutscene_skip"

    elif screen == "shop_popup":
        log("Shop refresh popup — tapping Cancel to dismiss")
        tap(120, 1231)
        return "shop_popup_dismiss"

    elif screen == "shop":
        log("Shop screen — pressing Back to return")
        press_back()
        return "shop_back"

    elif screen == "event":
        return handle_event(img)

    elif screen == "training":
        result = handle_training()
        # If training was interrupted by an event, process it now
        if result in ("event", "goal_complete", "tap_prompt", "cutscene"):
            log(f"Training interrupted by {result} — handling it")
            img2 = screenshot(f"train_interrupt_{int(time.time())}")
            screen2 = detect_screen(img2)
            if screen2 == "event":
                return handle_event(img2)
            elif screen2 == "goal_complete":
                next_btn = find_green_button(img2, (1600, 1800))
                if next_btn:
                    tap(next_btn[0], next_btn[1])
                else:
                    tap(540, 1680)
                return "goal_complete"
            elif screen2 == "tap_prompt":
                tap(540, 960)
                return "tap_prompt"
            elif screen2 == "cutscene":
                tap(135, 1853)
                return "cutscene_skip"
        return result

    elif screen == "complete_career":
        if _skill_shop_done:
            log("Complete Career — skills done, tapping Complete Career")
            tap(810, 1565)  # Complete Career button (right side)
            return "career_complete_final"
        log("Complete Career — opening skill shop to spend remaining pts")
        tap(270, 1565)  # Skills button
        return "complete_career"

    elif screen == "complete_career_finish":
        log("Complete Career finish dialog — tapping Finish")
        finish_btn = find_green_button(img, (1320, 1400), (550, 1000))
        if finish_btn:
            tap(finish_btn[0], finish_btn[1])
        else:
            tap(777, 1356)
        return "career_finishing"

    elif screen == "post_career_next":
        log("Post-career screen — tapping Next")
        tap(540, 1800)
        return "post_career_next"

    elif screen == "post_career_confirm":
        log("Epithet screen — tapping Confirm")
        tap(540, 1750)
        return "post_career_confirm"

    elif screen == "post_career_details":
        log("Umamusume Details — tapping Close")
        tap(540, 1750)
        return "post_career_close"

    elif screen == "career_complete_done":
        log("Career Complete — tapping To Home")
        tap(270, 1250)
        return "career_done"

    elif screen == "skill_confirm_dialog":
        log("Skill purchase confirmation — tapping Learn")
        tap(810, 1752)
        return "skill_confirm"

    elif screen == "skills_learned":
        log("Skills Learned popup — tapping Close")
        tap(540, 1230)
        return "skills_learned_close"

    elif screen == "skill_shop":
        return handle_skill_shop(img)

    else:
        # Try to find a green button (Next, OK, etc.) before blindly tapping
        green_btn = find_green_button(img, (1600, 1900))
        if green_btn:
            log(f"Unknown screen — found green button at {green_btn}, tapping")
            tap(green_btn[0], green_btn[1])
        else:
            log(f"Unknown screen — tapping center to advance")
            tap(540, 960)
        return "unknown"


def main():
    log("\n" + "=" * 50)
    log("Auto-turn session starting")
    log("=" * 50)

    num_turns = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    log(f"Running {num_turns} actions")

    global _last_result
    for i in range(num_turns):
        log(f"\n--- Action {i+1}/{num_turns} ---")
        result = run_one_turn()
        _last_result = result
        log(f"Result: {result}")
        time.sleep(2.5)

    log("\nSession complete")


if __name__ == "__main__":
    main()
