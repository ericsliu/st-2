"""Try HSV value channel extraction for cleaner OCR."""

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
    h, w = roi.shape[:2]

    # Extract V channel from HSV
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    v_channel = hsv[:, :, 2]

    # Upscale 3x
    v_3x = cv2.resize(v_channel, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)

    # Try CLAHE (contrast-limited adaptive histogram equalization)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    v_clahe = clahe.apply(v_3x)

    # Save
    cv2.imwrite(str(out_dir / f"hsv_{stat_type.value}_v3x.png"), v_3x)
    cv2.imwrite(str(out_dir / f"hsv_{stat_type.value}_clahe.png"), v_clahe)

    # OCR on V channel
    v_bgr = cv2.cvtColor(v_3x, cv2.COLOR_GRAY2BGR)
    pil_v = Image.fromarray(v_bgr[:, :, ::-1])
    av_v = " ".join(t for t, _ in av.recognize(pil_v))

    # OCR on CLAHE
    c_bgr = cv2.cvtColor(v_clahe, cv2.COLOR_GRAY2BGR)
    pil_c = Image.fromarray(c_bgr[:, :, ::-1])
    av_c = " ".join(t for t, _ in av.recognize(pil_c))

    # Also try: extract the lightest channel per pixel (max of R, G, B)
    light = np.max(roi, axis=2)
    light_3x = cv2.resize(light, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)
    l_bgr = cv2.cvtColor(light_3x, cv2.COLOR_GRAY2BGR)
    pil_l = Image.fromarray(l_bgr[:, :, ::-1])
    av_l = " ".join(t for t, _ in av.recognize(pil_l))

    cv2.imwrite(str(out_dir / f"hsv_{stat_type.value}_light.png"), light_3x)

    print(f"{stat_type.value:8s}: V='{av_v}'  CLAHE='{av_c}'  Light='{av_l}'")
