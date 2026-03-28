"""Capture current screen and sample key pixels to identify state."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import numpy as np
from PIL import Image

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

print(f"Screen size: {frame.shape}")

points = [
    (540, 100, "Top header"),
    (540, 960, "Center"),
    (540, 1675, "Bottom btn area"),
    (540, 1850, "Very bottom"),
    (100, 115, "Turn counter area"),
    (540, 400, "Upper mid"),
    (540, 1400, "Lower mid"),
]
for x, y, label in points:
    b, g, r = frame[y, x]
    print(f"  ({x:4d}, {y:4d}): R={r:3d} G={g:3d} B={b:3d}  -- {label}")

img.save("screenshots/current_screen.png")
print("\nSaved screenshots/current_screen.png")
