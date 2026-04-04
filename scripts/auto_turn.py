"""Automated turn executor. Runs one turn at a time with full logging.

Uses uma_trainer decision components for training scoring, skill buying,
and race selection. Screen detection and tap handling remain in this script.
"""
import re
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
# Last race placement (1=win, 99=unknown)
_last_race_placement = 99

# --- uma_trainer component initialization ---
_overrides = OverridesLoader("data/overrides")
_scorer_config = ScorerConfig()
_scorer = TrainingScorer(_scorer_config, overrides=_overrides)

# Knowledge base (auto-creates SQLite DB, accumulates event/skill data over runs)
from uma_trainer.knowledge.database import KnowledgeBase
_kb = KnowledgeBase("data/uma_trainer.db")

_skill_buyer = SkillBuyer(kb=_kb, scorer=_scorer)
_shop_manager = ShopManager(overrides=_overrides)

# Load scenario and runspec so the scorer knows about summer camp, stat targets, etc.
from uma_trainer.scenario import load_scenario
from uma_trainer.decision.runspec import load_runspec
from uma_trainer.decision.lookahead import should_conserve_energy
_scenario = load_scenario("trackblazer")
_runspec = load_runspec("end_guts_v1")
_scorer.scenario = _scenario
_scorer.runspec = _runspec
# Inventory is read from Training Items screen on first career_home — no yaml loading
_race_selector = RaceSelector(kb=_kb, overrides=_overrides, scenario=_scenario)
_event_handler = EventHandler(kb=_kb, local_llm=None, claude_client=None, overrides=_overrides)

from uma_trainer.perception.card_tracker import CardTracker
_card_tracker = CardTracker()

# Persistent state across turns (updated as we learn more)
_current_turn = 0
_current_stats = TraineeStats()
_skill_pts = 0
_cached_aptitudes = None  # Read once from Full Stats screen, then reused
_active_conditions = []   # Negative conditions detected this session
_game_state = None        # Last built GameState, reused across screens
_summer_whistle_used = False  # Reset each turn; prevents double-whistling
_ts_climax_retries = 0        # Retry counter for TS Climax races (max 3)
_prev_stats = None                # Previous turn's stats for suspicious jump detection

# Map negative conditions to their cure items in the shop catalogue
CONDITION_CURES = {
    "night owl": "fluffy_pillow",
    "migraine": "aroma_diffuser",
    "skin outbreak": "rich_hand_cream",
    "slacker": "pocket_planner",
    "practice poor": "practice_dvd",
    "overweight": "smart_scale",
}

# Career home button coordinates (1080x1920 portrait, never move)
BTN_REST = (185, 1525)
BTN_TRAINING = (540, 1550)
BTN_INFIRMARY = (162, 1750)
# These two move depending on screen layout — only valid on career_home
BTN_HOME_SKILLS = (918, 1535)
BTN_HOME_RACES = (920, 1750)
BTN_SHOP = (620, 1640)
BTN_TRAINING_ITEMS = (827, 1130)
BTN_TRAINING_ITEMS_RACE = (827, 1260)  # Training Items on race screens (ts_climax_race, required_race)
BTN_ITEMS_CONFIRM = (779, 1772)  # "Confirm Use" / "Use Training Items" right button
BTN_ITEMS_CLOSE = (303, 1772)    # "Close" left button


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def px(img, x, y):
    return img.getpixel((x, y))[:3]




def detect_mood(img):
    """Detect mood from the mood icon text via OCR.

    Returns one of: GREAT, GOOD, NORMAL, BAD, AWFUL, UNKNOWN
    """
    mood_text = ocr_region(img, 800, 160, 1080, 260, save_path="/tmp/mood_crop.png")
    if isinstance(mood_text, list):
        mood_text = " ".join(t for t, c in mood_text)
    mood_upper = mood_text.strip().upper()
    # Check for substrings — OCR may pick up arrow icon as extra chars
    for mood in ("GREAT", "AWFUL", "GOOD", "NORMAL", "BAD"):
        if mood in mood_upper:
            return mood
    # Fallback: partial matches (OCR may clip first letters)
    if "WFUL" in mood_upper:
        return "AWFUL"
    if "REAT" in mood_upper:
        return "GREAT"
    return "UNKNOWN"


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


def read_fullstats():
    """Navigate to Full Stats screen, OCR aptitudes + conditions, close.

    Runs every turn to keep state in sync with the game.
    Returns dict of aptitudes or None on failure.
    """
    global _cached_aptitudes, _active_conditions

    log("Reading Full Stats...")
    tap(990, 1160, delay=2.0)

    img = screenshot(f"full_stats_{int(time.time())}")

    # Verify we're on the full stats screen
    texts = [t.strip().lower() for t, c, y in ocr_full_screen(img) if c > 0.3]
    all_text = " ".join(texts)
    if "track" not in all_text or "distance" not in all_text:
        log("WARNING: Full Stats screen not detected, falling back to strategy.yaml")
        tap(540, 1800, delay=1.0)
        return None

    # Read aptitudes
    aptitudes = _parse_aptitudes_from_image(img)
    if len(aptitudes) >= 4:
        _cached_aptitudes = aptitudes

    # Read conditions (y=950-1250 area on Full Stats)
    conditions = []
    for condition_name in CONDITION_CURES:
        if condition_name in all_text:
            conditions.append(condition_name)
    _active_conditions = conditions

    if aptitudes:
        log(f"Aptitudes: {aptitudes}")
    if conditions:
        log(f"Active conditions: {conditions}")
    else:
        log("No negative conditions")

    # Tap Close button (bottom center of Full Stats screen)
    tap(540, 1800, delay=1.5)

    return aptitudes


def cure_conditions_from_inventory():
    """If we have cure items for active conditions, use them from Training Items."""
    if not _active_conditions:
        return

    # Check which conditions we can cure with inventory
    inventory = _shop_manager.inventory
    cure_keys = []
    for condition in _active_conditions:
        cure_key = CONDITION_CURES.get(condition)
        if cure_key and inventory.get(cure_key, 0) > 0:
            cure_keys.append(cure_key)
            log(f"Can cure '{condition}' with {cure_key}")

    # Also check miracle_cure for any condition
    if _active_conditions and not cure_keys and inventory.get("miracle_cure", 0) > 0:
        cure_keys.append("miracle_cure")
        log(f"Using miracle_cure for: {_active_conditions}")

    if not cure_keys:
        return

    _use_training_items(cure_keys)
    # Remove used items from inventory and clear cured conditions
    for key in cure_keys:
        if _shop_manager._inventory.get(key, 0) > 0:
            _shop_manager._inventory[key] -= 1
            if _shop_manager._inventory[key] <= 0:
                del _shop_manager._inventory[key]
    # Mark conditions as cured so Phase 3 doesn't re-act on them
    cured = {c for c in _active_conditions
             if CONDITION_CURES.get(c) in cure_keys or "miracle_cure" in cure_keys}
    for c in cured:
        _active_conditions.remove(c)
    _shop_manager.save_inventory()


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

    # Read period text from top-left (e.g. "Classic Year Early Apr")
    # The "X turn(s) left" is a GOAL DEADLINE, not a turn counter.
    # Derive absolute turn from year + month + half.
    MONTH_OFFSETS = {
        "jan": 0, "feb": 2, "mar": 4, "apr": 6, "may": 8, "jun": 10,
        "jul": 12, "aug": 14, "sep": 16, "oct": 18, "nov": 20, "dec": 22,
    }
    YEAR_OFFSETS = {"junior": 0, "classic": 24, "senior": 48}
    try:
        period_text = ocr_region(img, 20, 68, 380, 110, save_path="/tmp/period_crop.png")
        if isinstance(period_text, list):
            period_text = " ".join(t for t, c in period_text if c > 0.3)
        pt = period_text.strip().lower()
        # Split into words to avoid substring false positives (e.g. "jun" in "junior")
        words = pt.split()
        year_offset = next((v for k, v in YEAR_OFFSETS.items() if k in words), None)
        # Handle special periods (Pre-Debut, Debut) that don't have month names
        if "pre-debut" in pt or "pre debut" in pt:
            if year_offset is not None:
                _current_turn = year_offset + 1  # Turns 1-11
                log(f"Period: '{period_text.strip()}' → Turn {_current_turn} (Pre-Debut)")
        elif "debut" in pt and "pre" not in pt:
            if year_offset is not None:
                _current_turn = year_offset + 12  # Turn 12
                log(f"Period: '{period_text.strip()}' → Turn {_current_turn} (Debut)")
        else:
            month_offset = next((v for k, v in MONTH_OFFSETS.items() if k in words), None)
            if year_offset is not None and month_offset is not None:
                half = 1 if "late" in pt else 0
                _current_turn = year_offset + month_offset + half + 1
                log(f"Period: '{period_text.strip()}' → Turn {_current_turn}")
            else:
                log(f"Period OCR: '{period_text.strip()}' — could not parse turn")
    except Exception as e:
        log(f"Period OCR failed: {e}")
    # Read current stat values from the stat bar (y=1240-1360)
    # Layout: Speed | Stamina | Power | Guts | Wit | Skill Pts
    # Blend out diamond dividers between columns to prevent "1" misreads
    try:
        from scripts.ocr_util import ocr_image as _ocr_img
        stat_crop = img.crop((0, 1240, 1080, 1360))
        import numpy as np
        arr = np.array(stat_crop)
        # Blend out diamond separators between columns (horizontal avg, 5px wide)
        for dx in (208, 378, 546, 715):
            left = arr[:, max(0, dx - 4):max(0, dx - 3), :].mean(axis=1, keepdims=True)
            right = arr[:, min(arr.shape[1]-1, dx + 3):min(arr.shape[1], dx + 4), :].mean(axis=1, keepdims=True)
            blend = ((left + right) / 2).astype(np.uint8)
            arr[:, dx - 2:dx + 3, :] = blend
        # White-out grade badges at the start of each stat column (value + denom rows)
        # Badge is ~40px wide; fill with white to prevent OCR merging with numbers
        grade_badge_regions = [
            (38, 90),    # speed: badge at x=38-88
            (222, 264),  # stamina: badge at x=222-262
            (397, 443),  # power: badge at x=397-442
            (572, 600),  # guts: badge at x=572-598
            (734, 768),  # wit: badge at x=734-766
        ]
        for gx_start, gx_end in grade_badge_regions:
            arr[45:, gx_start:gx_end, :] = 255  # White-out below the header row
        from PIL import Image as _Img
        stat_crop = _Img.fromarray(arr)
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
            t = text.strip().replace(":", "").replace("|", "").replace("-", "").replace("—", "").replace("\\", "")
            if t.startswith("/"):
                continue
            digits = re.findall(r'\d+', t)
            if not digits:
                continue
            t = max(digits, key=len)
            val = int(t)
            cx = bbox[0] + bbox[2] / 2
            matched_stat = None
            for x_min, x_max, stat_name in stat_cols:
                if x_min <= cx < x_max:
                    matched_stat = stat_name
                    break
            if matched_stat is None:
                continue
            if matched_stat == "skill_pts":
                if val > 9999:
                    continue
                _skill_pts = val
            else:
                if val >= 1200:
                    continue  # Skip "/1200" denominator labels
                if val < 50:
                    continue
                setattr(_current_stats, stat_name, val)
        log(f"Stats: Spd={_current_stats.speed} Sta={_current_stats.stamina} Pow={_current_stats.power} Gut={_current_stats.guts} Wit={_current_stats.wit} SP={_skill_pts}")
        # Detect suspicious stat jumps and save upscaled screenshot for debugging
        global _prev_stats
        if _prev_stats is not None:
            JUMP_THRESHOLD = 80
            for sname in ("speed", "stamina", "power", "guts", "wit"):
                prev_val = getattr(_prev_stats, sname)
                curr_val = getattr(_current_stats, sname)
                if prev_val > 0 and curr_val > 0 and abs(curr_val - prev_val) > JUMP_THRESHOLD:
                    log(f"⚠ Suspicious OCR: {sname} jumped {prev_val}→{curr_val} (Δ{curr_val - prev_val})")
                    try:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        ocr_debug_dir = Path("screenshots/ocr_debug")
                        ocr_debug_dir.mkdir(parents=True, exist_ok=True)
                        upscaled = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
                        debug_path = ocr_debug_dir / f"suspicious_{sname}_{prev_val}to{curr_val}_t{_current_turn}_{ts}.png"
                        upscaled.save(debug_path)
                        stat_crop.save(ocr_debug_dir / f"suspicious_{sname}_{prev_val}to{curr_val}_t{_current_turn}_{ts}_statbar.png")
                        log(f"  Saved debug screenshot: {debug_path}")
                    except Exception as e:
                        log(f"  Failed to save debug screenshot: {e}")
                    break  # One save per turn is enough
        _prev_stats = TraineeStats(
            speed=_current_stats.speed, stamina=_current_stats.stamina,
            power=_current_stats.power, guts=_current_stats.guts, wit=_current_stats.wit
        )
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


