"""Debug race list screen - find race entries."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import cv2
import numpy as np
from PIL import Image

from uma_trainer.perception.ocr import AppleVisionOCR

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

av = AppleVisionOCR()

# Save the race entries section - from y=1020 (where the list begins) downward
# From the screenshot: first race entry starts around y=1040, each is about 150px tall
strips = [
    (1020, 1060, "list_header"),
    (1060, 1230, "slot_0"),
    (1230, 1400, "slot_1"),
    (1400, 1570, "slot_2"),
    (1570, 1740, "slot_3"),
]

for y1, y2, label in strips:
    strip = frame[y1:y2, 0:1080]
    cv2.imwrite(f"screenshots/calibration/race2_{label}.png", strip)
    pil = Image.fromarray(strip[:, :, ::-1])
    results = av.recognize(pil)
    texts = [(t, round(c, 2)) for t, c in results]
    print(f"y={y1}-{y2} ({label}):")
    for t, c in texts:
        print(f"  '{t}' ({c})")
