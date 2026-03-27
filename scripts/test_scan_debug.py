"""Tap each training tile and save the frame + OCR the gain regions.
Saves a frame per tile so we can see what the OCR is looking at."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from uma_trainer.action.adb_client import ADBClient
from uma_trainer.action.input_injector import InputInjector
from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS, TRAINING_TILES, get_tap_center

DEVICE = "127.0.0.1:5555"
OUT = Path("screenshots/debug_gains")
OUT.mkdir(parents=True, exist_ok=True)
STATS = ["speed", "stamina", "power", "guts", "wit"]

config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))
capture = ScrcpyCapture(config.capture)
capture.start()
ocr = OCREngine(config.ocr)
adb = ADBClient(device_serial=DEVICE)
injector = InputInjector(adb, config)

for i, tile in enumerate(TRAINING_TILES):
    tap_x, tap_y = get_tap_center(tile.tap_target)
    print(f"\n=== Tile {i} ({STATS[i]}) — tapping ({tap_x}, {tap_y}) ===")
    injector.tap(tap_x, tap_y)
    time.sleep(1.0)

    frame = capture.grab_frame()
    Image.fromarray(frame[:, :, ::-1]).save(OUT / f"scan_{i}_{STATS[i]}.png")

    # OCR gains using preprocessed number reader
    for stat in STATS:
        key = f"gain_{stat}"
        region = STAT_SELECTION_REGIONS.get(key)
        if not region:
            continue
        val = ocr.read_number_region(frame, region)
        raw = ocr.read_region(frame, region).strip()
        if val is not None or raw:
            print(f"  {key}: num={val}  raw='{raw}'")

    # Also check selected label and failure
    for key in ["selected_label", "failure_rate"]:
        region = STAT_SELECTION_REGIONS.get(key)
        if region:
            text = ocr.read_region(frame, region).strip()
            print(f"  {key}: '{text}'")

capture.stop()
print(f"\nFrames saved to {OUT}/")
