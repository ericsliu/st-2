"""Debug: capture stat selection screen and save crops of all gain regions.

Shows exactly what the OCR sees for each gain region, plus the selected
label and failure rate. Saves crops to screenshots/debug_gains/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.regions import STAT_SELECTION_REGIONS
from uma_trainer.perception.screen_identifier import ScreenIdentifier

DEVICE = "127.0.0.1:5555"
OUT_DIR = Path("screenshots/debug_gains")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))
    capture = ScrcpyCapture(config.capture)
    capture.start()

    ocr = OCREngine(config.ocr)

    frame = capture.grab_frame()
    h, w = frame.shape[:2]
    print(f"Frame size: {w}x{h}")

    # Save full frame
    full_rgb = frame[:, :, ::-1]
    Image.fromarray(full_rgb).save(OUT_DIR / "full_frame.png")
    print(f"Saved full frame to {OUT_DIR / 'full_frame.png'}")

    # Regions to debug
    regions_of_interest = [
        "selected_label",
        "gain_speed", "gain_stamina", "gain_power", "gain_guts", "gain_wit",
        "gain_skill_pts",
        "panel_gain_speed", "panel_gain_stamina", "panel_gain_power",
        "panel_gain_guts", "panel_gain_wit", "panel_gain_skill",
        "failure_rate",
        "stat_speed", "stat_stamina", "stat_power", "stat_guts", "stat_wit",
    ]

    print()
    print(f"{'Region':<25} {'Coords':<25} {'OCR Result'}")
    print("-" * 80)

    for name in regions_of_interest:
        region = STAT_SELECTION_REGIONS.get(name)
        if not region:
            print(f"{name:<25} (not defined)")
            continue

        x1, y1, x2, y2 = region
        crop = frame[y1:y2, x1:x2]

        if crop.size == 0:
            print(f"{name:<25} {str(region):<25} (empty crop)")
            continue

        # Save crop as image
        crop_rgb = crop[:, :, ::-1]
        Image.fromarray(crop_rgb).save(OUT_DIR / f"{name}.png")

        # OCR
        text = ocr.read_region(frame, region)
        coords_str = f"({x1},{y1})-({x2},{y2})"
        print(f"{name:<25} {coords_str:<25} '{text}'")

    print()
    print(f"All crops saved to {OUT_DIR}/")
    capture.stop()


if __name__ == "__main__":
    main()
