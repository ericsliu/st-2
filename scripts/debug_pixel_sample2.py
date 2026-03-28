"""Sample more pixel colors for race list screen anchors."""

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

# Race button green area - sample around (540, 1680)
print("Green Race button area:")
for y in range(1670, 1700, 5):
    for x in range(480, 600, 20):
        b, g, r = frame[y, x]
        print(f"  ({x:4d}, {y:4d}): R={r:3d} G={g:3d} B={b:3d}")

# "Predictions" text/button area at left
print("\nPredictions button area:")
for y in range(1740, 1780, 5):
    for x in range(100, 240, 20):
        b, g, r = frame[y, x]
        print(f"  ({x:4d}, {y:4d}): R={r:3d} G={g:3d} B={b:3d}")

# "Agenda" button area at right
print("\nAgenda button area:")
for y in range(1740, 1780, 5):
    for x in range(830, 970, 20):
        b, g, r = frame[y, x]
        print(f"  ({x:4d}, {y:4d}): R={r:3d} G={g:3d} B={b:3d}")

# Back button bottom-left
print("\nBack button area:")
for y in range(1845, 1880, 5):
    for x in range(20, 120, 20):
        b, g, r = frame[y, x]
        print(f"  ({x:4d}, {y:4d}): R={r:3d} G={g:3d} B={b:3d}")

# Header "Race List" dark bar
print("\nHeader bar:")
for y in range(5, 35, 5):
    for x in range(50, 200, 30):
        b, g, r = frame[y, x]
        print(f"  ({x:4d}, {y:4d}): R={r:3d} G={g:3d} B={b:3d}")
