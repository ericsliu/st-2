"""Sample pixel colors at specific coordinates for anchor calibration."""

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

# Sample points for race list screen
points = [
    (540, 1780, "Race button center (green)"),
    (170, 1780, "Predictions button"),
    (900, 1780, "Agenda button"),
    (540, 1860, "Skip button area"),
    (60, 1860, "Back button"),
    (540, 20, "Header area"),
    (200, 20, "Race List text area"),
    (540, 1680, "Race button top"),
    # Also sample the green Race button specifically
    (540, 1750, "Race btn upper"),
    (540, 1810, "Race btn lower"),
]

print(f"Frame: {frame.shape}")
print()
for x, y, label in points:
    if y < frame.shape[0] and x < frame.shape[1]:
        b, g, r = frame[y, x]
        print(f"({x:4d}, {y:4d}) R={r:3d} G={g:3d} B={b:3d}  — {label}")
    else:
        print(f"({x:4d}, {y:4d}) OUT OF BOUNDS — {label}")

# Also sample the green Race button in a 5x5 grid around center
print("\nRace button 5x5 grid around (540, 1780):")
for dy in range(-10, 15, 5):
    for dx in range(-10, 15, 5):
        px, py = 540 + dx, 1780 + dy
        b, g, r = frame[py, px]
        print(f"  ({px}, {py}): R={r:3d} G={g:3d} B={b:3d}")
