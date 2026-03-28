"""Find OK button and header bar on warning popup - wider scan."""

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

# Wider green scan for OK button
print("Green button scan (y=1050-1300):")
for y in range(1050, 1300, 5):
    greens = []
    for x in range(500, 1050, 10):
        b, g, r = frame[y, x]
        if g > 140 and r < 180 and b < 100:
            greens.append(x)
    if greens:
        print(f"  y={y}: green x={greens[0]}-{greens[-1]} (center ~{(greens[0]+greens[-1])//2})")

# Green header bar - wider scan
print("\nGreen header bar scan (y=690-780):")
for y in range(690, 780, 5):
    greens = []
    for x in range(50, 1030, 20):
        b, g, r = frame[y, x]
        if g > 140 and r < 180 and b < 100:
            greens.append(x)
    if greens:
        print(f"  y={y}: green x={greens[0]}-{greens[-1]} ({len(greens)} hits)")

# Sample specific pixel at the center of where OK should be
print("\nPixel samples around OK button:")
for x, y in [(700, 1120), (700, 1140), (700, 1160), (750, 1140), (800, 1140)]:
    b, g, r = frame[y, x]
    print(f"  ({x}, {y}): R={r} G={g} B={b}")
