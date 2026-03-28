"""Debug energy bar detection."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import cv2
import numpy as np
from PIL import Image

from uma_trainer.perception.regions import TURN_ACTION_REGIONS

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

region = TURN_ACTION_REGIONS["energy_bar"]
x1, y1, x2, y2 = region
print(f"Energy bar region: ({x1}, {y1}, {x2}, {y2})")

roi = frame[y1:y2, x1:x2]
print(f"ROI shape: {roi.shape}")

# Save the energy bar region
cv2.imwrite("screenshots/calibration/energy_bar.png", roi)
print("Saved energy_bar.png")

# Analyze HSV
hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
sat = hsv[:, :, 1]
val = hsv[:, :, 2]

# Column-wise saturation
col_sat = np.mean(sat, axis=0)
bar_width = x2 - x1
filled = int(np.sum(col_sat > 50))
energy_sat = int(round(filled / bar_width * 100))
print(f"Saturation-based: {filled}/{bar_width} cols > 50 sat = {energy_sat}%")

# Try different thresholds
for thresh in [20, 30, 40, 50, 60, 80, 100]:
    filled = int(np.sum(col_sat > thresh))
    pct = int(round(filled / bar_width * 100))
    print(f"  sat_thresh={thresh}: {filled}/{bar_width} = {pct}%")

# Show column saturation profile
print(f"\nFirst 20 col_sat: {col_sat[:20].astype(int)}")
print(f"Last 20 col_sat: {col_sat[-20:].astype(int)}")
print(f"Mean sat: {np.mean(sat):.1f}, max sat: {np.max(sat)}")
print(f"Mean val: {np.mean(val):.1f}, max val: {np.max(val)}")

# Also check raw BGR values
print(f"\nMean BGR per column (first 10):")
for c in range(min(10, roi.shape[1])):
    col = roi[:, c, :]
    print(f"  col {c}: B={col[:, 0].mean():.0f} G={col[:, 1].mean():.0f} R={col[:, 2].mean():.0f}")
