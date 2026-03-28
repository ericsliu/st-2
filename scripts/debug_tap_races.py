"""Tap Races button and capture what appears."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import time
import numpy as np
from PIL import Image

def capture():
    result = subprocess.run(
        ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10,
    )
    return Image.open(io.BytesIO(result.stdout)).convert("RGB")

def tap(x, y):
    subprocess.run(
        ["adb", "-s", "127.0.0.1:5555", "shell", "input", "tap", str(x), str(y)],
        timeout=5,
    )

print("Tapping Races button at (890, 1665)...")
tap(890, 1665)

for delay in [1.0, 2.0, 3.0]:
    time.sleep(delay)
    img = capture()
    img.save(f"screenshots/after_races_tap_{int(delay)}s.png")
    frame = np.array(img)[:, :, ::-1]
    print(f"\nAfter {delay}s:")
    for x, y, label in [
        (540, 100, "Top"), (540, 960, "Center"), (540, 1675, "Bottom"),
        (100, 115, "Turn counter"), (540, 400, "Upper"),
    ]:
        b, g, r = frame[y, x]
        print(f"  ({x}, {y}): R={r} G={g} B={b} -- {label}")
