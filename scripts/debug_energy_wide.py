"""Debug: show energy bar with wider region to find the rounded caps."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

src = sys.argv[1] if len(sys.argv) > 1 else "runs/hishi_amazon_20260327/screen_006_wit_energy.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

# Wide region around energy bar (extend 30px each side)
x1, y1, x2, y2 = 330, 216, 750, 230
roi = frame[y1:y2, x1:x2]
hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

bar_width = x2 - x1
print(f"Wide region: ({x1},{y1})-({x2},{y2}), ROI shape={roi.shape}")
print(f"Bar width: {bar_width} cols\n")

col_s = np.mean(hsv[:, :, 1], axis=0)
col_v = np.mean(hsv[:, :, 2], axis=0)
col_h = np.mean(hsv[:, :, 0], axis=0)

# Show every column near the edges
print("=== Left edge (first 50 cols from x=330) ===")
print("Col  absX    H     S     V")
for i in range(50):
    print(f"{i:3d}  {x1+i:4d}  {col_h[i]:5.1f} {col_s[i]:5.1f} {col_v[i]:5.1f}")

print(f"\n=== Right edge (last 60 cols to x=750) ===")
print("Col  absX    H     S     V")
for i in range(bar_width - 60, bar_width):
    print(f"{i:3d}  {x1+i:4d}  {col_h[i]:5.1f} {col_s[i]:5.1f} {col_v[i]:5.1f}")

# Save enlarged ROI
out = Path(src).parent / "energy_wide_debug.png"
roi_4x = cv2.resize(roi, (roi.shape[1] * 4, roi.shape[0] * 4), interpolation=cv2.INTER_NEAREST)
cv2.imwrite(str(out), roi_4x)
print(f"\nSaved {out}")
