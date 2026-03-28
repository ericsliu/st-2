"""Find Next button position on race results screen."""

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

# Find the green Next button
print("Green button scan (y=1800-1890):")
for y in range(1800, 1890, 5):
    greens = []
    for x in range(400, 1000, 10):
        b, g, r = frame[y, x]
        if g > 160 and r < 170 and b < 80:
            greens.append(x)
    if greens:
        print(f"  y={y}: green x={greens[0]}-{greens[-1]} (center ~{(greens[0]+greens[-1])//2})")
