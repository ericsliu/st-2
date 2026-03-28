"""Check exact pixel values on saved warning screenshot."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

img = Image.open("screenshots/after_races_tap_1s.png").convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

# Find the green header bar
print("Looking for green header bar:")
for y in range(550, 700, 5):
    b, g, r = frame[y, 540]
    if g > 140 and r < 180 and b < 100 and g > r:
        print(f"  (540, {y}): R={r} G={g} B={b} -- GREEN")

# Find the green OK button
print("\nLooking for green OK button:")
for y in range(1100, 1400, 5):
    b, g, r = frame[y, 775]
    if g > 140 and r < 180 and b < 100 and g > r:
        print(f"  (775, {y}): R={r} G={g} B={b} -- GREEN")

# Sample at the anchor positions
print("\nAnchor position samples:")
for x, y, label in [
    (540, 630, "header anchor"),
    (540, 960, "body anchor"),
    (775, 1245, "OK btn anchor"),
]:
    b, g, r = frame[y, x]
    print(f"  ({x}, {y}): R={r} G={g} B={b} -- {label}")
