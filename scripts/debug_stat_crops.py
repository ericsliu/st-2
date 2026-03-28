"""Save cropped stat regions to check alignment."""

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

from uma_trainer.perception.regions import TURN_ACTION_REGIONS

regions_to_check = [
    "stat_speed", "stat_stamina", "stat_power", "stat_guts", "stat_wit",
    "skill_pts", "turn_counter", "energy_bar", "mood_label",
    "period_text", "result_pts",
]

for name in regions_to_check:
    region = TURN_ACTION_REGIONS.get(name)
    if region:
        x1, y1, x2, y2 = region
        crop = img.crop((x1, y1, x2, y2))
        crop.save(out_dir / f"crop_{name}.png")
        print(f"{name:20s} ({x1},{y1})-({x2},{y2}) = {x2-x1}x{y2-y1}")
