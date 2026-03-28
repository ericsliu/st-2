"""Find the Close button on result pts popup."""

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

# Look for the Close button - it's white/light grey with border
print("Scanning for Close button area (y=900-1100):")
for y in range(900, 1100, 5):
    whites = []
    for x in range(300, 750, 10):
        b, g, r = frame[y, x]
        if r > 230 and g > 230 and b > 230:
            whites.append(x)
    if len(whites) > 5:
        print(f"  y={y}: white band x={whites[0]}-{whites[-1]} (center ~{(whites[0]+whites[-1])//2})")
