"""Debug energy bar - analyze the actual bar fill using brightness."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import cv2
import numpy as np
from PIL import Image

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

# The energy bar inner area - exclude borders
# From the strip image: bar starts around x=335, ends around x=705
# Inner y is about 248-265 based on the rounded rect
y1, y2 = 248, 265
x1, x2 = 335, 700
roi = frame[y1:y2, x1:x2]
cv2.imwrite("screenshots/calibration/energy_inner.png", roi)

hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

# Column analysis
col_h = np.mean(h, axis=0)
col_s = np.mean(s, axis=0)
col_v = np.mean(v, axis=0)

# Print profile every 10 columns
print("Col analysis (every 10 cols):")
print(f"{'col':>4} {'H':>5} {'S':>5} {'V':>5} {'B':>5} {'G':>5} {'R':>5}")
for c in range(0, roi.shape[1], 10):
    b = roi[:, c, 0].mean()
    g = roi[:, c, 1].mean()
    r = roi[:, c, 2].mean()
    print(f"{c:4d} {col_h[c]:5.1f} {col_s[c]:5.1f} {col_v[c]:5.1f} {b:5.1f} {g:5.1f} {r:5.1f}")

# The bar fill color when energy is low should be orange/red
# When high it's green. Let's see if there's any color at all.
print(f"\nOverall: H={np.mean(h):.1f} S={np.mean(s):.1f} V={np.mean(v):.1f}")
print(f"S range: {np.min(s)}-{np.max(s)}")

# OCR the energy text if there's a number shown
from uma_trainer.perception.ocr import AppleVisionOCR
av = AppleVisionOCR()

# Try reading just the energy label area
label_roi = frame[230:280, 200:350]
pil = Image.fromarray(label_roi[:, :, ::-1])
results = av.recognize(pil)
print(f"\nEnergy label OCR: {results}")

# Try reading the whole energy row
row_roi = frame[230:280, 200:750]
pil2 = Image.fromarray(row_roi[:, :, ::-1])
results2 = av.recognize(pil2)
print(f"Energy row OCR: {results2}")
