"""Find the Close button - scan y=1200-1400."""

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

for y1, y2, label in [
    (1200, 1300, "close area"),
    (1300, 1400, "below close"),
]:
    roi = frame[y1:y2, 200:850]
    pil = Image.fromarray(roi[:, :, ::-1])
    results = av.recognize(pil)
    print(f"y={y1}-{y2} ({label}):")
    for t, c in results:
        print(f"  '{t}' ({c:.2f})")

# Also scan for button-shaped white regions
print("\nWhite button scan (y=1200-1400):")
for y in range(1200, 1400, 5):
    whites = []
    for x in range(300, 750, 10):
        b, g, r = frame[y, x]
        if r > 220 and g > 220 and b > 220:
            whites.append(x)
    if len(whites) > 5:
        print(f"  y={y}: white x={whites[0]}-{whites[-1]} ({len(whites)} px)")
