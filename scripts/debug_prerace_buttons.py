"""Find View Results and Race button positions on pre-race screen."""

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

# Scan bottom area for buttons
print("Scanning for buttons (y=1750-1900):")
for y in range(1750, 1900, 5):
    row = []
    for x in range(50, 1050, 20):
        b, g, r = frame[y, x]
        if g > 160 and r < 160 and b < 80:
            row.append(f"G{x}")
        elif r > 220 and g > 220 and b > 220:
            row.append(f"W{x}")
    if row:
        print(f"  y={y}: {' '.join(row)}")

# Sample specific points
print("\nPixel samples at likely button positions:")
for x, y, label in [
    (130, 1830, "View Results area"),
    (350, 1830, "Race button area"),
    (540, 1830, "Menu icon area"),
    (130, 1850, "View Results lower"),
    (350, 1850, "Race button lower"),
]:
    b, g, r = frame[y, x]
    print(f"  ({x:4d}, {y:4d}): R={r:3d} G={g:3d} B={b:3d}  -- {label}")
