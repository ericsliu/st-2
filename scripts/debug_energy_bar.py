"""Debug: analyze energy bar HSV values across its width.

Shows per-column H/S/V to distinguish:
- Bright rainbow (current energy)
- Faded gradient (energy cost preview)
- Gray (missing energy)
- Recovery preview (Wit training, bright yellow)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from uma_trainer.perception.regions import STAT_SELECTION_REGIONS

src = sys.argv[1] if len(sys.argv) > 1 else "runs/hishi_amazon_20260327/screen_006_wit_energy.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

region = STAT_SELECTION_REGIONS["energy_bar"]
x1, y1, x2, y2 = region
roi = frame[y1:y2, x1:x2]
hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

bar_width = x2 - x1
print(f"Energy bar region: {region}, ROI shape={roi.shape}")
print(f"Bar width: {bar_width} cols\n")

# Per-column averages
col_h = np.mean(hsv[:, :, 0], axis=0)
col_s = np.mean(hsv[:, :, 1], axis=0)
col_v = np.mean(hsv[:, :, 2], axis=0)

# Print every 5th column
print("Col   H     S     V")
print("-" * 35)
for i in range(0, bar_width, 5):
    print(f"{i:3d}  {col_h[i]:5.1f} {col_s[i]:5.1f} {col_v[i]:5.1f}")

print(f"\nSaturation range: {col_s.min():.1f} - {col_s.max():.1f}")
print(f"Value range: {col_v.min():.1f} - {col_v.max():.1f}")

# Zone detection
bright = int(np.sum((col_s > 100) & (col_v > 200)))
faded = int(np.sum((col_s > 100) & (col_v <= 200)))
recovery = int(np.sum((col_s > 20) & (col_s <= 100)))
gray = int(np.sum(col_s <= 20))

print(f"\nBright (S>100, V>200): {bright} cols")
print(f"Faded  (S>100, V<=200): {faded} cols")
print(f"Recovery (20<S<=100): {recovery} cols")
print(f"Gray   (S<=20): {gray} cols")
print(f"Current energy (bright+faded): {(bright+faded)/bar_width*100:.0f}%")
print(f"Old reading (S>50): {int(np.sum(col_s > 50))/bar_width*100:.0f}%")
print(f"New reading (S>100): {int(np.sum(col_s > 100))/bar_width*100:.0f}%")

# Save 4x enlarged ROI
out = Path(src).parent / "energy_bar_debug.png"
roi_4x = cv2.resize(roi, (roi.shape[1] * 4, roi.shape[0] * 4), interpolation=cv2.INTER_NEAREST)
cv2.imwrite(str(out), roi_4x)
print(f"\nSaved enlarged ROI to {out}")
