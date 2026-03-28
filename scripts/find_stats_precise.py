"""Find precise stat row coordinates."""

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

# Narrow strips around the stat row and other key areas
strips = {
    # Header area - period and turn
    "header_0_60": (0, 0, 1080, 60),
    "header_60_120": (0, 60, 1080, 120),
    "header_120_180": (0, 120, 1080, 180),
    "header_180_250": (0, 180, 1080, 250),
    # Energy/mood area
    "energy_250_330": (0, 250, 1080, 330),
    "energy_330_400": (0, 330, 1080, 400),
    # Around stat row
    "stats_1180_1240": (0, 1180, 1080, 1240),
    "stats_1240_1300": (0, 1240, 1080, 1300),
    "stats_1300_1360": (0, 1300, 1080, 1360),
    "stats_1360_1420": (0, 1360, 1080, 1420),
    # Button rows
    "btns_1420_1520": (0, 1420, 1080, 1520),
    "btns_1520_1620": (0, 1520, 1080, 1620),
    "btns_1620_1720": (0, 1620, 1080, 1720),
    "btns_1720_1820": (0, 1720, 1080, 1820),
}

for name, box in strips.items():
    strip = img.crop(box)
    strip.save(out_dir / f"precise_{name}.png")
    print(f"Saved precise_{name}.png")
