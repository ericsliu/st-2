"""Debug: compare regular OCR vs gain-hinted OCR on gain regions."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS
from uma_trainer.config import OCRConfig

ocr = OCREngine(OCRConfig())

src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/tile_stamina.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

gain_keys = [
    ("speed", "gain_speed"),
    ("stamina", "gain_stamina"),
    ("power", "gain_power"),
    ("guts", "gain_guts"),
    ("wit", "gain_wit"),
]

print(f"{'Stat':8s}  {'Regular':12s}  {'Hinted':12s}  {'Parsed':8s}")
print("-" * 50)

for stat, key in gain_keys:
    region = STAT_SELECTION_REGIONS.get(key)
    if not region:
        continue
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        continue

    roi_up = cv2.resize(roi, (roi.shape[1] * 3, roi.shape[0] * 3),
                        interpolation=cv2.INTER_LANCZOS4)

    regular = ocr.read_text(roi_up).strip()
    hinted = ocr.read_text_gain_hints(roi_up).strip()
    parsed = ocr.read_gain_region(frame, region)

    if regular or hinted:
        print(f"{stat:8s}  {regular:12s}  {hinted:12s}  {str(parsed):8s}")
