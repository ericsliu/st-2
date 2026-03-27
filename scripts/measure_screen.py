"""OCR horizontal strips of a screenshot to map where text appears.

Usage: .venv/bin/python scripts/measure_screen.py [screenshot_path]
Default: takes a live screenshot via ADB.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from uma_trainer.perception.ocr import OCREngine
from uma_trainer.config import OCRConfig

DEVICE = "127.0.0.1:5555"
STRIP_HEIGHT = 50  # pixels per horizontal strip


def main():
    if len(sys.argv) > 1:
        img_path = sys.argv[1]
        img = Image.open(img_path)
        frame = np.array(img)[:, :, ::-1]  # RGB->BGR
    else:
        from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
        from uma_trainer.config import CaptureConfig
        capture = ScrcpyCapture(CaptureConfig(device_serial=DEVICE))
        capture.start()
        frame = capture.grab_frame()
        capture.stop()

    h, w = frame.shape[:2]
    print(f"Frame: {w}x{h}")

    ocr = OCREngine(OCRConfig())

    print(f"\n{'Y Range':>12}  Text")
    print("-" * 80)

    for y_start in range(0, h, STRIP_HEIGHT):
        y_end = min(y_start + STRIP_HEIGHT, h)
        region = (0, y_start, w, y_end)
        text = ocr.read_region(frame, region).strip()
        if text:
            print(f"  {y_start:4d}-{y_end:4d}  {text}")


if __name__ == "__main__":
    main()