def swipe(x1, y1, x2, y2, duration_ms=300, settle=3.0):
    """Raw swipe from (x1,y1) to (x2,y2), then wait for momentum to settle."""
    import random
    jx = random.randint(-10, 10)
    jy = random.randint(-10, 10)
    subprocess.run(
        ["adb", "-s", DEVICE, "shell", "input", "swipe",
         str(x1 + jx), str(y1 + jy), str(x2 + jx), str(y2 + jy), str(duration_ms)],
        capture_output=True, timeout=10,
    )
    time.sleep(settle)


def scroll_down(distance="normal", settle=3.0):
    """Scroll down (drag finger upward). Conservative distance with jitter."""
    if distance == "short":
        swipe(540, 1050, 540, 750, settle=settle)
    else:
        swipe(540, 1350, 540, 750, settle=settle)


def scroll_up(distance="normal", settle=3.0):
    """Scroll up (drag finger downward). Conservative distance with jitter."""
    if distance == "short":
        swipe(540, 750, 540, 1050, settle=settle)
    else:
        swipe(540, 750, 540, 1350, settle=settle)


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

    # Live race animation — "Photo" at bottom + "Commentary" visible
    has_photo_bottom = False
    for text, conf, y_pos in results:
        if conf >= 0.5 and text.strip() == "Photo" and y_pos > 1800:
            has_photo_bottom = True
    if has_photo_bottom and has("Commentary"):
        return "race_live"

    # Victory concert — "Photo" at bottom, no game UI
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

    # Race confirm popup: has Cancel + Race + "Enter race?"
    # Must be checked BEFORE Cancel+OK block — background OCR can bleed "OK" through overlay
    if has("Cancel") and has("Race") and has("Enter race"):
        return "race_confirm"

    # Popup screens (checked first — they overlay other screens)
    if has("Cancel") and has("OK"):
        if has("Rest") and has("recover energy"):
            return "rest_confirm"
        if has("Infirmary") or has("infirmary"):
            return "infirmary_confirm"
        if has("enter this race"):
            return "race_confirm"
        if has("Playback") or has("Songs") or has("Landscape") or has("Portrait"):
            return "concert_confirm"
        if has("Recreation") or has("fun outing"):
            return "recreation_confirm"
        return "warning_popup"

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

    # Race Day — no Training/Rest buttons, just Race! + Skills
    # TS Climax variant also has Shop
    if has("Race Day") and has("Race!"):
        if has("TS CLIMAX") or has("Climax"):
            return "ts_climax_race"
        return "race_day"

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

    # Cutscene / animation result: has Skip/Quick but no main nav.
    # Event screens also have "Skip Off" + "Quick" + "Log" — exclude via Log.
    if has("Skip") and has("Quick") and not has("Rest") and not has("Races") and not has("Log"):
        return "cutscene"

    # Dark overlay with choice box — event choice on dimmed background.
    # When Skip is toggled off, events show dialogue then present choices
    # on a dimmed screen. Detect by checking: dark top half + bright choice band.
    top_brightness = 0
    for x in range(200, 900, 40):
        r, g, b = px(img, x, 400)
        top_brightness += r + g + b
    choice_brightness = 0
    choice_band_y = 0
    for y in range(900, 1500, 50):
        band = 0
        for x in range(100, 980, 40):
            r, g, b = px(img, x, y)
            band += r + g + b
        if band > choice_brightness:
            choice_brightness = band
            choice_band_y = y
    if top_brightness < 3000 and choice_brightness > 10000:
        log(f"Dimmed event choice detected (top={top_brightness}, band={choice_brightness} at y={choice_band_y})")
        return "event_choice_dimmed"

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


def count_portraits(img):
    """Count support card portraits on a training preview screenshot.

    Detects the dark gray friendship gauge bar backgrounds that appear below
    each portrait icon on the right side of the screen (x=940-1060).
    Real gauge bars are uniform neutral gray ~(73-77, 72-77, 73-77).
    Character art can have dark pixels too, so we require neutral gray
    (low channel spread) to avoid false positives from hair/clothing.
    """
    bar_ys = []
    for y in range(350, 980):
        gray_count = 0
        for x in range(940, 1060, 10):
            r, g, b = img.getpixel((x, y))[:3]
            # Must be dark AND neutral gray (channels within 10 of each other)
            is_dark = r < 85 and g < 85 and b < 85
            is_neutral = abs(r - g) < 10 and abs(g - b) < 10 and abs(r - b) < 10
            if is_dark and is_neutral:
                gray_count += 1
        if gray_count >= 6:
            bar_ys.append(y)

    if not bar_ys:
        return 0

    # Cluster consecutive y values (gap > 30 = new portrait)
    clusters = [[bar_ys[0]]]
    for i in range(1, len(bar_ys)):
        if bar_ys[i] - bar_ys[i - 1] > 30:
            clusters.append([])
        clusters[-1].append(bar_ys[i])

    # Only count clusters with 3+ rows (filters out single-row noise)
    return sum(1 for c in clusters if len(c) >= 3)


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

        # Parse distance and fans from card text (e.g. "1400m", "+6,500 fans")
        import re
        distance = 0
        surface = "turf"
        fan_reward = 0
        for text, y_pos in card_texts:
            m = re.search(r'(\d{4})m', text)
            if m:
                distance = int(m.group(1))
            tl = text.lower()
            if "dirt" in tl:
                surface = "dirt"
            elif "turf" in tl:
                surface = "turf"
            # Parse fan reward: "+6,500 fans" or "+7000 fans"
            fm = re.search(r'\+?([\d,]+)\s*fans?', tl)
            if fm:
                fan_reward = int(fm.group(1).replace(",", ""))

        # Check aptitude from green badges
        apt_ok = has_green_aptitude_badge(img, y_min + 100, y_max - 30)

        race = RaceOption(
            name=name,
            grade=grade,
            distance=distance,
            surface=surface,
            fan_reward=fan_reward,
            is_aptitude_ok=apt_ok,
            position=i,
            tap_coords=(540, region["tap_y"]),
        )
        races.append(race)
        log(f"  Race {i+1}: '{name}' grade={grade} dist={distance}m {surface} fans={fan_reward} apt_ok={apt_ok}")

    return races


def _use_cleat_for_race(is_ts_climax=False):
    """Use a cleat hammer before a race if available.
    For TS Climax: use Master Cleat.
    For G1: use Artisan first; use Master only if we have >3 Master cleats (reserve 3 for TS Climax).
    """
    inventory = _shop_manager.inventory
    masters = inventory.get("master_hammer", 0)
    artisans = inventory.get("artisan_hammer", 0)

    cleat_key = None
    if is_ts_climax:
        if masters > 0:
            cleat_key = "master_hammer"
    else:
        if artisans > 0:
            cleat_key = "artisan_hammer"
        elif masters > 3:
            cleat_key = "master_hammer"

    if cleat_key:
        cleat_name = "Master Cleat Hammer" if cleat_key == "master_hammer" else "Artisan Cleat Hammer"
        log(f"Using {cleat_name} before race (have {masters} master, {artisans} artisan)")
        _use_training_items([cleat_key])
        if _shop_manager._inventory.get(cleat_key, 0) > 0:
            _shop_manager._inventory[cleat_key] -= 1
            if _shop_manager._inventory[cleat_key] <= 0:
                del _shop_manager._inventory[cleat_key]
        _shop_manager.save_inventory()
        time.sleep(1)


