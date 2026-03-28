"""Check raw OCR output for each stat region."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import re
import numpy as np
from PIL import Image

from uma_trainer.perception.regions import TURN_ACTION_REGIONS, STAT_REGION_KEYS
from uma_trainer.perception.ocr import OCREngine, AppleVisionOCR
from uma_trainer.config import load_config

cfg = load_config("config/default.yaml")

# Grab frame
result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR

# Init Apple Vision directly
av = AppleVisionOCR()

regions = TURN_ACTION_REGIONS
for stat_type, region_key in STAT_REGION_KEYS.items():
    region = regions.get(region_key)
    if not region:
        continue
    x1, y1, x2, y2 = region
    roi_bgr = frame[y1:y2, x1:x2]
    roi_rgb = Image.fromarray(roi_bgr[:, :, ::-1])

    # Raw Apple Vision results
    av_results = av.recognize(roi_rgb)
    av_text = " ".join(t for t, _ in av_results)
    av_conf = [f"{c:.2f}" for _, c in av_results]

    # Also try with upscaled image (2x)
    w, h = roi_rgb.size
    roi_2x = roi_rgb.resize((w * 3, h * 3), Image.LANCZOS)
    av_results_2x = av.recognize(roi_2x)
    av_text_2x = " ".join(t for t, _ in av_results_2x)

    print(f"{stat_type.value:8s}: AV='{av_text}' conf={av_conf}  AV@3x='{av_text_2x}'")

# Also test turn counter
turn_region = regions.get("turn_counter")
if turn_region:
    x1, y1, x2, y2 = turn_region
    roi_bgr = frame[y1:y2, x1:x2]
    roi_rgb = Image.fromarray(roi_bgr[:, :, ::-1])
    av_results = av.recognize(roi_rgb)
    av_text = " ".join(t for t, _ in av_results)
    roi_2x = roi_rgb.resize((roi_rgb.width * 3, roi_rgb.height * 3), Image.LANCZOS)
    av_results_2x = av.recognize(roi_2x)
    av_text_2x = " ".join(t for t, _ in av_results_2x)
    print(f"turn    : AV='{av_text}'  AV@3x='{av_text_2x}'")
