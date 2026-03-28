"""Debug preprocessed stat regions — save binary images and check OCR."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import cv2
import numpy as np
from PIL import Image

from uma_trainer.perception.regions import TURN_ACTION_REGIONS, STAT_REGION_KEYS
from uma_trainer.perception.ocr import AppleVisionOCR

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

av = AppleVisionOCR()
out_dir = Path("screenshots/calibration")

for stat_type, region_key in STAT_REGION_KEYS.items():
    region = TURN_ACTION_REGIONS.get(region_key)
    if not region:
        continue
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]

    # Grayscale
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    gray_3x = cv2.resize(gray, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)

    # Adaptive threshold
    binary = cv2.adaptiveThreshold(
        gray_3x, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 10,
    )

    # Also try Otsu threshold
    _, otsu = cv2.threshold(gray_3x, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Also try inverted (dark text on light bg might need inversion)
    _, inv = cv2.threshold(gray_3x, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Save all variants
    cv2.imwrite(str(out_dir / f"pp_{stat_type.value}_gray3x.png"), gray_3x)
    cv2.imwrite(str(out_dir / f"pp_{stat_type.value}_adaptive.png"), binary)
    cv2.imwrite(str(out_dir / f"pp_{stat_type.value}_otsu.png"), otsu)
    cv2.imwrite(str(out_dir / f"pp_{stat_type.value}_inv.png"), inv)

    # OCR each variant
    for name, img_arr in [("gray3x", gray_3x), ("adaptive", binary), ("otsu", otsu), ("inv", inv)]:
        bgr = cv2.cvtColor(img_arr, cv2.COLOR_GRAY2BGR)
        pil = Image.fromarray(bgr[:, :, ::-1])
        results = av.recognize(pil)
        text = " ".join(t for t, _ in results)
        print(f"{stat_type.value:8s} {name:10s}: '{text}'")
    print()
