"""Semi-autonomous career runner — runs turns, logs everything."""
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.career_helper import adb, screenshot, tap

LOG_FILE = Path("screenshots/run_log/auto_run.md")


def log(msg: str):
    """Append a timestamped line to the run log and print it."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_pixel(img, x, y):
    return img.getpixel((x, y))[:3]


def check_anchor(img, x, y, r_range, g_range, b_range):
    """Check if pixel at (x,y) falls within the given RGB ranges."""
    r, g, b = get_pixel(img, x, y)
    return (r_range[0] <= r <= r_range[1] and
            g_range[0] <= g <= g_range[1] and
            b_range[0] <= b <= b_range[1])


def detect_screen_anchors(img):
    """Detect screen using pixel anchors. Returns (screen_name, match_ratio)."""
    from uma_trainer.perception.regions import SCREEN_ANCHORS
    best_screen = "unknown"
    best_ratio = 0.0

    for screen_name, anchors in SCREEN_ANCHORS.items():
        hits = 0
        for anchor in anchors:
            x, y = anchor["pos"]
            if x >= img.size[0] or y >= img.size[1]:
                continue
            r, g, b = get_pixel(img, x, y)
            rr = anchor["r"]
            gr = anchor["g"]
            br = anchor["b"]
            if rr[0] <= r <= rr[1] and gr[0] <= g <= gr[1] and br[0] <= b <= br[1]:
                hits += 1
        ratio = hits / len(anchors) if anchors else 0
        if ratio > best_ratio:
            best_ratio = ratio
            best_screen = screen_name
    return best_screen, best_ratio


def read_energy(img):
    """Read energy percentage from the energy bar."""
    green_count = 0
    total = 0
    for x in range(190, 520, 5):
        r, g, b = get_pixel(img, x, 250)
        total += 1
        if g > 150 and r < 150:
            green_count += 1
    return int(100 * green_count / max(total, 1))


def detect_mood(img):
    """Detect mood from the mood icon area."""
    # Mood icon is at roughly (950-1020, 260-330)
    # Check for arrow color
    r, g, b = get_pixel(img, 985, 290)
    if g > 180 and r < 150:
        return "GREAT"
    if g > 150 and r > 150:
        return "GOOD"
    if b > 150 and r < 100:
        return "NORMAL"
    if r > 180 and g < 100:
        return "BAD"
    return "UNKNOWN"


def read_turn_count(img):
    """Try to read the turn count from the screenshot."""
    # Turn count is displayed prominently at top-left
    # We'll use OCR for this
    try:
        from uma_trainer.perception.ocr import OCREngine
        ocr = OCREngine()
        # Crop the turn area (roughly 20-130, 100-210)
        turn_roi = img.crop((20, 100, 170, 210))
        text = ocr.read_text(turn_roi)
        import re
        match = re.search(r'(\d+)', text)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return -1


def is_career_home(img):
    """Check if we're on the career home / turn action screen."""
    # Training button is a big blue bubble at ~(440-600, 1500-1600)
    # Rest button at top-left of action grid
    # Check for the action buttons row
    has_training = False
    for x in range(350, 600, 10):
        r, g, b = get_pixel(img, x, 1540)
        if 30 < b < 230 and b > r:
            has_training = True
            break
    return has_training


def is_event_screen(img):
    """Check for event choice buttons."""
    white_bands = 0
    for y in range(1000, 1500, 50):
        whites = sum(1 for x in range(100, 900, 30)
                     if all(c > 230 for c in get_pixel(img, x, y)))
        if whites > 10:
            white_bands += 1
    return white_bands >= 2


def is_race_list(img):
    """Check for race list entries."""
    for y in range(900, 1400, 50):
        border_pixels = sum(1 for x in range(50, 1030, 20)
                           if all(c > 200 for c in get_pixel(img, x, y)))
        if border_pixels > 20:
            return True
    return False


def check_for_tap_prompt(img):
    """Check if there's a 'TAP' prompt on screen (post-race results etc)."""
    # TAP prompts usually have pulsing text near bottom center
    # Check for mostly dark screen with light text
    bottom_brightness = 0
    for x in range(400, 700, 20):
        r, g, b = get_pixel(img, x, 1700)
        bottom_brightness += r + g + b
    return bottom_brightness < 3000  # Very dark = might be TAP overlay


def run_turn():
    """Execute one turn of the career loop. Returns False to stop."""
    img = screenshot(f"turn_{int(time.time())}")
    screen, ratio = detect_screen_anchors(img)

    log(f"Screen: {screen} (ratio={ratio:.2f})")

    if screen == "training" or is_career_home(img):
        energy = read_energy(img)
        mood = detect_mood(img)
        log(f"Career home: energy={energy}%, mood={mood}")

        # Need to race for Result Pts — check if races are available
        # With 0/300 pts and 24 turns, we MUST race aggressively
        if energy < 20:
            log("Energy too low, resting")
            tap(187, 1525)  # Rest button
            time.sleep(2)
            # Confirm rest
            tap(730, 1385)
            return True

        # Tap Races button
        log("Tapping Races button")
        tap(900, 1560)  # Races button
        return True

    elif screen == "race_entry" or is_race_list(img):
        log("On race list — need to evaluate races")
        # For now, take a screenshot and let me analyze
        img.save("screenshots/run_log/race_list_current.png")
        log("Saved race list screenshot for analysis")
        # We need to check for yellow vs white text on each race
        # and pick the best one
        return "race_list"

    elif screen == "pre_race":
        log("Pre-race screen: tapping View Results")
        tap(380, 1760)  # View Results button
        return True

    elif screen == "post_race":
        log("Post-race screen: tapping Next")
        tap(765, 1760)
        return True

    elif screen == "warning_popup":
        log("Warning popup detected — tapping OK (proceeding)")
        tap(775, 1245)  # OK button
        return True

    elif screen == "event" or is_event_screen(img):
        log("Event screen — picking choice 1 (top)")
        # First choice is usually safest
        tap(540, 1100)
        return True

    elif screen == "skill_shop":
        log("Skill shop — exiting")
        tap(60, 60)  # Back button
        return True

    elif screen == "loading":
        log("Loading screen — waiting")
        time.sleep(3)
        return True

    elif check_for_tap_prompt(img):
        log("Possible TAP prompt — tapping center")
        tap(540, 1675)
        return True

    else:
        log(f"Unknown screen ({screen}), saving screenshot")
        img.save(f"screenshots/run_log/unknown_{int(time.time())}.png")
        # Try tapping common dismiss positions
        tap(540, 960)
        return True


def main():
    log("=" * 50)
    log("Auto career run starting")
    log("=" * 50)

    consecutive_unknown = 0
    turn_count = 0

    while True:
        try:
            result = run_turn()
            if result == "race_list":
                log("PAUSED: Race list needs manual analysis")
                log("Run analyze_race_list() to continue")
                break
            if result is False:
                break
            turn_count += 1
            if turn_count > 200:
                log("Safety limit: 200 iterations reached")
                break
            time.sleep(1.5)  # Wait between actions
        except KeyboardInterrupt:
            log("Interrupted by user")
            break
        except Exception as e:
            log(f"ERROR: {e}")
            consecutive_unknown += 1
            if consecutive_unknown > 5:
                log("Too many errors, stopping")
                break
            time.sleep(2)


if __name__ == "__main__":
    main()
