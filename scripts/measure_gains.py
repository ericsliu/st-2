"""Precisely measure gain preview positions on the stat selection screen.

Scans vertical strips in the gain row (y=1200-1250) and stat label row
(y=1250-1300) to find exact X positions for each stat.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from uma_trainer.perception.ocr import OCREngine
from uma_trainer.config import OCRConfig

img = Image.open("screenshots/debug_gains/stat_selection.png")
frame = np.array(img)[:, :, ::-1]
h, w = frame.shape[:2]

ocr = OCREngine(OCRConfig())

# Scan the gain row in vertical strips
print("=== Gain preview row (y=1190-1250) ===")
for x_start in range(0, w, 100):
    x_end = min(x_start + 150, w)
    region = (x_start, 1190, x_end, 1250)
    text = ocr.read_region(frame, region).strip()
    if text:
        print(f"  x={x_start:4d}-{x_end:4d}: '{text}'")

print("\n=== Stat label row (y=1250-1310) ===")
for x_start in range(0, w, 100):
    x_end = min(x_start + 150, w)
    region = (x_start, 1250, x_end, 1310)
    text = ocr.read_region(frame, region).strip()
    if text:
        print(f"  x={x_start:4d}-{x_end:4d}: '{text}'")

print("\n=== Stat value row (y=1300-1370) ===")
for x_start in range(0, w, 100):
    x_end = min(x_start + 150, w)
    region = (x_start, 1300, x_end, 1370)
    text = ocr.read_region(frame, region).strip()
    if text:
        print(f"  x={x_start:4d}-{x_end:4d}: '{text}'")

print("\n=== Failure rate (y=1340-1450) ===")
text = ocr.read_region(frame, (0, 1340, 300, 1460)).strip()
print(f"  '{text}'")

print("\n=== Tile labels row (y=1550-1700) ===")
for x_start in range(0, w, 120):
    x_end = min(x_start + 200, w)
    region = (x_start, 1550, x_end, 1700)
    text = ocr.read_region(frame, region).strip()
    if text:
        print(f"  x={x_start:4d}-{x_end:4d}: '{text}'")

print("\n=== Selected label area (y=300-410) ===")
text = ocr.read_region(frame, (0, 290, 400, 420)).strip()
print(f"  '{text}'")

print("\n=== Bottom bar (y=1840-1920) ===")
text = ocr.read_region(frame, (0, 1840, 1080, 1920)).strip()
print(f"  '{text}'")
