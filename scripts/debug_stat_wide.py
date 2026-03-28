"""Save a wide crop of just the stat number row to measure exact positions."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
from PIL import Image

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")

out_dir = Path("screenshots/calibration")

# Full width strip at just the stat numbers (no /1200)
strip = img.crop((0, 1290, 1080, 1340))
strip.save(out_dir / "stat_row_full.png")
print("Saved stat_row_full.png (y=1290-1340)")

# Even tighter
strip2 = img.crop((0, 1295, 1080, 1330))
strip2.save(out_dir / "stat_row_tight.png")
print("Saved stat_row_tight.png (y=1295-1330)")
