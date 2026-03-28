"""Sample pixels around the Race confirm button on the dialog."""

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

# Scan for the green Race button on the confirmation dialog
print("Scanning for green Race button (y=1340-1440):")
for y in range(1340, 1440, 5):
    greens = []
    for x in range(400, 900, 10):
        b, g, r = frame[y, x]
        if g > 160 and r < 160 and b < 80:
            greens.append(x)
    if greens:
        print(f"  y={y}: green pixels at x={greens[0]}-{greens[-1]} (center ~{(greens[0]+greens[-1])//2})")

# Also scan for Cancel button (white/light)
print("\nScanning for Cancel button (y=1340-1440):")
for y in range(1340, 1440, 5):
    whites = []
    for x in range(50, 400, 10):
        b, g, r = frame[y, x]
        if r > 220 and g > 220 and b > 220:
            whites.append(x)
    if whites:
        print(f"  y={y}: white pixels at x={whites[0]}-{whites[-1]} (center ~{(whites[0]+whites[-1])//2})")
