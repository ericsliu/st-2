"""Debug: find precise stat value locations on the training screen."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.config import OCRConfig

ocr = OCREngine(OCRConfig())

src = sys.argv[1] if len(sys.argv) > 1 else "runs/hishi_amazon_20260327/screen_015_turn4_scan.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

# Try different y ranges for stat values (narrower strips)
# The stat values are the big bold numbers between the rank letter and /1200
print("=== Y-range scan for stat values (power column, x=390-490) ===")
for y_start in range(1275, 1340, 5):
    for y_end in [y_start + 30, y_start + 40, y_start + 50]:
        roi = frame[y_start:y_end, 390:490]
        roi_up = cv2.resize(roi, (roi.shape[1] * 3, roi.shape[0] * 3), interpolation=cv2.INTER_CUBIC)
        text = ocr.read_text(roi_up).strip()
        if text:
            print(f"  y={y_start}-{y_end}: '{text}'")

# Try tighter x ranges for each stat to avoid rank letter
print("\n=== Per-stat with tighter regions (value only, y=1285-1325) ===")
stat_regions = {
    "speed":   (95, 1285, 185, 1325),
    "stamina": (250, 1285, 340, 1325),
    "power":   (415, 1285, 505, 1325),
    "guts":    (585, 1285, 670, 1325),
    "wit":     (770, 1285, 855, 1325),
}
for stat, (x1, y1, x2, y2) in stat_regions.items():
    roi = frame[y1:y2, x1:x2]
    roi_up = cv2.resize(roi, (roi.shape[1] * 3, roi.shape[0] * 3), interpolation=cv2.INTER_CUBIC)
    text = ocr.read_text(roi_up).strip()
    num = ocr.read_number(roi_up)
    cv2.imwrite(f"/tmp/stat_tight_{stat}.png", roi)
    print(f"  {stat:8s}: raw='{text}' num={num}  ({x1},{y1},{x2},{y2})")
