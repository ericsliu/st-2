"""Debug: test bulk stat parsing on different screenshots."""
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS, TURN_ACTION_REGIONS
from uma_trainer.config import OCRConfig
from uma_trainer.types import StatType

ocr = OCREngine(OCRConfig())

src = sys.argv[1] if len(sys.argv) > 1 else "runs/hishi_amazon_20260327/screen_015_turn4_scan.png"
frame = cv2.imread(src)
if frame is None:
    print(f"Cannot read {src}")
    sys.exit(1)

for label, regions in [("STAT_SELECTION", STAT_SELECTION_REGIONS), ("TURN_ACTION", TURN_ACTION_REGIONS)]:
    stat_regions = [regions.get(f"stat_{s.value}") for s in StatType]
    stat_regions = [r for r in stat_regions if r is not None]
    if not stat_regions:
        print(f"{label}: no stat regions")
        continue

    x1 = min(r[0] for r in stat_regions) - 80
    y1 = min(r[1] for r in stat_regions) - 15  # labels only, not gain previews
    x2 = max(r[2] for r in stat_regions) + 20
    y2 = max(r[3] for r in stat_regions) + 50

    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)

    text = ocr.read_region(frame, (x1, y1, x2, y2))
    print(f"\n{label} bulk region: ({x1},{y1},{x2},{y2})")
    print(f"Raw OCR text: '{text}'")

    cleaned = re.sub(r'/\s*\d{3,4}', '', text)
    numbers = [int(m.group()) for m in re.finditer(r'(?<!\d)\d{2,4}(?!\d)', cleaned)]
    numbers = [n for n in numbers if 10 <= n <= 2000]
    print(f"Extracted numbers: {numbers}")

    stat_keys = ["speed", "stamina", "power", "guts", "wit"]
    for i, val in enumerate(numbers):
        if i < len(stat_keys):
            print(f"  {stat_keys[i]} = {val}")

    # Save the crop
    roi = frame[y1:y2, x1:x2]
    cv2.imwrite(f"/tmp/stat_bulk_{label.lower()}.png", roi)
