"""Test Tesseract OCR with digit-only config on stat regions."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import cv2
import numpy as np
from PIL import Image
import pytesseract

from uma_trainer.perception.regions import TURN_ACTION_REGIONS, STAT_REGION_KEYS

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

# Tesseract config: digits only, single line
tess_config = "--psm 7 -c tessedit_char_whitelist=0123456789"

for stat_type, region_key in STAT_REGION_KEYS.items():
    region = TURN_ACTION_REGIONS.get(region_key)
    if not region:
        continue
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]
    h, w = roi.shape[:2]

    # Lightness channel
    light = np.max(roi, axis=2)

    # 3x upscale
    light_3x = cv2.resize(light, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)

    # Otsu on lightness
    _, otsu = cv2.threshold(light_3x, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Tesseract on various inputs
    pil_light = Image.fromarray(light_3x)
    pil_otsu = Image.fromarray(otsu)
    pil_color_3x = Image.fromarray(
        cv2.resize(roi, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)[:, :, ::-1]
    )

    t_light = pytesseract.image_to_string(pil_light, config=tess_config).strip()
    t_otsu = pytesseract.image_to_string(pil_otsu, config=tess_config).strip()
    t_color = pytesseract.image_to_string(pil_color_3x, config=tess_config).strip()

    print(f"{stat_type.value:8s}: light='{t_light}' otsu='{t_otsu}' color='{t_color}'")
