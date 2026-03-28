"""Debug race list screen - save strips at different y levels."""

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

# Save horizontal strips and OCR them
strips = [
    (700, 830, "race_header"),
    (830, 950, "race_slot_0"),
    (950, 1080, "race_slot_1"),
    (1080, 1200, "race_slot_2"),
    (1200, 1320, "race_slot_3"),
    (1640, 1720, "race_buttons"),
]

for y1, y2, label in strips:
    strip = frame[y1:y2, 0:1080]
    cv2.imwrite(f"screenshots/calibration/race_{label}.png", strip)
    pil = Image.fromarray(strip[:, :, ::-1])
    results = av.recognize(pil)
    texts = [t for t, c in results]
    print(f"y={y1}-{y2} ({label}): {texts}")
