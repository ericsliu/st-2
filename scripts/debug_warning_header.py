"""Find green Warning header bar."""

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

# Scan for green header from y=600 to 750
print("Green bar scan (y=600-750):")
for y in range(600, 750, 3):
    greens = []
    for x in range(50, 1030, 20):
        b, g, r = frame[y, x]
        if g > 130 and r < 200 and b < 100 and g > r:
            greens.append(x)
    if len(greens) > 5:
        b, g, r = frame[y, 540]
        print(f"  y={y}: {len(greens)} green pixels, center R={r} G={g} B={b}")

# Also sample where the "Warning" text header should be
print("\nPixel samples around header area:")
for y in range(650, 720, 5):
    b, g, r = frame[y, 540]
    print(f"  (540, {y}): R={r} G={g} B={b}")
