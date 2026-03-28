"""Debug race confirmation dialog pixel positions."""

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

# OCR the bottom part of the dialog to find button positions
for y1, y2, label in [
    (1100, 1200, "enter_race_text"),
    (1200, 1300, "buttons"),
    (1300, 1400, "below_buttons"),
]:
    roi = frame[y1:y2, 50:1030]
    pil = Image.fromarray(roi[:, :, ::-1])
    results = av.recognize(pil)
    print(f"y={y1}-{y2} ({label}):")
    for t, c in results:
        print(f"  '{t}' ({c:.2f})")

# Sample pixel colors for button areas
print("\nPixel samples for Race confirm dialog:")
points = [
    (300, 1250, "Cancel button area"),
    (760, 1250, "Race button area"),
    (540, 1180, "Enter race? text"),
    (540, 510, "Race Details header"),
    (100, 400, "Dark overlay corner"),
]
for x, y, label in points:
    b, g, r = frame[y, x]
    print(f"  ({x:4d}, {y:4d}): R={r:3d} G={g:3d} B={b:3d}  — {label}")

# Save the dialog area
cv2.imwrite("screenshots/calibration/race_confirm.png", frame[400:1350, 30:1050])
print("\nSaved race_confirm.png")