def _detect_active_effects():
    """Detect active item effects by tapping the effect indicator icon.

    Taps the left-side effect icon to open the Active Item Effects popup,
    reads it via OCR, then closes it. Updates _shop_manager._active_effects.
    Returns True if any effects were detected.
    """
    from scripts.ocr_util import ocr_image as ocr_full
    import tempfile, os

    # Tap the effect indicator icon (left side, below Result Pts)
    tap(87, 568, delay=2.0)

    popup_img = screenshot(f"active_effects_{int(time.time())}")
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    popup_img.save(tmp.name)
    results = ocr_full(tmp.name)
    os.unlink(tmp.name)

    texts = [(t.strip().lower(), c) for t, c, _ in results if c > 0.25]
    all_text = " ".join(t for t, c in texts)

    # Verify the popup actually opened
    if "active item effects" not in all_text:
        log("Active effects popup did not open — no effects active or icon not present")
        return False

    # Close the popup
    tap(460, 1361, delay=1.5)

    # Parse effects from OCR text
    found_effects = []

    # Megaphone detection
    mega_keywords = {
        "empowering": "empowering_mega",
        "motivating": "motivating_mega",
        "coaching": "coaching_mega",
    }
    for keyword, item_key in mega_keywords.items():
        if keyword in all_text:
            found_effects.append(item_key)

    # Cleat detection
    if "cleat" in all_text:
        found_effects.append("master_cleat")

    # Extract turns remaining
    turns_left = 1
    for t, c in texts:
        if "turn" in t:
            digits = re.findall(r'\d+', t)
            if digits:
                turns_left = int(digits[0])

    # Update _shop_manager._active_effects
    from uma_trainer.decision.shop_manager import ActiveEffect
    for item_key in found_effects:
        already_tracked = any(e.item_key == item_key for e in _shop_manager._active_effects)
        if not already_tracked:
            _shop_manager._active_effects.append(
                ActiveEffect(item_key=item_key, turns_remaining=turns_left + 1)
            )
            log(f"Detected active effect: {item_key} ({turns_left} turn(s) left)")

    if not found_effects:
        log(f"Active effects popup opened but no recognized effects. OCR: {all_text[:200]}")

    return len(found_effects) > 0


def _use_megaphone_if_needed(is_ts_climax=False):
    """Use a megaphone before training if one isn't already active.

    Reserves: 2 per remaining summer camp + 3 for TS Climax training turns.
    Uses extras on any training turn. Always uses on TS Climax training turns.
    """
    _shop_manager.tick_effects(_current_turn)
    has_mega = any(
        e.item_key in ("empowering_mega", "motivating_mega", "coaching_mega")
        for e in _shop_manager._active_effects
    )
    if has_mega:
        active = next(e for e in _shop_manager._active_effects if "mega" in e.item_key)
        log(f"Megaphone already active: {active.item_key} ({active.turns_remaining} turns left)")
        return

    inventory = _shop_manager.inventory
    empowering = inventory.get("empowering_mega", 0)
    motivating = inventory.get("motivating_mega", 0)
    total_megas = empowering + motivating

    # Calculate reserves needed
    ts_climax_reserve = 3
    summer_reserve = 0
    if _current_turn < 37:
        summer_reserve = 4  # 2 for classic summer + 2 for senior summer
    elif _current_turn < 61:
        summer_reserve = 2  # 2 for senior summer
    reserve = ts_climax_reserve + summer_reserve

    if is_ts_climax:
        # Always use for TS Climax, no reserve check
        pass
    elif total_megas <= reserve:
        log(f"No spare megaphones ({total_megas} held, {reserve} reserved)")
        return

    mega_key = None
    if empowering > 0:
        mega_key = "empowering_mega"
    elif motivating > 0:
        mega_key = "motivating_mega"

    if mega_key:
        context = "TS Climax" if is_ts_climax else "training"
        log(f"Using {mega_key} for {context} (have {empowering}E + {motivating}M, reserve={reserve})")
        _use_training_items([mega_key])
        if _shop_manager._inventory.get(mega_key, 0) > 0:
            _shop_manager._inventory[mega_key] -= 1
            if _shop_manager._inventory[mega_key] <= 0:
                del _shop_manager._inventory[mega_key]
        _shop_manager.save_inventory()
        _shop_manager.activate_item(mega_key)
        time.sleep(1)


def handle_race_list(img):
    """Handle race list screen using RaceSelector."""
    races = _ocr_race_list(img)
    if not races:
        log("No races detected on list — pressing Back")
        press_back()
        return "race_back"

    # Race Day forced race — pick first real race (skip header entries)
    # Detect Race Day from OCR: "Race Day" text visible on race list screen
    all_text = " ".join(t for t, c, _ in ocr_full_screen(img) if c > 0.3)
    is_race_day = "Race Day" in all_text or _last_result == "race_day_racing"
    if is_race_day:
        real_races = [r for r in races if r.distance > 0]
        pick = real_races[0] if real_races else races[-1]
        log(f"Race Day — selecting '{pick.name}' at {pick.tap_coords}")
        tap(pick.tap_coords[0], pick.tap_coords[1], delay=1.5)
        img2 = screenshot(f"race_confirm_{int(time.time())}")
        race_btn = find_green_button(img2, (1550, 1650))
        if race_btn:
            log(f"Confirming race at {race_btn}")
            tap(race_btn[0], race_btn[1])
        else:
            tap(540, 1590)
        return "race_enter"

    # Use game state from career_home (built earlier in the same process).
    if _game_state:
        state = _game_state
    else:
        log("Warning: no cached game state — building from race list (stats may be inaccurate)")
        state = build_game_state(img, "race_list")
    state.available_races = races
    action = _race_selector.decide(state)
    log(f"RaceSelector: {action.reason}")

    if action.action_type == ActionType.RACE and action.tap_coords != (0, 0):
        is_g1 = "(G1," in action.reason or "G1" in action.reason
        if is_g1:
            _use_cleat_for_race(is_ts_climax=False)
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
    """OCR the event title from the banner area, with fallback to wider scan."""
    _skip = {
        "Main Scenario Event", "Trackblazer",
        "Support Card Event", "Random Event",
        "Trainee Event",
        "T GREAT", "Energy",
    }
    try:
        texts = ocr_region(img, 0, 280, 1080, 420,
                           save_path="/tmp/event_banner.png")
        for text, conf in texts:
            if conf > 0.4 and text not in _skip:
                return text
    except Exception as e:
        log(f"OCR error: {e}")
    # Fallback: scan wider area (y=280-550) for the actual event title
    try:
        texts = ocr_region(img, 0, 280, 1080, 550,
                           save_path="/tmp/event_banner_wide.png")
        for text, conf in texts:
            if conf > 0.4 and text not in _skip:
                return text
    except Exception as e:
        log(f"OCR error (wide): {e}")
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
    # But NOT the Trackblazer scenario tutorial ("Would you like an explanation?")
    # which is a real event with Yes/No choices — let it fall through to normal handling.
    if event_name.lower() == "tutorial":
        try:
            all_text = ocr_full_screen(img)
            text_joined = " ".join(t.lower() for t, c, _ in all_text if c > 0.3)
            # Trackblazer tutorial is a regular event — handle normally
            if "would you like an explanation" in text_joined or "no, thank you" in text_joined:
                log("Trackblazer tutorial event — picking 'No, thank you' (choice 2)")
                tap(540, 1340)
                return "event_choice"
            for t, c, y in sorted(all_text, key=lambda r: r[2]):
                tl = t.strip().lower()
                if "all i need to know" in tl:
                    log(f"Tutorial — tapping 'That's all I need to know' at y={y:.0f}")
                    tap(540, int(y))
                    return "tutorial_dismiss"
                if tl in ("yes.", "yes", "yes, please."):
                    log(f"Tutorial — tapping '{t}' at y={y:.0f}")
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

    # Use EventHandler (Tier 0 overrides → KB → fallback to choice 1)
    action = _event_handler.decide(state)
    log(f"EventHandler: {action.reason} → choice {action.target}")

    if action.tap_coords != (0, 0):
        tap(action.tap_coords[0], action.tap_coords[1])
    else:
        tap(540, 1120)
    return "event"


