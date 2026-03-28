"""Automated turn executor. Runs one turn at a time with full logging."""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.career_helper import adb, screenshot, tap
from scripts.ocr_util import ocr_region, ocr_full_screen
from PIL import Image

LOG = Path("screenshots/run_log/run_senior_year.md")
DEVICE = "127.0.0.1:5555"

# State tracking to avoid loops
_last_result = None


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def px(img, x, y):
    return img.getpixel((x, y))[:3]


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

    # Popup screens (checked first — they overlay other screens)
    if has("Cancel") and has("OK"):
        if has("Rest") and has("recover energy"):
            return "rest_confirm"
        if has("enter this race"):
            return "race_confirm"
        return "warning_popup"

    # Pre-race screen: has "View Results" and "Race" buttons, plus strategy info
    if has("View Results") and has("Strategy"):
        return "pre_race"

    # Post-race standings: has "Try Again" and "Next"
    if has("Try Again") and has("Next"):
        return "post_race_standings"

    # Post-race result (animation done, shows WIN/placement, no nav buttons)
    if has("WIN") and not has("Race List") and not has("Back"):
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

    # Shop screen
    if has("Shop Coins") or (has("Shop") and has("Cost")):
        return "shop"

    # Training screen: has "Failure" indicator and stat tile labels
    if has("Failure") and has("Back"):
        return "training"

    # Career home: has the action buttons
    if has("Training") and has("Races") and has("Rest"):
        return "career_home"

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
    """Estimate energy percentage from the energy bar fill."""
    BAR_Y = 236
    BAR_X_START = 340
    BAR_X_END = 750
    green_count = 0
    total = 0
    for x in range(BAR_X_START, BAR_X_END, 5):
        r, g, b = px(img, x, BAR_Y)
        total += 1
        if g > 130 and g > r + 30 and b < 150:
            green_count += 1
    return int(100 * green_count / max(total, 1))


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


def handle_race_list(img):
    """Handle race list screen. Only enter race if top card has good aptitude."""
    # Top race card area (verified from pixel analysis): y=730-870
    CARD1_Y = (730, 870)

    race1_ok = has_green_aptitude_badge(img, *CARD1_Y)
    log(f"Race aptitude — Top card ok: {race1_ok}")

    if race1_ok:
        log("Top card has good aptitude — tapping Race at (540, 1620)")
        tap(540, 1620)
        return "race_enter"

    # Top card has bad aptitude — go back to train instead
    log("Top card has bad aptitude — pressing Back to train")
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


# Stat keywords to look for in effects text — positive effects
POSITIVE_KEYWORDS = [
    "speed", "stamina", "power", "guts", "wisdom", "wit",
    "energy", "motivation", "mood", "bond", "skill",
    "recover", "restore",
]

# Negative keywords — effects we want to avoid
NEGATIVE_KEYWORDS = [
    "decrease", "reduce", "lose", "down", "lower", "drain",
    "fatigue", "tired", "lazy",
]


def _score_effect_text(text):
    """Score an effect description. Higher = better."""
    text_lower = text.lower()
    score = 0
    for kw in POSITIVE_KEYWORDS:
        if kw in text_lower:
            score += 1
    for kw in NEGATIVE_KEYWORDS:
        if kw in text_lower:
            score -= 2
    return score


def handle_event(img):
    """Handle event screen: OCR name, tap Effects, read effects, pick best choice."""
    event_name = _ocr_event_name(img)
    log(f"Event: '{event_name}'")

    # Try to find and tap the Effects button to see choice effects
    effects_btn = _find_effects_button(img)
    if effects_btn:
        log(f"Found Effects button at {effects_btn} — tapping to preview")
        tap(effects_btn[0], effects_btn[1], delay=2)

        # Screenshot the effects preview
        effects_img = screenshot(f"effects_{int(time.time())}")
        effects_screen = detect_screen(effects_img)

        # OCR the effects panel
        try:
            from scripts.ocr_util import ocr_full_screen
            all_text = ocr_full_screen(effects_img)
            effects_lines = []
            for text, conf, y_pos in all_text:
                if conf > 0.3:
                    effects_lines.append((text, y_pos))
            log(f"Effects OCR ({len(effects_lines)} lines):")
            for text, y_pos in effects_lines:
                log(f"  y={y_pos:.0f}: {text}")

            # Try to identify choice sections and score them
            # Effects preview typically shows choice 1 effects then choice 2 effects
            # separated vertically
            choice_texts = {1: [], 2: []}
            current_choice = 0
            for text, y_pos in effects_lines:
                text_lower = text.lower()
                if "choice" in text_lower or "option" in text_lower:
                    if "1" in text or "top" in text_lower:
                        current_choice = 1
                    elif "2" in text or "bottom" in text_lower:
                        current_choice = 2
                if current_choice > 0:
                    choice_texts[current_choice].append(text)

            # If we couldn't parse by choice markers, split by position
            if not choice_texts[1] and not choice_texts[2] and effects_lines:
                mid_y = (effects_lines[0][1] + effects_lines[-1][1]) / 2
                for text, y_pos in effects_lines:
                    if y_pos < mid_y:
                        choice_texts[1].append(text)
                    else:
                        choice_texts[2].append(text)

            score1 = sum(_score_effect_text(t) for t in choice_texts[1])
            score2 = sum(_score_effect_text(t) for t in choice_texts[2])
            log(f"Choice scores — 1: {score1}, 2: {score2}")

            # Go back to event screen to make the choice
            press_back()
            time.sleep(1)

            if score2 > score1:
                log("Picking choice 2 (better effects)")
                tap(540, 1250)
            else:
                log("Picking choice 1 (default/better effects)")
                tap(540, 1120)
            return "event"

        except Exception as e:
            log(f"Effects OCR failed: {e} — going back and picking choice 1")
            press_back()
            time.sleep(1)
            tap(540, 1120)
            return "event"
    else:
        log("No Effects button found — picking choice 1")
        tap(540, 1120)
        return "event"


