"""Find Cancel and OK button centers on warning popup."""

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
frame = np.array(img)[:, :, ::-1]

# Cancel button - white with grey border
print("Cancel button (white, y=1100-1300):")
for y in range(1100, 1300, 5):
    whites = []
    for x in range(30, 550, 10):
        b, g, r = frame[y, x]
        if r > 230 and g > 230 and b > 230:
            whites.append(x)
    if len(whites) > 5:
        print(f"  y={y}: white x={whites[0]}-{whites[-1]} (center ~{(whites[0]+whites[-1])//2})")

# OK button - green
print("\nOK button (green, y=1100-1300):")
for y in range(1100, 1300, 5):
    greens = []
    for x in range(500, 1050, 10):
        b, g, r = frame[y, x]
        if g > 170 and r < 180 and b < 50:
            greens.append(x)
    if greens:
        print(f"  y={y}: green x={greens[0]}-{greens[-1]} (center ~{(greens[0]+greens[-1])//2})")
