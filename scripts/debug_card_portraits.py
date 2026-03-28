"""Debug: test portrait slot detection on saved screenshots."""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.DEBUG, format="%(message)s")

import cv2
from uma_trainer.perception.pixel_analysis import count_panel_portraits
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS

panel_region = STAT_SELECTION_REGIONS["support_panel"]

# Test on debug_gains tile screenshots (different tiles selected)
stat_names = ["speed", "stamina", "power", "guts", "wit"]
print("=== Debug gains screenshots (turn 2) ===")
for i, stat in enumerate(stat_names):
    path = Path(f"screenshots/debug_gains/tile_{i}_{stat}_full.png")
    if not path.exists():
        continue
    frame = cv2.imread(str(path))
    count = count_panel_portraits(frame, panel_region)
    print(f"  {stat}: {count} portraits\n")

print("=== Validation screenshot (Wit selected) ===")
frame = cv2.imread("runs/hishi_amazon_20260327/screen_004_validate.png")
count = count_panel_portraits(frame, panel_region)
print(f"  wit: {count} portraits")
