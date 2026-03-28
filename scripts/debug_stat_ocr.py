"""Debug: show raw OCR output for stat value regions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS
from uma_trainer.config import OCRConfig

ocr = OCREngine(OCRConfig())

src = sys.argv[1] if len(sys.argv) > 1 else "runs/hishi_amazon_20260327/screen_015_turn4_scan.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

regions = STAT_SELECTION_REGIONS
for stat in ["speed", "stamina", "power", "guts", "wit"]:
    key = f"stat_{stat}"
    if key not in regions:
        continue
    x1, y1, x2, y2 = regions[key]
    roi = frame[y1:y2, x1:x2]
    # Save crop for visual inspection
    out = Path(f"/tmp/stat_crop_{stat}.png")
    cv2.imwrite(str(out), roi)
    # Also try 3x upscale
    roi_up = cv2.resize(roi, (roi.shape[1] * 3, roi.shape[0] * 3), interpolation=cv2.INTER_CUBIC)
    raw_text = ocr.read_text(roi_up)
    number = ocr.read_number(roi_up)
    print(f"{stat:8s}: raw='{raw_text}' -> number={number}  region=({x1},{y1},{x2},{y2})  crop={roi.shape[1]}x{roi.shape[0]}")
