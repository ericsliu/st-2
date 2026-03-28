"""Find the Close button - wider scan."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
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

# OCR the popup area to find "Close" text
for y1, y2, label in [
    (800, 900, "upper popup"),
    (900, 1000, "mid popup"),
    (1000, 1100, "lower popup"),
    (1100, 1200, "below popup"),
]:
    roi = frame[y1:y2, 200:850]
    pil = Image.fromarray(roi[:, :, ::-1])
    results = av.recognize(pil)
    print(f"y={y1}-{y2} ({label}):")
    for t, c in results:
        print(f"  '{t}' ({c:.2f})")