# Training tile tap positions (x, y) for each stat
TRAINING_TILES = {
    "Speed":   (158, 1520),
    "Stamina": (350, 1580),
    "Power":   (541, 1580),
    "Guts":    (731, 1580),
    "Wit":     (921, 1580),
}

# X positions of stat columns in the preview bar (for mapping gain numbers)
STAT_COLUMNS = {
    "Speed": 158, "Stamina": 313, "Power": 477,
    "Guts": 646, "Wit": 818, "Skill Pts": 969,
}


def _ocr_training_gains(img):
    """OCR the stat gain preview numbers from a training screen.

    Returns dict of stat_name -> gain_value for each visible "+N" indicator.
    """
    from scripts.ocr_util import ocr_image
    w, h = img.size
    # Crop the stat gain area (just above the stat bars)
    crop = img.crop((0, 1170, 1080, 1260))
    crop.save("/tmp/stat_gains_crop.png")
    raw = ocr_image("/tmp/stat_gains_crop.png")
    cw = crop.size[0]

    gains = {}
    for text, conf, bbox in raw:
        if conf < 0.25:
            continue
        # Extract the number from "+N" or "N" text
        clean = text.replace("+", "").replace("$", "").replace(",", "").strip()
        try:
            val = int(clean)
        except ValueError:
            continue
        # Find which stat column this gain belongs to by x position
        center_x = (bbox[0] + bbox[2] / 2) * cw
        best_stat = None
        best_dist = 999
        for stat, col_x in STAT_COLUMNS.items():
            dist = abs(center_x - col_x)
            if dist < best_dist:
                best_dist = dist
                best_stat = stat
        if best_stat and best_dist < 120:
            gains[best_stat] = val
    return gains


def handle_training():
    """Preview all 5 training tiles and pick the best one."""
    log("Training — previewing all tiles")

    tile_scores = {}
    for tile_name, (tx, ty) in TRAINING_TILES.items():
        tap(tx, ty, delay=1)
        img = screenshot(f"train_preview_{tile_name.lower()}_{int(time.time())}")
        gains = _ocr_training_gains(img)

        # Score: sum of all stat gains (simple for now)
        total = sum(gains.values())
        tile_scores[tile_name] = (total, gains)
        gains_str = ", ".join(f"{k}+{v}" for k, v in sorted(gains.items()))
        log(f"  {tile_name}: total={total} ({gains_str})")

    # Pick the tile with the highest total gains
    best_tile = max(tile_scores, key=lambda t: tile_scores[t][0])
    best_total, best_gains = tile_scores[best_tile]
    log(f"Best: {best_tile} (total={best_total})")

    # Tap the best tile and confirm
    bx, by = TRAINING_TILES[best_tile]
    tap(bx, by, delay=1)
    tap(bx, by)
    return "training"


def run_one_turn():
    """Execute one game action. Returns screen type for logging."""
    global _last_result

    img = screenshot(f"auto_{int(time.time())}")
    screen = detect_screen(img)
    log(f"Detected: {screen}")

    if screen == "career_home":
        energy = get_energy_level(img)
        log(f"Energy: ~{energy}%")

        if energy < 25:
            log("Low energy — resting")
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

        # If we just came back from race_list with no good races, train instead
        if _last_result == "race_back":
            log("No good races available — going to Training instead")
            tap(540, 1480)
            return "going_to_training"

        # Try racing
        log("Tapping Races")
        tap(910, 1680)
        return "going_to_races"

    elif screen == "warning_popup":
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
        log("Race confirm — tapping Race button")
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
        log("Pre-race — tapping View Results (skip animation)")
        tap(380, 1780)
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
        log("Standings — tapping Next")
        next_btn = find_green_button(img, (1700, 1850), (500, 1000))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(750, 1780)
        return "standings_next"

    elif screen == "fan_class":
        log("Fan class — tapping Next")
        next_btn = find_green_button(img, (1750, 1870), (600, 1000))
        if next_btn:
            tap(next_btn[0], next_btn[1])
        else:
            tap(810, 1810)
        return "fan_next"

    elif screen == "post_race_result":
        log("Post-race result — tapping to continue")
        tap(540, 960)
        return "post_race_result"

    elif screen == "inspiration":
        log("Inspiration screen — tapping GO!")
        tap(540, 1530)
        return "inspiration"

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
        return handle_training()

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
