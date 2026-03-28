"""Debug OCR on specific regions of the current screen."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import io
import numpy as np
from PIL import Image

from uma_trainer.perception.regions import TURN_ACTION_REGIONS, STAT_REGION_KEYS
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.config import load_config

cfg = load_config("config/default.yaml")
ocr = OCREngine(cfg.ocr)

# Grab frame
result = subprocess.run(
    ["adb", "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
    capture_output=True, timeout=10,
)
img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
frame = np.array(img)[:, :, ::-1]  # RGB->BGR
print(f"Frame: {frame.shape}")

# Test each stat region
regions = TURN_ACTION_REGIONS
for stat_type, region_key in STAT_REGION_KEYS.items():
    region = regions.get(region_key)
    if region:
        x1, y1, x2, y2 = region
        roi = frame[y1:y2, x1:x2]
        text = ocr.read_region(frame, region)
        num = ocr.read_number_region(frame, region)
        print(f"{stat_type.value:8s} region={region} text='{text}' number={num}")

# Test turn counter
turn_region = regions.get("turn_counter")
if turn_region:
    text = ocr.read_region(frame, turn_region)
    print(f"Turn counter: text='{text}' region={turn_region}")

# Test mood
mood_region = regions.get("mood_label")
if mood_region:
    text = ocr.read_region(frame, mood_region)
    print(f"Mood label: text='{text}' region={mood_region}")

# Test energy bar region info
energy_region = regions.get("energy_bar")
if energy_region:
    print(f"Energy bar region: {energy_region}")

# Test period text
period_region = regions.get("period_text")
if period_region:
    text = ocr.read_region(frame, period_region)
    print(f"Period: text='{text}' region={period_region}")

# Save cropped regions as images for visual inspection
for name in ["stat_speed", "turn_counter", "mood_label", "period_text"]:
    region = regions.get(name)
    if region:
        x1, y1, x2, y2 = region
        roi = frame[y1:y2, x1:x2]
        roi_rgb = roi[:, :, ::-1]
        Image.fromarray(roi_rgb).save(f"screenshots/debug_{name}.png")
        print(f"Saved debug crop: screenshots/debug_{name}.png ({x2-x1}x{y2-y1})")
