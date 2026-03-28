"""Find actual UI element positions by saving horizontal strips."""

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
print(f"Image size: {img.size}")

out_dir = Path("screenshots/calibration")
out_dir.mkdir(parents=True, exist_ok=True)

# Save strips to find exact positions of key elements
strips = {
    "y0000_0200": (0, 0, 1080, 200),
    "y0200_0400": (0, 200, 1080, 400),
    "y0400_0600": (0, 400, 1080, 600),
    "y0600_0800": (0, 600, 1080, 800),
    "y0800_1000": (0, 800, 1080, 1000),
    "y1000_1200": (0, 1000, 1080, 1200),
    "y1200_1400": (0, 1200, 1080, 1400),
    "y1400_1600": (0, 1400, 1080, 1600),
    "y1600_1800": (0, 1600, 1080, 1800),
    "y1800_1920": (0, 1800, 1080, 1920),
}

for name, box in strips.items():
    strip = img.crop(box)
    strip.save(out_dir / f"strip_{name}.png")
    print(f"Saved strip_{name}.png")
