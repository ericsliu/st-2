"""Try OCR on the full stat row instead of individual values."""

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

# Full stat row - just the numbers, no /1200
# y=1295-1330, x=0-870 (all 5 stats)
roi = frame[1295:1330, 0:870]
light = np.max(roi, axis=2)

# 3x upscale
h, w = light.shape
light_3x = cv2.resize(light, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)
pil = Image.fromarray(cv2.cvtColor(light_3x, cv2.COLOR_GRAY2BGR)[:, :, ::-1])
results = av.recognize(pil)
print("Full row (lightness 3x):")
for text, conf in results:
    print(f"  '{text}' (conf={conf:.2f})")

# Try color 3x
roi_3x = cv2.resize(roi, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)
pil_c = Image.fromarray(roi_3x[:, :, ::-1])
results_c = av.recognize(pil_c)
print("\nFull row (color 3x):")
for text, conf in results_c:
    print(f"  '{text}' (conf={conf:.2f})")

# Try with stat labels row included (y=1250-1330)
roi2 = frame[1250:1330, 0:870]
h2, w2 = roi2.shape[:2]
roi2_3x = cv2.resize(roi2, (w2 * 2, h2 * 2), interpolation=cv2.INTER_LANCZOS4)
pil2 = Image.fromarray(roi2_3x[:, :, ::-1])
results2 = av.recognize(pil2)
print("\nWith labels (color 2x):")
for text, conf in results2:
    print(f"  '{text}' (conf={conf:.2f})")

# Try the full stat+label+/1200 block
roi3 = frame[1250:1380, 0:1080]
pil3 = Image.fromarray(roi3[:, :, ::-1])
results3 = av.recognize(pil3)
print("\nFull stat block (1x):")
for text, conf in results3:
    print(f"  '{text}' (conf={conf:.2f})")
