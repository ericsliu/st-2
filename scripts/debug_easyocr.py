"""Compare Apple Vision vs EasyOCR on stat regions."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import cv2
import numpy as np
from PIL import Image

from uma_trainer.perception.regions import TURN_ACTION_REGIONS, STAT_REGION_KEYS
from uma_trainer.perception.ocr import AppleVisionOCR, EasyOCROCR

result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

av = AppleVisionOCR()
eo = EasyOCROCR(["en"])

for stat_type, region_key in STAT_REGION_KEYS.items():
    region = TURN_ACTION_REGIONS.get(region_key)
    if not region:
        continue
    x1, y1, x2, y2 = region
    roi = frame[y1:y2, x1:x2]

    # 1x
    pil_1x = Image.fromarray(roi[:, :, ::-1])
    av_1x = " ".join(t for t, _ in av.recognize(pil_1x))
    eo_1x = " ".join(t for t, _ in eo.recognize(roi[:, :, ::-1]))  # EasyOCR wants RGB

    # 3x color upscale
    h, w = roi.shape[:2]
    roi_3x = cv2.resize(roi, (w * 3, h * 3), interpolation=cv2.INTER_LANCZOS4)
    pil_3x = Image.fromarray(roi_3x[:, :, ::-1])
    av_3x = " ".join(t for t, _ in av.recognize(pil_3x))
    eo_3x = " ".join(t for t, _ in eo.recognize(roi_3x[:, :, ::-1]))

    print(f"{stat_type.value:8s}: AV@1x='{av_1x}' AV@3x='{av_3x}' EO@1x='{eo_1x}' EO@3x='{eo_3x}'")
