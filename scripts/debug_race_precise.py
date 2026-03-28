"""Precisely find race entry boundaries."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import cv2
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

# OCR the detail line for each race - this has "Venue Turf/Dirt Xm (Category)"
# From images: detail line for race 0 is around y=1080-1115
# Need to find exact y for each race detail line

# Try narrow strips to find the detail text
for y in range(1070, 1160, 10):
    strip = frame[y:y+25, 380:1060]
    pil = Image.fromarray(strip[:, :, ::-1])
    results = av.recognize(pil)
    if results:
        texts = " ".join(t for t, _ in results)
        print(f"y={y}-{y+25}: {texts}")

print()
print("--- Race 1 ---")
for y in range(1280, 1380, 10):
    strip = frame[y:y+25, 380:1060]
    pil = Image.fromarray(strip[:, :, ::-1])
    results = av.recognize(pil)
    if results:
        texts = " ".join(t for t, _ in results)
        print(f"y={y}-{y+25}: {texts}")

# Also try full-width OCR of the race name area
print()
print("--- Race 0 name+detail OCR ---")
roi = frame[1060:1135, 50:1060]
pil = Image.fromarray(roi[:, :, ::-1])
cv2.imwrite("screenshots/calibration/race_slot0_detail.png", frame[1060:1135, 50:1060])
results = av.recognize(pil)
for t, c in results:
    print(f"  '{t}' ({c:.2f})")

print()
print("--- Race 1 name+detail OCR ---")
roi = frame[1280:1360, 50:1060]
pil = Image.fromarray(roi[:, :, ::-1])
cv2.imwrite("screenshots/calibration/race_slot1_detail.png", frame[1280:1360, 50:1060])
results = av.recognize(pil)
for t, c in results:
    print(f"  '{t}' ({c:.2f})")

# Save visible tap target areas
print()
print("--- Race button (bottom center) ---")
roi = frame[1640:1720, 350:720]
pil = Image.fromarray(roi[:, :, ::-1])
results = av.recognize(pil)
for t, c in results:
    print(f"  '{t}' ({c:.2f})")
