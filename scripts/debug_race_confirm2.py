"""Find exact button positions on race confirmation."""

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

# Search for the green "Race" button on the dialog
# Green in this game is typically R≈97-121 G≈189-206 B≈12-36
print("Searching for green Race button pixels (y=1300-1450):")
for y in range(1300, 1450, 5):
    for x in range(400, 1000, 20):
        b, g, r = frame[y, x]
        if g > 150 and r < 150 and b < 60:  # greenish
            print(f"  GREEN at ({x}, {y}): R={r} G={g} B={b}")

# Also check for the Cancel button (white/grey with border)
print("\nSearching for Cancel/Race text buttons (y=1300-1450):")
for y in range(1300, 1450, 10):
    row_colors = []
    for x in range(100, 1000, 50):
        b, g, r = frame[y, x]
        row_colors.append(f"({x}:R{r}G{g}B{b})")
    print(f"  y={y}: {' '.join(row_colors)}")
