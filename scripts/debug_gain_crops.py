"""Debug: tap each training tile and save the full frame + gain region crops.

Navigate to the stat selection screen first, then run this.
Saves to screenshots/debug_gains/.
"""

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
OUT_DIR = Path("screenshots/debug_gains")
OUT_DIR.mkdir(parents=True, exist_ok=True)

STAT_NAMES = ["speed", "stamina", "power", "guts", "wit"]

GAIN_KEYS = {
    "bar": [f"gain_{s}" for s in STAT_NAMES],
    "panel": [f"panel_gain_{s}" for s in STAT_NAMES],
}


def save_crop(frame, region, name):
    x1, y1, x2, y2 = region
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return
    crop_rgb = crop[:, :, ::-1]
    Image.fromarray(crop_rgb).save(OUT_DIR / f"{name}.png")


def main():
    config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))
    capture = ScrcpyCapture(config.capture)
    capture.start()
    ocr = OCREngine(config.ocr)
    adb = ADBClient(device_serial=DEVICE)
    injector = InputInjector(adb, config)

    for i, tile_region in enumerate(TRAINING_TILES):
        stat = STAT_NAMES[i]
        tap_x, tap_y = get_tap_center(tile_region.tap_target)

        print(f"\n--- Tapping tile {i} ({stat}) at ({tap_x}, {tap_y}) ---")
        injector.tap(tap_x, tap_y)
        time.sleep(1.0)

        frame = capture.grab_frame()
        h, w = frame.shape[:2]

        # Save full frame
        full_rgb = frame[:, :, ::-1]
        Image.fromarray(full_rgb).save(OUT_DIR / f"tile_{i}_{stat}_full.png")

        # Save and OCR each gain region
        for source, keys in GAIN_KEYS.items():
            for key in keys:
                region = STAT_SELECTION_REGIONS.get(key)
                if not region:
                    continue
                save_crop(frame, region, f"tile_{i}_{stat}_{key}")
                text = ocr.read_region(frame, region)
                print(f"  {key:25s} {str(region):25s} -> '{text}'")

        # Also selected_label and failure_rate
        for extra_key in ["selected_label", "failure_rate"]:
            region = STAT_SELECTION_REGIONS.get(extra_key)
            if region:
                save_crop(frame, region, f"tile_{i}_{stat}_{extra_key}")
                text = ocr.read_region(frame, region)
                print(f"  {extra_key:25s} {str(region):25s} -> '{text}'")

    capture.stop()
    print(f"\nAll crops saved to {OUT_DIR}/")


if __name__ == "__main__":
    main()
