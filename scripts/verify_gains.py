"""Verify gain region OCR on the saved stat selection screenshot."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from uma_trainer.perception.ocr import OCREngine
from uma_trainer.config import OCRConfig
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS

img = Image.open("screenshots/debug_gains/stat_selection.png")
frame = np.array(img)[:, :, ::-1]

ocr = OCREngine(OCRConfig())

regions_to_test = [
    "selected_label", "selected_subtitle",
    "gain_speed", "gain_stamina", "gain_power", "gain_guts", "gain_wit",
    "gain_skill_pts",
    "stat_speed", "stat_stamina", "stat_power", "stat_guts", "stat_wit",
    "skill_pts", "failure_rate",
]

print(f"{'Region':<22} {'Coords':<28} {'OCR Result'}")
print("-" * 75)

for name in regions_to_test:
    region = STAT_SELECTION_REGIONS.get(name)
    if not region:
        print(f"{name:<22} (not defined)")
        continue
    x1, y1, x2, y2 = region
    text = ocr.read_region(frame, region).strip()
    # Also try the preprocessed number reader for gain regions
    num = ocr.read_number_region(frame, region) if "gain" in name else None
    num_str = f"  num={num}" if num is not None else ""
    print(f"{name:<22} ({x1},{y1})-({x2},{y2})  '{text}'{num_str}")
