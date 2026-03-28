"""Check HSV values of actual gain digits vs skin/background."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

# +5 crop — gain digits at right, skin tones nearby
crop = cv2.imread("data/gain_ocr_samples/1774657071547_510_1185.png")
hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

# Sample the actual "5" digit area (roughly x=120-155, y=10-50)
digit_area = hsv[10:50, 120:155]
print("+5 digit area HSV stats:")
print(f"  H: min={digit_area[:,:,0].min()} max={digit_area[:,:,0].max()} mean={digit_area[:,:,0].mean():.0f}")
print(f"  S: min={digit_area[:,:,1].min()} max={digit_area[:,:,1].max()} mean={digit_area[:,:,1].mean():.0f}")
print(f"  V: min={digit_area[:,:,2].min()} max={digit_area[:,:,2].max()} mean={digit_area[:,:,2].mean():.0f}")

# Sample the skin area (roughly x=150-180, y=30-60)
skin_area = hsv[30:60, 150:180]
print("\nSkin area HSV stats:")
print(f"  H: min={skin_area[:,:,0].min()} max={skin_area[:,:,0].max()} mean={skin_area[:,:,0].mean():.0f}")
print(f"  S: min={skin_area[:,:,1].min()} max={skin_area[:,:,1].max()} mean={skin_area[:,:,1].mean():.0f}")
print(f"  V: min={skin_area[:,:,2].min()} max={skin_area[:,:,2].max()} mean={skin_area[:,:,2].mean():.0f}")

# Also check the "+" area
plus_area = hsv[15:45, 87:120]
print("\n'+' area HSV stats:")
print(f"  H: min={plus_area[:,:,0].min()} max={plus_area[:,:,0].max()} mean={plus_area[:,:,0].mean():.0f}")
print(f"  S: min={plus_area[:,:,1].min()} max={plus_area[:,:,1].max()} mean={plus_area[:,:,1].mean():.0f}")
print(f"  V: min={plus_area[:,:,2].min()} max={plus_area[:,:,2].max()} mean={plus_area[:,:,2].mean():.0f}")

# Check the +11 crop digit area
crop11 = cv2.imread("data/gain_ocr_samples/1774657070255_175_1185.png")
hsv11 = cv2.cvtColor(crop11, cv2.COLOR_BGR2HSV)
digit_area11 = hsv11[10:55, 100:165]
print("\n+11 digit area HSV stats:")
print(f"  H: min={digit_area11[:,:,0].min()} max={digit_area11[:,:,0].max()} mean={digit_area11[:,:,0].mean():.0f}")
print(f"  S: min={digit_area11[:,:,1].min()} max={digit_area11[:,:,1].max()} mean={digit_area11[:,:,1].mean():.0f}")
print(f"  V: min={digit_area11[:,:,2].min()} max={digit_area11[:,:,2].max()} mean={digit_area11[:,:,2].mean():.0f}")

# Show what different thresholds capture
for s_min in [50, 80, 100, 120, 150]:
    orange = cv2.inRange(hsv, (5, s_min, 150), (38, 255, 255))
    count = np.count_nonzero(orange)
    cv2.imwrite(f"/tmp/hsv_s{s_min}.png", orange)
    print(f"\nS>={s_min}: {count} pixels")
