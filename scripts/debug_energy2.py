"""Debug energy bar - capture wider area and analyze."""

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

# Save a wide strip around the energy area (y=200-300)
strip = frame[200:310, 0:1080]
cv2.imwrite("screenshots/calibration/energy_strip.png", strip)
print("Saved energy_strip.png (y=200-310, full width)")

# Save just the bar area more tightly
# The "Energy" label is at left, bar extends to right
# Let's try different y ranges
for y_start, y_end, label in [(240, 275, "current"), (245, 270, "tight"), (250, 268, "inner")]:
    roi = frame[y_start:y_end, 260:750]
    cv2.imwrite(f"screenshots/calibration/energy_{label}.png", roi)

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    col_sat = np.mean(sat, axis=0)
    filled = int(np.sum(col_sat > 50))
    pct = int(round(filled / (750 - 260) * 100))
    print(f"y={y_start}-{y_end}: mean_sat={np.mean(sat):.1f}, filled(>50)={filled}/{750-260}={pct}%")
