"""Execute a single action and log it. Run repeatedly for each turn."""
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.career_helper import adb, screenshot, tap

LOG = Path("screenshots/run_log/run_senior_year.md")

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

action = sys.argv[1] if len(sys.argv) > 1 else "screenshot"

if action == "screenshot":
    img = screenshot(f"turn_{int(time.time())}")
    print(f"Saved. Size: {img.size}")

elif action == "races":
    log("Tapping Races button")
    tap(900, 1560)

elif action == "training":
    log("Tapping Training button")
    tap(470, 1540)

elif action == "rest":
    log("Tapping Rest button")
    tap(187, 1525)
    time.sleep(2)
    log("Confirming rest")
    tap(730, 1385)

elif action == "view_results":
    log("Tapping View Results")
    tap(380, 1760)

elif action == "next":
    log("Tapping Next")
    tap(765, 1760)

elif action == "tap_center":
    log("Tapping center")
    tap(540, 960)

elif action == "tap_bottom":
    log("Tapping bottom center (TAP prompt)")
    tap(540, 1675)

elif action == "ok":
    log("Tapping OK on popup")
    tap(775, 1245)

elif action == "cancel":
    log("Tapping Cancel on popup")
    tap(285, 1245)

elif action == "back":
    log("Tapping Back")
    adb("shell input keyevent 4")
    time.sleep(1.5)

elif action == "choice1":
    log("Picking event choice 1")
    tap(540, 1100)

elif action == "choice2":
    log("Picking event choice 2")
    tap(540, 1300)

elif action == "skills":
    log("Tapping Skills button")
    tap(920, 1430)

elif action == "shop":
    log("Tapping Shop button")
    tap(620, 1660)

elif action.startswith("tap:"):
    coords = action[4:].split(",")
    x, y = int(coords[0]), int(coords[1])
    log(f"Tapping ({x}, {y})")
    tap(x, y)

else:
    print(f"Unknown action: {action}")
    print("Actions: screenshot, races, training, rest, view_results, next,")
    print("         tap_center, tap_bottom, ok, cancel, back, choice1, choice2,")
    print("         skills, shop, tap:x,y")
