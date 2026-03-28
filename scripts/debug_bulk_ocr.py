"""Debug: show raw text from bulk stat OCR."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import numpy as np
from PIL import Image

from uma_trainer.perception.ocr import AppleVisionOCR
from uma_trainer.perception.regions import TURN_ACTION_REGIONS, STAT_REGION_KEYS
from uma_trainer.types import StatType

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

av = AppleVisionOCR()

# Build the same bounding box as _parse_stats_bulk
stat_regions = [
    TURN_ACTION_REGIONS.get(f"stat_{s.value}") for s in StatType
]
stat_regions = [r for r in stat_regions if r is not None]

x1 = min(r[0] for r in stat_regions) - 80
y1 = min(r[1] for r in stat_regions) - 50
x2 = max(r[2] for r in stat_regions) + 20
y2 = max(r[3] for r in stat_regions) + 50

x1 = max(0, x1)
y1 = max(0, y1)
x2 = min(frame.shape[1], x2)
y2 = min(frame.shape[0], y2)

print(f"Bulk region: ({x1}, {y1}, {x2}, {y2})")

roi = frame[y1:y2, x1:x2]
pil = Image.fromarray(roi[:, :, ::-1])
results = av.recognize(pil)

print("\nRaw OCR results:")
for text, conf in results:
    print(f"  '{text}' (conf={conf:.2f})")

# Show the joined text that the regex would parse
full_text = " ".join(t for t, _ in results)
print(f"\nJoined: '{full_text}'")
