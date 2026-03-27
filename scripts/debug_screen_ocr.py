"""Debug: identify current screen using OCR and show what text was found."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.screen_identifier import ScreenIdentifier

DEVICE = "127.0.0.1:5555"


def main():
    config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))
    capture = ScrcpyCapture(config.capture)
    capture.start()

    ocr = OCREngine(config.ocr)
    screen_id = ScreenIdentifier(ocr=ocr)

    frame = capture.grab_frame()
    h, w = frame.shape[:2]
    print(f"Frame: {w}x{h}")

    result, details = screen_id.identify_with_details(frame)
    print(f"\nScreen: {result.value}")
    print(f"\nOCR text per region:")
    for region_name, text in details.items():
        print(f"  {region_name:15s}: '{text}'")

    # Also test is_stat_selection
    from uma_trainer.types import ScreenState
    if result == ScreenState.TRAINING:
        is_stat = screen_id.is_stat_selection(frame)
        print(f"\n  is_stat_selection: {is_stat}")

    capture.stop()


if __name__ == "__main__":
    main()
