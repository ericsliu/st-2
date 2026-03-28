"""Find Next button - wider range."""

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

# Find the green Next button - wider scan
print("Green button scan (y=1700-1880):")
for y in range(1700, 1880, 5):
    greens = []
    for x in range(400, 1050, 10):
        b, g, r = frame[y, x]
        if g > 150 and r < 180 and b < 80:
            greens.append(x)
    if greens:
        print(f"  y={y}: green x={greens[0]}-{greens[-1]} (center ~{(greens[0]+greens[-1])//2})")