# Skill purchase priority for End Closer builds.
# Higher number = buy first. Skills not listed get priority 1 (low).
# Hint-discounted skills get +3 bonus.
# Format: partial skill name (case-insensitive) → priority
def _get_skill_priority(name):
    """Look up skill priority from strategy overrides (fuzzy match)."""
    strategy = _overrides.get_strategy()
    if strategy.is_blacklisted(name):
        return 0
    sp = strategy.is_priority_skill(name)
    if sp is not None:
        return sp.priority
    return 0


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
            # Parse hint level from "Hint Lvl N" text
            hlvl = 1
            for word in t.split():
                if word.isdigit():
                    hlvl = int(word)
                    break
            hint_ys.add((int(y_pos), hlvl))
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
        hlvl = 0
        for hy, hl in hint_ys:
            if abs(hy - y) < 40:
                hlvl = hl
                break

        if cost > 0:
            base_prio = _get_skill_priority(t)
            # Hint multiplier: lvl 1→1.1x, 2→1.2x, 3→1.3x, 4→1.3x, 5→1.4x
            hint_mult = {0: 1.0, 1: 1.1, 2: 1.2, 3: 1.3, 4: 1.35, 5: 1.4}.get(hlvl, 1.0)
            skill = SkillOption(
                name=t,
                cost=cost,
                is_hint_skill=hlvl > 0,
                hint_level=hlvl,
                tap_coords=(960, y + 70),  # + button is to the right, slightly below name
                priority=base_prio * hint_mult,
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


_skill_shop_done = False


def _scan_all_skills():
    """Scroll through entire skill shop and collect all buyable skills.

    Returns (all_skills, sp) where all_skills is deduplicated by name.
    """
    # Scroll to top first (short swipes, fast settle)
    log("Skill shop — scrolling to top")
    for _ in range(8):
        scroll_up(settle=1.0)
    time.sleep(1)

    all_skills = {}  # name → SkillOption (dedup by name)
    sp = 0

    for page in range(15):  # more pages since we use shorter swipes
        img = screenshot(f"skill_scan_{page}_{int(time.time())}")
        skills = _ocr_skill_list(img)
        page_sp = _read_skill_pts(img)
        if page_sp > 0:
            sp = page_sp

        new_count = 0
        for s in skills:
            if s.name not in all_skills:
                all_skills[s.name] = s
                new_count += 1

        log(f"  Page {page}: {len(skills)} visible, {new_count} new (total: {len(all_skills)})")

        if not skills and page > 0:
            break  # No more skills to find
        if new_count == 0 and page > 0:
            break  # All duplicates — reached end

        scroll_down("short", settle=1.0)

    return list(all_skills.values()), sp


def handle_skill_shop(img):
    """Buy skills from the skill shop screen.

    Strategy: scan all pages, sort by priority, buy highest-priority
    skills until we hit SP reserve. At Complete Career, spend everything.
    """
    global _skill_shop_done

    # Phase 1: Scan all pages
    all_skills, sp = _scan_all_skills()

    if not all_skills:
        log(f"Skill shop — no buyable skills found ({sp} SP remaining), exiting")
        _skill_shop_done = True
        tap(40, 1830)
        return "skill_back"

    # Phase 2: Sort by priority (highest first), then by cost (cheapest first for ties)
    all_skills.sort(key=lambda s: (-s.priority, s.cost))

    is_end_game = _last_result in ("complete_career",)
    strategy = _overrides.get_strategy()
    sp_reserve = 0 if is_end_game else strategy.raw.get("skill_pts_reserve", 800)

    # Build prereq map and skill lookup from strategy
    prereqs = strategy.raw.get("skill_prereqs", {})
    skill_by_name = {}
    for s in all_skills:
        skill_by_name[s.name] = s
        # Also index by lowercase for fuzzy prereq matching
        skill_by_name[s.name.lower()] = s

    # Decide which skills to buy
    to_buy = []
    to_buy_names = set()
    remaining = sp
    for skill in all_skills:
        if skill.cost <= 0:
            continue
        if skill.priority <= 0:
            continue
        if skill.name in to_buy_names:
            continue
        # Check if this skill has a prereq we also need to buy
        prereq_skill = None
        for target_name, prereq_name in prereqs.items():
            if target_name.lower() in skill.name.lower() or skill.name.lower() in target_name.lower():
                # Find the prereq in available skills
                for s in all_skills:
                    if prereq_name.lower() in s.name.lower() or s.name.lower() in prereq_name.lower():
                        if s.name not in to_buy_names and s.cost > 0:
                            prereq_skill = s
                            break
                break
        total_cost = skill.cost + (prereq_skill.cost if prereq_skill else 0)
        if remaining - total_cost < sp_reserve:
            continue
        if prereq_skill and prereq_skill.name not in to_buy_names:
            to_buy.append(prereq_skill)
            to_buy_names.add(prereq_skill.name)
            remaining -= prereq_skill.cost
        to_buy.append(skill)
        to_buy_names.add(skill.name)
        remaining -= skill.cost

    if not to_buy:
        log(f"Skill shop — no skills worth buying (SP={sp}, reserve={sp_reserve}), exiting")
        for s in all_skills[:5]:
            log(f"  Available: {s.name} (cost={s.cost}, prio={s.priority})")
        _skill_shop_done = True
        tap(40, 1830)
        return "skill_back"

    log(f"Skill shop — buying {len(to_buy)} skills ({sp} SP, reserve {sp_reserve}):")
    for s in to_buy:
        log(f"  → {s.name} (cost={s.cost}, prio={s.priority}, hint={s.is_hint_skill})")

    # Phase 3: Scroll to top, then scroll through with short swipes to tap skills
    for _ in range(8):
        scroll_up(settle=1.0)
    time.sleep(1)

    buy_names = {s.name for s in to_buy}
    bought = set()

    for page in range(20):
        img = screenshot(f"skill_buy_{page}_{int(time.time())}")
        visible = _ocr_skill_list(img)

        for skill in visible:
            if skill.name in buy_names and skill.name not in bought:
                log(f"  Tapping + for: {skill.name} at {skill.tap_coords}")
                tap(skill.tap_coords[0], skill.tap_coords[1])
                time.sleep(0.5)
                bought.add(skill.name)

        if bought == buy_names:
            break  # All found

        scroll_down("short")

    if not bought:
        log("Skill shop — failed to tap any skills, exiting")
        _skill_shop_done = True
        tap(40, 1830)
        return "skill_back"

    # Phase 4: Confirm purchase
    log(f"Confirming {len(bought)} skill purchases")
    time.sleep(0.5)
    fresh_img = screenshot(f"skill_confirm_{int(time.time())}")
    confirm_btn = find_green_button(fresh_img, (1570, 1640), (100, 500))
    if confirm_btn:
        log(f"  Found Confirm at {confirm_btn}")
        tap(confirm_btn[0], confirm_btn[1])
    else:
        log("  Confirm button not found — tapping default coords")
        tap(270, 1600)

    # Phase 5: Handle "Learn the above skills?" confirmation dialog
    time.sleep(1.5)
    learn_img = screenshot(f"skill_learn_{int(time.time())}")
    learn_screen = detect_screen(learn_img)
    if learn_screen == "skill_confirm_dialog":
        log("  Tapping Learn on confirmation dialog")
        tap(810, 1830, delay=2.0)
        # Wait for "Skills Learned" popup and close it
        for _ in range(5):
            time.sleep(1.5)
            sl_img = screenshot(f"skill_learned_{int(time.time())}")
            sl_screen = detect_screen(sl_img)
            if sl_screen == "skills_learned":
                log("  Skills Learned popup — tapping Close")
                tap(540, 1200)
                break
            elif sl_screen == "skill_confirm_dialog":
                tap(810, 1830)
            else:
                tap(540, 960)

    return "skill_shop"


# --- Shop handling ---

_SHOP_TURN_FILE = Path("data/last_shop_turn.txt")
try:
    _last_shop_turn = int(_SHOP_TURN_FILE.read_text().strip())
except Exception:
    _last_shop_turn = -1
_needs_shop_visit = False  # Set True only on race win or explicit trigger
_inventory_checked = False  # Read Training Items on first career_home


def read_inventory_from_training_items():
    """Open Training Items screen, OCR item names and counts, update inventory."""
    global _inventory_checked
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE
    from rapidfuzz import fuzz, process

    log("Reading inventory from Training Items screen...")
    from scripts.ocr_util import ocr_image as ocr_full

    tap(*BTN_TRAINING_ITEMS, delay=2.5)
    img = screenshot(f"training_items_{int(time.time())}")

    # Verify we're on Training Items screen
    img.save("/tmp/training_items.png")
    results = ocr_full("/tmp/training_items.png")
    texts = [text for text, conf, bbox in results if conf > 0.3]
    if "Training Items" not in " ".join(texts):
        log("Not on Training Items screen — trying race-screen position")
        tap(*BTN_TRAINING_ITEMS_RACE, delay=2.5)
        img = screenshot(f"training_items_retry_{int(time.time())}")
        img.save("/tmp/training_items.png")
        results = ocr_full("/tmp/training_items.png")
        texts = [text for text, conf, bbox in results if conf > 0.3]
        if "Training Items" not in " ".join(texts):
            log("Not on Training Items screen — aborting inventory read")
            _inventory_checked = True  # Don't retry every turn
            press_back()
            return

    # Build name matcher — include stat-prefixed variants
    _STAT_PREFIXES = ("Speed", "Stamina", "Power", "Guts", "Wit")
    _STAT_VARIANT_KEYS = {"notepad", "manual", "scroll", "ankle_weights"}
    name_to_key = {}
    for key, item in ITEM_CATALOGUE.items():
        name_to_key[item.name] = key
        if key in _STAT_VARIANT_KEYS:
            for prefix in _STAT_PREFIXES:
                name_to_key[f"{prefix} {item.name}"] = key
    catalogue_names = list(name_to_key.keys())

    # Parse items: match only item name lines (skip effect descriptions, UI labels)
    skip_words = {"held", "effect", "training items", "close", "confirm use",
                  "choose how", "training stat", "race stat", "cures", "cure",
                  "shuffles", "increase", "energy", "acquires", "grants"}
    inventory = {}
    use_now = {}

    all_found_keys = set()

    # Effect text → item key fallback (for items whose name scrolled off-screen)
    effect_to_key = {
        "shuffles character appearances": "reset_whistle",
        "rearrange support cards": "reset_whistle",
        "training stat gain +40%": "motivating_mega",
        "training stat gain +60%": "empowering_mega",
        "training stat gain +20%": "coaching_mega",
        "sets training failure rate to 0%": "good_luck_charm",
        "race stat gain +20%": "artisan_hammer",
        "race stat gain +35%": "master_hammer",
        "energy +100": "royal_kale",
        "energy +20": "vita_20",
        "energy +40": "vita_40",
        "energy +65": "vita_65",
        "max energy +4": "energy_drink_max",
        "max energy +8": "energy_drink_max_ex",
        "mood +1": "plain_cupcake",
        "mood +2": "berry_cupcake",
        "cures night owl": "fluffy_pillow",
        "cures skin outbreak": "rich_hand_cream",
        "cures slow metabolism": "smart_scale",
        "cures all bad conditions": "miracle_cure",
        "cures migraine": "aroma_diffuser",
        "cures practice poor": "practice_dvd",
        "cures slacker": "pocket_planner",
        "all bond +5": "grilled_carrots",
    }

    def _scan_page(ocr_results):
        """Extract items from one page of OCR results. Skips already-found items."""
        import re
        page_keys = set()

        # Build list of all OCR entries with pixel y positions
        entries = []
        for text, conf, bbox in ocr_results:
            bx, by, bw, bh = bbox
            # Apple Vision bbox: (x, y_from_bottom, w, h) normalized
            pixel_y = (1.0 - by - bh / 2) * 1920
            entries.append((text, conf, pixel_y))

        # First pass: find item names and their y positions
        matched_items = []  # (pixel_y, item_key, item)
        matched_y_ranges = set()  # track which y-ranges have a name match
        for text, conf, pixel_y in entries:
            if conf < 0.8:
                continue
            lower = text.strip().lower()
            if any(lower.startswith(w) for w in skip_words):
                continue
            if len(lower) < 4:
                continue
            result = process.extractOne(text, catalogue_names, scorer=fuzz.token_sort_ratio, score_cutoff=80)
            if result:
                matched_name, score, _idx = result
                item_key = name_to_key[matched_name]
                item = ITEM_CATALOGUE[item_key]
                if item_key in all_found_keys and not item.use_immediately:
                    continue
                matched_items.append((pixel_y, item_key, item))
                matched_y_ranges.add(int(pixel_y // 200))

        # Fallback: match items by effect text (for names scrolled off-screen)
        # This catches items whose name is above the visible area after scrolling
        for text, conf, pixel_y in entries:
            if conf < 0.8:
                continue
            lower = text.strip().lower()
            for effect_phrase, item_key in effect_to_key.items():
                if effect_phrase in lower:
                    item = ITEM_CATALOGUE[item_key]
                    if item_key in all_found_keys and not item.use_immediately:
                        break
                    # Only suppress if a name-matched item is ABOVE this effect
                    # (i.e. the effect belongs to an already-matched item)
                    already_covered = any(my <= pixel_y and abs(pixel_y - my) < 120
                                          for my, _, _ in matched_items)
                    if already_covered:
                        break
                    matched_items.append((pixel_y, item_key, item))
                    break

        # Second pass: for each matched item, find nearby held count
        for name_y, item_key, item in matched_items:
            held_count = 1  # default
            for text, conf, py in entries:
                # Look for count near the item name/effect (within 80px)
                if abs(py - name_y) > 80:
                    continue
                # "N > N" (full held count)
                m = re.search(r'(\d+)\s*[>»]\s*(\d+)', text)
                if m:
                    held_count = int(m.group(1))
                    break
                # "N >" (OCR split the second number into a separate entry)
                m2 = re.search(r'^(\d+)\s*[>»]', text.strip())
                if m2 and int(m2.group(1)) > 0:
                    held_count = int(m2.group(1))
                    break
                # "• N" pattern
                m3 = re.match(r'[•·]\s*(\d+)', text.strip())
                if m3:
                    held_count = int(m3.group(1))
                    break

            page_keys.add(item_key)
            if item.use_immediately:
                use_now[item_key] = use_now.get(item_key, 0) + held_count
            else:
                inventory[item_key] = inventory.get(item_key, 0) + held_count
        return page_keys

    # Scan first page
    page_keys = _scan_page(results)
    all_found_keys |= page_keys

    # Scroll down and scan additional pages until no new items found
    # Use short scrolls so item names don't scroll off the top of the viewport
    for page in range(6):
        scroll_down("short")
        img = screenshot(f"training_items_p{page+2}_{int(time.time())}")
        img.save("/tmp/training_items.png")
        page_results = ocr_full("/tmp/training_items.png")
        new_keys = _scan_page(page_results)
        if not new_keys:
            break
        all_found_keys |= new_keys

    # Reset and set inventory (include use_immediately items so they can be consumed)
    _shop_manager._inventory.clear()
    for key, count in inventory.items():
        _shop_manager._inventory[key] = count
    for key, count in use_now.items():
        _shop_manager._inventory[key] = _shop_manager._inventory.get(key, 0) + count
    _shop_manager.save_inventory()
    log(f"Inventory from Training Items: {dict(_shop_manager.inventory)}")

    _inventory_checked = True

    # Use any use-immediately items (carrots, scrolls, manuals) sitting in inventory
    if use_now:
        log(f"Use-immediately items found: {use_now}")
        use_keys = []
        for key, count in use_now.items():
            use_keys.extend([key] * count)
        tap(302, 1772, delay=1.5)  # Close Training Items first
        _use_training_items(use_keys)
    else:
        tap(302, 1772, delay=1.5)  # Tap Close (left button)


def handle_shop(img):
    """Buy priority items from the shop screen, then exit."""
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE, ItemTier
    from rapidfuzz import fuzz, process

    # Read coin balance
    coins_text = ocr_region(img, 780, 575, 1060, 665, save_path="/tmp/shop_coins.png")
    coins = None
    for text, conf in coins_text:
        import re
        m = re.search(r"(\d+)", text)
        if m:
            coins = int(m.group(1))
            break
    if coins is None:
        coins = 999  # Assume we have coins if OCR fails
        log("Shop coins: OCR failed, assuming enough to shop")
    else:
        log(f"Shop coins: {coins}")

    if coins < 15:
        log("Not enough coins — exiting shop")
        press_back()
        return "shop_back"

    # Build want list from catalogue
    inventory = _shop_manager.inventory
    tier_order = {ItemTier.SS: 0, ItemTier.S: 1, ItemTier.A: 2, ItemTier.B: 3}

    # Dynamic tier overrides based on game state
    tier_overrides = {}
    friendship_deadline = 36
    if _current_turn < friendship_deadline:
        tier_overrides["grilled_carrots"] = ItemTier.SS
    else:
        tier_overrides["grilled_carrots"] = ItemTier.NEVER

    buyable = []
    for key, item in ITEM_CATALOGUE.items():
        tier = tier_overrides.get(key, item.tier)
        if tier == ItemTier.NEVER:
            continue
        owned = inventory.get(key, 0)
        if owned >= item.max_stock:
            continue
        buyable.append((tier_order.get(tier, 9), item.cost, key))
    buyable.sort()
    want_keys = [key for _, _, key in buyable]

    if not want_keys:
        log("Nothing to buy — exiting shop")
        press_back()
        return "shop_back"

    log(f"Want list: {want_keys[:8]}")

    # Build name matcher — include stat-prefixed variants for items like
    # "Stamina Scroll", "Speed Manual", "Guts Ankle Weights" etc.
    _STAT_PREFIXES = ("Speed", "Stamina", "Power", "Guts", "Wit")
    _STAT_VARIANT_KEYS = {"notepad", "manual", "scroll", "ankle_weights"}
    name_to_key = {}
    for key, item in ITEM_CATALOGUE.items():
        name_to_key[item.name] = key
        if key in _STAT_VARIANT_KEYS:
            for prefix in _STAT_PREFIXES:
                name_to_key[f"{prefix} {item.name}"] = key
    catalogue_names = list(name_to_key.keys())

    # Scan and select items across scroll pages
    selected_keys = []
    spent = 0
    tapped_positions = []

    for scroll in range(5):
        if scroll > 0:
            scroll_down("short")
            time.sleep(3.0)
            img = screenshot(f"shop_scroll_{scroll}_{int(time.time())}")

        # Scan visible items at y=700, 150px spacing
        y = 700
        while y < 1450:
            texts = ocr_region(img, 130, y, 700, y + 45, save_path="/tmp/shop_item.png")
            name_text = " ".join(t.strip() for t, c in texts if c > 0.3).strip()
            if not name_text or len(name_text) < 3:
                y += 30
                continue

            lower = name_text.lower()
            if any(lower.startswith(w) for w in ("cost", "effect", "choose", "x1", "xl")):
                y += 30
                continue

            result = process.extractOne(name_text, catalogue_names, scorer=fuzz.token_sort_ratio, score_cutoff=65)
            if result is None:
                y += 30
                continue

            matched_name, score, _idx = result
            item_key = name_to_key[matched_name]

            # Skip if matched item is NEVER-tier (after overrides)
            effective_item_tier = tier_overrides.get(item_key, ITEM_CATALOGUE[item_key].tier)
            if effective_item_tier == ItemTier.NEVER:
                y += 150
                continue

            # Check if already purchased (OCR + orange pixel fallback)
            right_texts = ocr_region(img, 700, y + 20, 1050, y + 80, save_path="/tmp/shop_right.png")
            right_text = " ".join(t.strip().lower() for t, c in right_texts if c > 0.3)
            is_purchased = "purchased" in right_text or "purch" in right_text
            if not is_purchased:
                # Fallback: check for orange "Purchased" badge pixel
                try:
                    pr, pg, pb = img.getpixel((980, y + 40))[:3]
                    is_purchased = pr > 200 and pg < 150 and pb < 100
                except Exception:
                    pass

            if is_purchased or item_key not in want_keys:
                y += 150
                continue

            # Check stock limit
            item = ITEM_CATALOGUE[item_key]
            owned = inventory.get(item_key, 0)
            already_selected = sum(1 for k in selected_keys if k == item_key)
            if owned + already_selected >= item.max_stock:
                y += 150
                continue

            # Check affordability — reserve 100 coins for SS/S tier items
            effective_tier = tier_overrides.get(item_key, item.tier)
            coin_reserve = 0 if effective_tier in (ItemTier.SS, ItemTier.S) else 100
            if coins is not None and (spent + item.cost + coin_reserve) > coins:
                y += 150
                continue

            # Deduplicate (don't tap same position twice)
            abs_y = y + scroll * 350
            if any(abs(abs_y - py) < 200 and pk == item_key for pk, py in tapped_positions):
                y += 150
                continue

            # Select item — checkbox is ~130px below name text, centered at x=915
            log(f"  Selecting: {item.name} ({item.cost} coins) at y={y}")
            tap(915, y + 130, delay=0.5)
            tapped_positions.append((item_key, abs_y))
            selected_keys.append(item_key)
            spent += item.cost
            y += 150

    if selected_keys:
        log(f"Confirming purchase of {len(selected_keys)} items ({spent} coins): {selected_keys}")
        # Tap Confirm button
        tap(540, 1640, delay=2.0)
        # Tap Exchange button
        tap(810, 1780, delay=2.0)
        # Tap Close on Exchange Complete
        tap(270, 1780, delay=2.0)

        for key in selected_keys:
            item = ITEM_CATALOGUE[key]
            if not item.use_immediately:
                _shop_manager.add_item(key)
            else:
                log(f"  {item.name} — used immediately, not added to inventory")
        _shop_manager.save_inventory()
        log(f"Inventory updated: {dict(_shop_manager.inventory)}")
    else:
        log("No items selected for purchase")

    # Track which use-immediately items were bought
    use_now_keys = [k for k in selected_keys if ITEM_CATALOGUE[k].use_immediately]

    # Exit shop
    for attempt in range(3):
        press_back()
        time.sleep(2.0)
        img2 = screenshot(f"shop_exit_{int(time.time())}")
        screen2 = detect_screen(img2)
        if screen2 != "shop":
            log(f"Exited shop (attempt {attempt + 1})")
            break
    else:
        log("WARNING: Could not exit shop after 3 attempts")
        return "shop_stuck"

    # Use immediately-consumable items via Training Items screen
    if use_now_keys:
        time.sleep(1.0)
        _use_training_items(use_now_keys)

    return "shop_done"


def _use_training_items(item_keys):
    """Open Training Items, tap '+' for each item in item_keys, then Confirm Use."""
    from scripts.ocr_util import ocr_image as ocr_full

    # Keyword-based matching: game prefixes stat name (e.g. "Guts Manual", "Power Scroll")
    keyword_to_key = {
        "manual": "manual",
        "scroll": "scroll",
        "carrots": "grilled_carrots",
        "grilled": "grilled_carrots",
        "fluffy": "fluffy_pillow",
        "pillow": "fluffy_pillow",
        "hand cream": "rich_hand_cream",
        "miracle": "miracle_cure",
        "practice drills": "practice_dvd",
        "pocket planner": "pocket_planner",
        "smart scale": "smart_scale",
        "aroma": "aroma_diffuser",
        "empowering": "empowering_mega",
        "motivating": "motivating_mega",
        "coaching": "coaching_mega",
        "royal kale": "royal_kale",
        "vita 20": "vita_20",
        "vita 40": "vita_40",
        "vita 65": "vita_65",
        "energy drink max": "energy_drink_max",
        "ankle weights": "ankle_weights",
        "artisan": "artisan_hammer",
        "master": "master_hammer",
        "good-luck": "good_luck_charm",
        "reset whistle": "reset_whistle",
    }

    # Count how many of each item to use
    use_counts = {}
    for k in item_keys:
        use_counts[k] = use_counts.get(k, 0) + 1

    log(f"Using training items: {use_counts}")
    tap(*BTN_TRAINING_ITEMS, delay=3.0)  # Open Training Items (extra delay for load)

    # Verify we actually opened the Training Items screen
    verify_img = screenshot(f"use_items_verify_{int(time.time())}")
    verify_img.save("/tmp/use_items_verify.png")
    verify_results = ocr_full("/tmp/use_items_verify.png")
    verify_texts = " ".join(t for t, c, b in verify_results if c > 0.5)
    if "Training Items" not in verify_texts:
        log("Training Items screen did not open — trying race-screen position")
        tap(*BTN_TRAINING_ITEMS_RACE, delay=3.0)
        verify_img = screenshot(f"use_items_verify2_{int(time.time())}")
        verify_img.save("/tmp/use_items_verify.png")
        verify_results = ocr_full("/tmp/use_items_verify.png")
        verify_texts = " ".join(t for t, c, b in verify_results if c > 0.5)
        if "Training Items" not in verify_texts:
            log("Training Items screen still did not open — aborting item use")
            return False

    # Use verify screenshot as first page (already loaded)
    first_page_img = verify_img
    first_page_results = verify_results

    used_any = False
    for scroll_page in range(4):
        if not use_counts:
            break
        if scroll_page > 0:
            scroll_down()
            time.sleep(1.5)

        if scroll_page == 0:
            img = first_page_img
            results = first_page_results
        else:
            img = screenshot(f"use_items_{int(time.time())}")
            img.save("/tmp/use_items.png")
            results = ocr_full("/tmp/use_items.png")

        # Find green "+" button positions by scanning for green pixels
        h = img.size[1]
        plus_positions = []
        green_ys = []
        for y in range(160, 1600, 5):
            green_count = 0
            for x in range(950, 1010, 5):
                r, g, b = img.getpixel((x, y))[:3]
                if g > 150 and g > r + 30 and g > b + 30:
                    green_count += 1
            if green_count >= 3:
                green_ys.append(y)
        if green_ys:
            clusters = [[green_ys[0]]]
            for i in range(1, len(green_ys)):
                if green_ys[i] - green_ys[i - 1] > 50:
                    clusters.append([])
                clusters[-1].append(green_ys[i])
            plus_positions = [sum(c) // len(c) for c in clusters]

        log(f"  Page {scroll_page}: {len(plus_positions)} green + buttons at y={plus_positions}")

        # Build ordered list of matching item names on screen
        items_on_screen = []
        for text, conf, bbox in results:
            if conf < 0.8:
                continue
            lower = text.strip().lower()
            for keyword, key in keyword_to_key.items():
                if keyword in lower and key in use_counts:
                    bx, by, bw, bh = bbox
                    name_y = (1.0 - by - bh) * h
                    items_on_screen.append((name_y, key, text.strip()))
                    break
        items_on_screen.sort(key=lambda x: x[0])
        if items_on_screen:
            log(f"  Page {scroll_page}: matched items: {[(n, k, int(y)) for y, k, n in items_on_screen]}")
        else:
            # Log all OCR text to help debug
            all_texts = [(t.strip(), round(c, 2)) for t, c, b in results if c > 0.5 and len(t.strip()) > 2]
            log(f"  Page {scroll_page}: no matching items. OCR saw: {all_texts[:15]}")

        # Match each item to the "+" button in its row (below name, within 120px)
        for name_y, item_key, display_name in items_on_screen:
            if item_key not in use_counts:
                continue
            best_btn = None
            best_dist = 999
            for py in plus_positions:
                dist = py - name_y  # + button should be below name
                if 0 <= dist <= 120 and dist < best_dist:
                    best_dist = dist
                    best_btn = py
            if best_btn is None:
                continue
            # Tap "+" for this row — once for most items, multiple for stacked items.
            # For items sharing a key across rows (e.g. 4 manuals), tap once per row.
            # For items stacked in one row (e.g. 2x Grilled Carrots), tap all remaining.
            remaining = use_counts[item_key]
            idx = items_on_screen.index((name_y, item_key, display_name))
            future_rows = sum(1 for _, k, _ in items_on_screen[idx+1:] if k == item_key)
            taps = max(1, remaining - future_rows)
            log(f"  {display_name}: tapping + {taps}x at (975, {best_btn})")
            for _ in range(taps):
                tap(975, best_btn, delay=0.3)
            used_any = True
            use_counts[item_key] -= taps
            if use_counts[item_key] <= 0:
                del use_counts[item_key]

        if not use_counts:
            break

    if used_any:
        log("Tapping Confirm Use")
        tap(*BTN_ITEMS_CONFIRM, delay=2.0)
        # Confirmation popup: "Use Training Items" — same position
        tap(*BTN_ITEMS_CONFIRM, delay=3.0)
        # Result screen with "Close"
        tap(*BTN_ITEMS_CLOSE, delay=2.0)
    else:
        log("No items found to use — tapping Close")
        tap(*BTN_ITEMS_CLOSE, delay=1.5)
    return used_any


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

        # Count support card portraits and read bond levels
        n_cards = count_portraits(img)

        # Read bond gauge fill levels for each card on this tile
        import numpy as np
        from uma_trainer.perception.pixel_analysis import read_bond_levels
        frame_rgb = np.array(img.convert("RGB"))
        frame_bgr = frame_rgb[:, :, ::-1].copy()
        bond_levels = read_bond_levels(frame_bgr)
        # Pad/trim to match card count
        if len(bond_levels) < n_cards:
            bond_levels.extend([80] * (n_cards - len(bond_levels)))
        bond_levels = bond_levels[:n_cards]

        # Identify cards via portrait matching and update bond tracker
        card_ids = _card_tracker.identify_cards(frame_bgr, n_cards, bond_levels)

        stat_type = StatType(tile_name.lower())
        tile = TrainingTile(
            stat_type=stat_type,
            tap_coords=(tx, ty),
            stat_gains={k.lower(): v for k, v in gains.items()},
            support_cards=card_ids,
            bond_levels=bond_levels,
        )
        tiles.append(tile)

        bond_str = f" bonds={bond_levels}" if bond_levels else ""
        gains_str = ", ".join(f"{k}+{v}" for k, v in sorted(gains.items()))
        log(f"  {tile_name}: total={tile.total_stat_gain}, cards={n_cards}{bond_str} ({gains_str})")

    # Build GameState and let the scorer decide
    energy = get_energy_level(img)
    state = build_game_state(img, "training", energy=energy)
    state.training_tiles = tiles
    state.all_bonds_maxed = _card_tracker.all_bonds_maxed()

    if _card_tracker.card_count > 0:
        log(f"Bond tracker: {_card_tracker.summary()}")

    action = _scorer.best_action(state)
    scored_tiles = _scorer.score_tiles(state) if state.training_tiles else []
    best_score = scored_tiles[0][1] if scored_tiles else 0
    log(f"Scorer decision: {action.action_type.value} — {action.reason}")
    for st_tile, st_score in scored_tiles:
        log(f"  {st_tile.stat_type.value:8s}: score={st_score:5.1f}  cards={len(st_tile.support_cards)}  gains={dict(st_tile.stat_gains) if st_tile.stat_gains else {}}")

    # Summer camp / TS Climax: use reset whistle if best score is underwhelming
    global _summer_whistle_used
    WHISTLE_THRESHOLD = 40
    summer_turns = set(range(37, 41)) | set(range(61, 65))
    is_whistle_turn = _current_turn in summer_turns or _current_turn >= 72
    if (is_whistle_turn
            and best_score < WHISTLE_THRESHOLD
            and not _summer_whistle_used
            and _shop_manager.inventory.get("reset_whistle", 0) > 0):
        phase = "TS CLIMAX" if _current_turn >= 72 else "SUMMER CAMP"
        log(f"{phase} — Best score {best_score:.1f} < {WHISTLE_THRESHOLD}, backing out to use Reset Whistle")
        _summer_whistle_used = True
        tap(80, 1855)  # Back to career home
        time.sleep(2)
        if _use_training_items(["reset_whistle"]):
            if _shop_manager._inventory.get("reset_whistle", 0) > 0:
                _shop_manager._inventory["reset_whistle"] -= 1
                if _shop_manager._inventory["reset_whistle"] <= 0:
                    del _shop_manager._inventory["reset_whistle"]
            _shop_manager.save_inventory()
            log(f"{phase} — Whistle used, re-entering training")
        else:
            log(f"{phase} — Failed to use Reset Whistle")
        return "training_back_to_rest"  # Re-enters training via summer/TS handler

    if action.action_type == ActionType.REST:
        log("Scorer says rest — tapping Back to return to career home")
        _scenario.on_non_race_action()
        tap(80, 1855)
        return "training_back_to_rest"

    # Find the tile the scorer chose and tap it
    _scenario.on_non_race_action()
    if action.tap_coords != (0, 0):
        tx, ty = action.tap_coords
    else:
        # Fallback: pick highest total gain
        best = max(tiles, key=lambda t: t.total_stat_gain)
        tx, ty = best.tap_coords
    tap(tx, ty, delay=1)
    tap(tx, ty)
    return "training"


def _wait_for_career_home(tag=""):
    """Screenshot + detect in a loop until we're back on career_home. Max 5 attempts."""
    for attempt in range(5):
        img = screenshot(f"career_home_wait_{tag}_{int(time.time())}")
        s = detect_screen(img)
        if s == "career_home":
            return img
        # Try dismissing popups/dialogs
        if s == "warning_popup":
            ok = find_green_button(img, (1150, 1350))
            if ok:
                tap(ok[0], ok[1])
        elif s == "rest_confirm":
            tap(270, 1250)  # Cancel — never confirm unintended rest
        elif s == "recreation_confirm":
            tap(270, 1260)  # Cancel
        elif s == "unknown":
            btn = find_green_button(img, (1780, 1900))
            if btn:
                tap(btn[0], btn[1])
            else:
                tap(540, 960)
        else:
            tap(540, 960)
        time.sleep(1)
    return None


def _handle_career_home(img):
    """Full career_home handler: gather state → housekeeping → decide → act."""
    global _game_state, _skill_shop_done, _needs_shop_visit, _last_shop_turn
    global _inventory_checked, _summer_whistle_used

    # =====================================================================
    # PHASE 1: Gather state (stats, aptitudes, conditions, inventory)
    # =====================================================================
    _skill_shop_done = False
    _summer_whistle_used = False
    energy = get_energy_level(img)
    build_game_state(img, "career_home", energy=energy)

    is_pre_debut = _current_turn < 12  # Pre-Debut: no shop, no inventory, no effects

    if not is_pre_debut:
        _detect_active_effects()
        img = _wait_for_career_home("post_effects")
        if img is None:
            return "recovering"
        energy = get_energy_level(img)
    log(f"Energy: ~{energy}% | Turn: {_current_turn} | Consecutive races: {_scenario._consecutive_races}")

    # Read Full Stats (aptitudes + conditions)
    read_fullstats()
    time.sleep(1)
    img = _wait_for_career_home("post_stats")
    if img is None:
        return "recovering"
    energy = get_energy_level(img)

    # Build authoritative game state with aptitudes
    _game_state = build_game_state(img, "career_home", energy=energy)

    # Read inventory on first turn, then every 6 turns (synced with shop refreshes)
    if not is_pre_debut and (not _inventory_checked or _current_turn % 6 == 0):
        read_inventory_from_training_items()
        time.sleep(1)
        img = _wait_for_career_home("post_inv")
        if img is None:
            return "recovering"
        energy = get_energy_level(img)

    log(f"State gathered: Turn {_current_turn}, Energy {energy}%, "
        f"Stats Spd={_current_stats.speed} Sta={_current_stats.stamina} "
        f"Pow={_current_stats.power} Gut={_current_stats.guts} Wit={_current_stats.wit} "
        f"SP={_skill_pts}")

    # =====================================================================
    # PHASE 2: Housekeeping (cure, shop, consumables, mood)
    # =====================================================================

    # Cure conditions
    if _active_conditions:
        cure_conditions_from_inventory()
        time.sleep(1)
        img = _wait_for_career_home("post_cure")
        if img is None:
            return "recovering"

    # Shop visit
    should_shop = _needs_shop_visit or (
        _current_turn >= 6 and _current_turn % 6 == 0 and _last_shop_turn != _current_turn
    )
    if should_shop:
        reason = "flagged" if _needs_shop_visit else f"refresh turn ({_current_turn})"
        log(f"Visiting shop — {reason}")
        _last_shop_turn = _current_turn
        _SHOP_TURN_FILE.write_text(str(_current_turn))
        _needs_shop_visit = False
        tap(*BTN_SHOP, delay=2.5)
        img3 = screenshot(f"shop_visit_{int(time.time())}")
        screen3 = detect_screen(img3)
        if screen3 == "shop":
            shop_result = handle_shop(img3)
            # Wait for shop to finish and return to career_home
            for _ in range(15):
                time.sleep(2)
                img3 = screenshot(f"shop_exit_{int(time.time())}")
                s3 = detect_screen(img3)
                if s3 == "career_home":
                    break
                elif s3 == "shop":
                    handle_shop(img3)
                elif s3 in ("warning_popup", "unknown"):
                    btn = find_green_button(img3, (1780, 1900))
                    if btn:
                        tap(btn[0], btn[1])
                    else:
                        tap(540, 960)
        img = _wait_for_career_home("post_shop")
        if img is None:
            return "recovering"
        energy = get_energy_level(img)

    # Use consumables (manuals, scrolls, grilled carrots)
    use_now_inv = {k: v for k, v in _shop_manager.inventory.items()
                   if k in ("manual", "scroll", "grilled_carrots") and v > 0}
    if use_now_inv:
        log(f"Using consumables from inventory: {use_now_inv}")
        use_keys = []
        for k, count in use_now_inv.items():
            use_keys.extend([k] * count)
        _use_training_items(use_keys)
        for k in use_now_inv:
            _shop_manager._inventory.pop(k, None)
        _shop_manager.save_inventory()
        time.sleep(1)
        img = _wait_for_career_home("post_use")
        if img is None:
            return "recovering"

    # Mood management
    mood = detect_mood(img)
    if _current_turn >= 29 and mood in ("NORMAL", "BAD"):
        inventory = _shop_manager.inventory
        cupcake_key = None
        if mood == "BAD" and inventory.get("berry_cupcake", 0) > 0:
            cupcake_key = "berry_cupcake"
        elif inventory.get("plain_cupcake", 0) > 0:
            cupcake_key = "plain_cupcake"
        elif inventory.get("berry_cupcake", 0) > 0:
            cupcake_key = "berry_cupcake"
        if cupcake_key:
            log(f"Mood {mood} — using {cupcake_key} to boost")
            _use_training_items([cupcake_key])
            if _shop_manager._inventory.get(cupcake_key, 0) > 0:
                _shop_manager._inventory[cupcake_key] -= 1
                if _shop_manager._inventory[cupcake_key] <= 0:
                    del _shop_manager._inventory[cupcake_key]
            _shop_manager.save_inventory()
            time.sleep(1)
            img = _wait_for_career_home("post_cupcake")
            if img is None:
                return "recovering"

    # Skill shop — visit if SP exceeds threshold (configurable via strategy.yaml)
    sp_threshold = _overrides.get_strategy().raw.get("skill_shop_sp_threshold", 1200)
    if _skill_pts > sp_threshold and not _skill_shop_done:
        log(f"SP {_skill_pts} > {sp_threshold} — visiting skill shop")
        tap(*BTN_HOME_SKILLS)
        time.sleep(2)
        for _ in range(20):
            img_sk = screenshot(f"skill_visit_{int(time.time())}")
            s_sk = detect_screen(img_sk)
            if s_sk == "skill_shop":
                result = handle_skill_shop(img_sk)
                if result == "skill_back":
                    break
            elif s_sk == "career_home":
                break
            elif s_sk in ("warning_popup", "unknown"):
                btn = find_green_button(img_sk, (1100, 1350))
                if btn:
                    tap(btn[0], btn[1])
                else:
                    tap(540, 960)
            else:
                tap(540, 960)
            time.sleep(2)
        img = _wait_for_career_home("post_skills")
        if img is None:
            return "recovering"
        energy = get_energy_level(img)

    # =====================================================================
    # PHASE 3: Decide action (rest / race / train)
    # =====================================================================

    # Slacker is debilitating — go to Infirmary immediately if still active
    if "slacker" in _active_conditions:
        log("SLACKER detected — going to Infirmary immediately")
        tap(*BTN_INFIRMARY)
        return "rest"  # Infirmary confirm handled same as rest confirm

    # If we just came back from race_list with no good races, train or rest
    if _last_result == "race_back":
        _scenario.on_non_race_action()
        summer_camp_rb = _scenario.get_event_turns("summer_camp")
        if energy < 50 and _current_turn not in summer_camp_rb:
            log(f"No good races, energy {energy}% — resting")
            tap(*BTN_REST)
            return "rest"
        log("No good races available — going to Training instead")
        tap(*BTN_TRAINING)
        return "going_to_training"

    # Energy budget lookahead — check if we need to conserve for an upcoming milestone
    mood = detect_mood(img)
    conserve, conserve_reason = should_conserve_energy(
        _current_turn, energy, _shop_manager.inventory, mood,
    )

    # Ask the race selector (handles: hard cap, G1/goal,
    # scenario fatigue chain, early game skip, race rhythm, low energy racing)
    _game_state.energy = energy
    race_action = _race_selector.should_race_this_turn(_game_state)

    if race_action:
        # Conservation overrides non-mandatory races (rhythm, low-energy races)
        # but NOT goal races or G1s — those are too important to skip
        is_mandatory = "Goal race" in race_action.reason or "G1 available" in race_action.reason
        if conserve and not is_mandatory:
            log(f"Lookahead: conserving energy — {conserve_reason}")
            log(f"  (would have raced: {race_action.reason})")
            _scenario.on_non_race_action()
        else:
            log(f"Racing: {race_action.reason}")
            tap(*BTN_HOME_RACES)
            return "going_to_races"
    else:
        # Not racing — notify scenario to reset consecutive race counter
        _scenario.on_non_race_action()

    # Rest vs train — use lookahead budget instead of fixed threshold
    if conserve:
        log(f"Energy {energy}%, conserving for milestone — resting")
        tap(*BTN_REST)
        return "rest"

    summer_camp = _scenario.get_event_turns("summer_camp")
    in_summer = _current_turn in summer_camp
    if energy < 50 and not in_summer:
        log(f"Energy {energy}% too low — resting")
        tap(*BTN_REST)
        return "rest"

    log(f"Training turn, energy {energy}%")
    _use_megaphone_if_needed()
    tap(*BTN_TRAINING)
    return "going_to_training"


_INTERMEDIATE_RESULTS = {
    "going_to_races", "going_to_training", "race_confirm", "pre_race",
    "race_enter", "result_pts", "standings_next", "tap_prompt",
    "cutscene_skip", "tutorial_slide", "goal_complete", "fan_class",
    "unlock_popup", "trophy_won", "race_lineup", "post_race_next",
    "shop_popup_enter", "unknown", "event_choice", "skill_confirm", "skills_learned_close",
    "recovering", "placement_next", "ts_climax_racing", "race_day_racing",
    "ts_climax_standings", "ts_standings_next", "post_career_next",
    "post_career_confirm", "career_finishing", "warning_ok",
    "recreation_cancel", "rest_confirm", "race_back", "training_back_to_rest",
    "race_live_skip",
}

def run_one_turn(stop_before=None):
    """Execute one full game turn. Loops through intermediate screens.

    Args:
        stop_before: set of screen names. If detected, return immediately
                     WITHOUT acting (e.g. {"complete_career"} to stop at
                     end-of-career without opening skill shop).
    """
    global _last_result
    for _ in range(50):
        result = _run_one_turn_inner(stop_before=stop_before)
        _last_result = result
        log(f"Result: {result}")
        if result not in _INTERMEDIATE_RESULTS:
            return result
        time.sleep(2.5)
    log("run_one_turn: hit 50 action limit")
    return result


def _run_one_turn_inner(stop_before=None):
    """Internal: execute one game action."""
    global _last_result, _needs_shop_visit, _last_shop_turn, _inventory_checked, _skill_shop_done, _summer_whistle_used

    img = screenshot(f"auto_{int(time.time())}")
    screen = detect_screen(img)
    log(f"Detected: {screen}")

    if stop_before and screen in stop_before:
        log(f"Stop-before triggered: {screen}")
        return f"stopped:{screen}"

    if screen == "career_home_summer":
        # SUMMER CAMP: train as much as possible, never race

        # Read inventory on first encounter (same as career_home init)
        if not _inventory_checked:
            read_inventory_from_training_items()
            time.sleep(1)
            img = screenshot(f"summer_post_inv_{int(time.time())}")
            if detect_screen(img) != "career_home_summer":
                return "recovering"

        energy = get_energy_level(img)
        _detect_active_effects()
        # Re-screenshot after popup close
        img = screenshot(f"summer_post_effects_{int(time.time())}")
        if detect_screen(img) != "career_home_summer":
            return "recovering"
        energy = get_energy_level(img)
        mood = detect_mood(img)
        inventory = _shop_manager.inventory
        active_megas = [e for e in _shop_manager._active_effects if "mega" in e.item_key]
        mega_info = f"{active_megas[0].item_key} ({active_megas[0].turns_remaining} left)" if active_megas else "none"
        log(f"SUMMER CAMP — Energy: ~{energy}%, Mood: {mood}, Megaphone: {mega_info}")

        # 1. Mood check — AWFUL/BAD must be fixed first via Recreation
        #    Do NOT use megaphone — it would waste a turn of the buff
        if mood in ("AWFUL", "BAD"):
            log(f"SUMMER CAMP — Mood {mood}, doing Rest & Recreation")
            tap(210, 1460)
            return "recreation"

        # 2. Energy check — can we train?
        can_train = True
        if energy < 50:
            # Try energy recovery items
            energy_item = None
            for key in ("vita_65", "vita_40", "vita_20", "royal_kale"):
                if inventory.get(key, 0) > 0:
                    energy_item = key
                    break
            has_charm = inventory.get("good_luck_charm", 0) > 0

            if energy_item:
                log(f"SUMMER CAMP — Low energy, using {energy_item}")
                if _use_training_items([energy_item]):
                    if _shop_manager._inventory.get(energy_item, 0) > 0:
                        _shop_manager._inventory[energy_item] -= 1
                        if _shop_manager._inventory[energy_item] <= 0:
                            del _shop_manager._inventory[energy_item]
                    _shop_manager.save_inventory()
                    time.sleep(1)
                    img = screenshot(f"summer_post_item_{int(time.time())}")
                    if detect_screen(img) != "career_home_summer":
                        return "recovering"
                    energy = get_energy_level(img)
                    log(f"SUMMER CAMP — Energy after item: ~{energy}%")
                else:
                    log(f"SUMMER CAMP — Failed to use {energy_item}")
            elif has_charm:
                log(f"SUMMER CAMP — Low energy, using Good-Luck Charm (0%% failure)")
                if _use_training_items(["good_luck_charm"]):
                    if _shop_manager._inventory.get("good_luck_charm", 0) > 0:
                        _shop_manager._inventory["good_luck_charm"] -= 1
                        if _shop_manager._inventory["good_luck_charm"] <= 0:
                            del _shop_manager._inventory["good_luck_charm"]
                    _shop_manager.save_inventory()
                    time.sleep(1)
                    img = screenshot(f"summer_post_charm_{int(time.time())}")
                    if detect_screen(img) != "career_home_summer":
                        return "recovering"
                    log(f"SUMMER CAMP — Good-Luck Charm active, safe to train")
                else:
                    log(f"SUMMER CAMP — Failed to use Good-Luck Charm")
            else:
                can_train = False

        if not can_train:
            log(f"SUMMER CAMP — Energy ~{energy}%, no recovery items — resting")
            _scenario.on_non_race_action()
            tap(*BTN_REST)
            time.sleep(2)
            img2 = screenshot(f"rest_check_{int(time.time())}")
            s2 = detect_screen(img2)
            if "confirm" in s2 or "warning" in s2:
                ok = find_green_button(img2, (1150, 1350))
                if ok:
                    tap(ok[0], ok[1])
            return "rest"

        # 3. Ensure a megaphone buff is active
        _shop_manager.tick_effects(_current_turn)
        has_mega = any(
            e.item_key in ("empowering_mega", "motivating_mega", "coaching_mega")
            for e in _shop_manager._active_effects
        )
        if not has_mega:
            # Use best available: Empowering (+60%, 2 turns) > Motivating (+40%, 3 turns)
            mega_key = None
            if inventory.get("empowering_mega", 0) > 0:
                mega_key = "empowering_mega"
            elif inventory.get("motivating_mega", 0) > 0:
                mega_key = "motivating_mega"
            if mega_key:
                log(f"SUMMER CAMP — No active megaphone, using {mega_key}")
                if _use_training_items([mega_key]):
                    if _shop_manager._inventory.get(mega_key, 0) > 0:
                        _shop_manager._inventory[mega_key] -= 1
                        if _shop_manager._inventory[mega_key] <= 0:
                            del _shop_manager._inventory[mega_key]
                    _shop_manager.save_inventory()
                    _shop_manager.activate_item(mega_key)
                else:
                    log(f"SUMMER CAMP — Failed to use {mega_key}")
                time.sleep(1)
                img = screenshot(f"summer_post_mega_{int(time.time())}")
                if detect_screen(img) != "career_home_summer":
                    return "recovering"
        else:
            active_mega = next(e for e in _shop_manager._active_effects if "mega" in e.item_key)
            log(f"SUMMER CAMP — Megaphone active: {active_mega.item_key} ({active_mega.turns_remaining} turns left)")

        log(f"SUMMER CAMP — Energy ~{energy}% OK — going to Training")
        tap(*BTN_TRAINING)
        return "going_to_training"

    if screen == "race_day":
        log("Race Day — tapping Race!")
        tap(620, 1680)
        return "race_day_racing"

    if screen == "ts_climax_race":
        # Dismiss any trainee dialogue overlay first
        tap(540, 500, delay=1.0)
        if not _inventory_checked:
            read_inventory_from_training_items()
            time.sleep(1)
            img = screenshot(f"ts_race_post_inv_{int(time.time())}")
            if detect_screen(img) != "ts_climax_race":
                return "recovering"
        log("TS CLIMAX Race Day — using Master Cleat and racing")
        _use_cleat_for_race(is_ts_climax=True)
        # Only one race in TS Climax — tap Race directly
        tap(620, 1680)
        return "ts_climax_racing"

    if screen == "ts_climax_home":
        # TS Climax with Training/Rest buttons visible = training turn
        # Race turns force you into race selection directly
        _summer_whistle_used = False

        # Read inventory on first encounter
        if not _inventory_checked:
            read_inventory_from_training_items()
            time.sleep(1)
            img = screenshot(f"ts_post_inv_{int(time.time())}")
            if detect_screen(img) != "ts_climax_home":
                return "recovering"

        energy = get_energy_level(img)
        _detect_active_effects()
        # Re-screenshot after popup close
        img = screenshot(f"ts_post_effects_{int(time.time())}")
        if detect_screen(img) != "ts_climax_home":
            return "recovering"
        energy = get_energy_level(img)
        log(f"TS CLIMAX training turn — Energy: ~{energy}%")

        # 1. Megaphone first — maximise every training turn
        _use_megaphone_if_needed(is_ts_climax=True)

        # 2. Energy drink to top up without overcapping
        inventory = _shop_manager.inventory
        for key, gain in [("vita_65", 65), ("vita_40", 40), ("vita_20", 20)]:
            if inventory.get(key, 0) > 0 and energy + gain <= 100:
                log(f"TS CLIMAX — using {key} (+{gain}) at energy {energy}%")
                _use_training_items([key])
                if _shop_manager._inventory.get(key, 0) > 0:
                    _shop_manager._inventory[key] -= 1
                    if _shop_manager._inventory[key] <= 0:
                        del _shop_manager._inventory[key]
                _shop_manager.save_inventory()
                time.sleep(2)
                img = screenshot(f"ts_post_vita_{int(time.time())}")
                if detect_screen(img) == "ts_climax_home":
                    energy = get_energy_level(img)
                    log(f"TS CLIMAX — energy after vita: ~{energy}%")
                break

        # 3. Train
        tap(540, 1496)
        return "going_to_training"

    if screen == "career_home":
        return _handle_career_home(img)

    if screen == "tutorial_slide":
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
            _scenario.on_race_completed()
            log(f"Race warning popup — tapping OK (consecutive: {_scenario._consecutive_races})")
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
        _scenario.on_race_completed()
        log(f"Race confirm — tapping Race button (consecutive: {_scenario._consecutive_races})")
        race_btn = find_green_button(img, (1250, 1450))
        if race_btn:
            tap(race_btn[0], race_btn[1])
        else:
            tap(730, 1360)
        return "race_confirm"

    elif screen == "recreation_confirm":
        log("Recreation confirm detected — tapping Cancel (never waste turns on Recreation)")
        tap(270, 1260)
        return "recreation_cancel"

    elif screen == "infirmary_confirm":
        log("Infirmary confirm — tapping OK")
        ok = find_green_button(img, (1150, 1350))
        if ok:
            tap(ok[0], ok[1])
        else:
            tap(730, 1250)
        return "rest_confirm"

    elif screen == "rest_confirm":
        if _last_result == "rest":
            log("Rest confirm — tapping OK (intentional rest)")
            ok = find_green_button(img, (1150, 1350))
            if ok:
                tap(ok[0], ok[1])
            else:
                tap(730, 1250)
        else:
            log(f"Rest confirm — unexpected (last_result={_last_result}), tapping Cancel")
            tap(270, 1250)
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
        global _last_race_placement
        _last_race_placement = placement
        # TS Climax: retry if placed worse than 3rd (max 3 retries)
        global _ts_climax_retries
        if is_climax and placement > 3 and _ts_climax_retries < 3:
            _ts_climax_retries += 1
            log(f"TS Climax race — placed {placement}th, tapping Try Again (retry {_ts_climax_retries}/3)")
            tap(270, 1780)
            return "retry_race"
        if is_climax and placement <= 3:
            _ts_climax_retries = 0  # Reset on success
        if placement == 1:
            _needs_shop_visit = True
            log(f"Won race! Will visit shop next career_home")
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
        tap(540, 1792)
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

    elif screen == "race_live":
        log("Live race — tapping Skip to fast-forward")
        tap(778, 1862, delay=3.0)
        return "race_live_skip"

    elif screen == "concert_confirm":
        log("Concert playback prompt — pressing Back to dismiss")
        subprocess.run(["adb", "-s", DEVICE, "shell", "input", "keyevent", "KEYCODE_BACK"])
        return "concert_cancel"

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
        log("Shop refresh popup — tapping Shop to go buy items")
        tap(810, 1360)
        return "shop_popup_enter"

    elif screen == "shop":
        return handle_shop(img)

    elif screen == "event":
        return handle_event(img)

    elif screen == "event_choice_dimmed":
        log("Dimmed event choice — tapping choice box")
        tap(540, 1200)
        return "event_choice"

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
        tap(810, 1830)
        return "skill_confirm"

    elif screen == "skills_learned":
        log("Skills Learned popup — tapping Close")
        tap(540, 1200)
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
