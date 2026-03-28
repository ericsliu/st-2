"""Find button positions on the Warning popup."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import numpy as np
from PIL import Image
from uma_trainer.perception.ocr import AppleVisionOCR

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

av = AppleVisionOCR()

# OCR the warning popup
for y1, y2, label in [
    (700, 800, "Warning header"),
    (800, 950, "Warning text"),
    (950, 1100, "Warning detail"),
    (1100, 1200, "Buttons"),
]:
    roi = frame[y1:y2, 50:1030]
    pil = Image.fromarray(roi[:, :, ::-1])
    results = av.recognize(pil)
    print(f"y={y1}-{y2} ({label}):")
    for t, c in results:
        print(f"  '{t}' ({c:.2f})")

# Find green OK button and white Cancel button
print("\nGreen OK button scan (y=1100-1200):")
for y in range(1100, 1200, 5):
    greens = []
    for x in range(400, 900, 10):
        b, g, r = frame[y, x]
        if g > 160 and r < 170 and b < 80:
            greens.append(x)
    if greens:
        print(f"  y={y}: green x={greens[0]}-{greens[-1]} (center ~{(greens[0]+greens[-1])//2})")

print("\nWhite Cancel button scan (y=1100-1200):")
for y in range(1100, 1200, 5):
    whites = []
    for x in range(50, 500, 10):
        b, g, r = frame[y, x]
        if r > 230 and g > 230 and b > 230:
            whites.append(x)
    if len(whites) > 5:
        print(f"  y={y}: white x={whites[0]}-{whites[-1]} (center ~{(whites[0]+whites[-1])//2})")

# Sample the green Warning header bar
print("\nGreen header bar scan (y=740-780):")
for y in range(740, 780, 5):
    greens = []
    for x in range(50, 1030, 20):
        b, g, r = frame[y, x]
        if g > 160 and r < 170 and b < 80:
            greens.append(x)
    if greens:
        print(f"  y={y}: green x={greens[0]}-{greens[-1]}")
