"""Debug: show raw OCR output for each gain region."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS, STAT_REGION_KEYS

from uma_trainer.config import OCRConfig
ocr = OCREngine(OCRConfig())

src = sys.argv[1] if len(sys.argv) > 1 else "runs/hishi_amazon_20260327/screen_012_speed_check.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

regions = STAT_SELECTION_REGIONS
gain_keys = ["gain_speed", "gain_stamina", "gain_power", "gain_guts", "gain_wit", "gain_skill_pts"]
for gain_key in gain_keys:
    if gain_key not in regions:
        continue
    stat = gain_key.replace("gain_", "")
    x1, y1, x2, y2 = regions[gain_key]
    roi = frame[y1:y2, x1:x2]
    # 3x upscale like read_gain_region does
    roi_up = cv2.resize(roi, (roi.shape[1] * 3, roi.shape[0] * 3), interpolation=cv2.INTER_CUBIC)
    raw_text = ocr.read_text(roi_up)
    parsed = ocr.read_gain_number(roi_up)
    print(f"{stat:8s}: raw='{raw_text}' -> parsed={parsed}  region={regions[gain_key]}")
