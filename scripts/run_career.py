"""Run a full career until the Complete Career screen."""
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set up a fresh log file for this run BEFORE importing auto_turn
log_dir = Path("screenshots/run_log")
log_dir.mkdir(parents=True, exist_ok=True)
run_log = log_dir / f"career_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

import scripts.auto_turn as auto_turn
auto_turn.LOG = run_log

from scripts.auto_turn import run_one_turn, log

STOP_SCREENS = {"complete_career"}
MAX_TURNS = 500

log("\n" + "=" * 50)
log("Career run starting (stops at Complete Career)")
log("=" * 50)

for i in range(MAX_TURNS):
    print(f"\n{'='*40} Turn attempt {i+1} {'='*40}")
    try:
        result = run_one_turn(stop_before=STOP_SCREENS)
        print(f"Result: {result}")
        if result and result.startswith("stopped:"):
            log(f"Career run stopped: {result}")
            break
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    time.sleep(3)

log("Career run finished")
print(f"\nLog file: {run_log}")
