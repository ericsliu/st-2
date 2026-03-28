"""Try aggressive contrast boosting for OCR."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import cv2
import numpy as np
from PIL import Image, ImageEnhance

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
    h, w = roi.shape[:2]

    # Lightness channel 3x
    light = np.max(roi, axis=2)
    light_3x = cv2.resize(light, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)

    # Approach 1: heavy Otsu on lightness
    _, otsu = cv2.threshold(light_3x, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bgr1 = cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR)
    pil1 = Image.fromarray(bgr1[:, :, ::-1])
    av1 = " ".join(t for t, _ in av.recognize(pil1))

    # Approach 2: fixed threshold at 50% brightness
    _, fixed = cv2.threshold(light_3x, 160, 255, cv2.THRESH_BINARY)
    bgr2 = cv2.cvtColor(fixed, cv2.COLOR_GRAY2BGR)
    pil2 = Image.fromarray(bgr2[:, :, ::-1])
    av2 = " ".join(t for t, _ in av.recognize(pil2))

    # Approach 3: PIL contrast enhancement (5x) on color 3x
    roi_3x = cv2.resize(roi, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)
    pil_color = Image.fromarray(roi_3x[:, :, ::-1])
    enhanced = ImageEnhance.Contrast(pil_color).enhance(5.0)
    av3 = " ".join(t for t, _ in av.recognize(enhanced))

    # Approach 4: invert lightness then Otsu
    inv = 255 - light_3x
    _, inv_otsu = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bgr4 = cv2.cvtColor(inv_otsu, cv2.COLOR_GRAY2BGR)
    pil4 = Image.fromarray(bgr4[:, :, ::-1])
    av4 = " ".join(t for t, _ in av.recognize(pil4))

    # Save best candidates
    cv2.imwrite(str(out_dir / f"cc_{stat_type.value}_otsu.png"), otsu)
    cv2.imwrite(str(out_dir / f"cc_{stat_type.value}_fixed.png"), fixed)
    enhanced.save(out_dir / f"cc_{stat_type.value}_contrast.png")
    cv2.imwrite(str(out_dir / f"cc_{stat_type.value}_inv_otsu.png"), inv_otsu)

    print(f"{stat_type.value:8s}: otsu='{av1}' fixed='{av2}' contrast='{av3}' inv_otsu='{av4}'")
