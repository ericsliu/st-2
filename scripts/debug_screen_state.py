"""Debug current screen detection."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
logging.basicConfig(level=logging.DEBUG, format="%(name)-30s %(levelname)-5s %(message)s")

from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.screen_identifier import ScreenIdentifier

DEVICE = "127.0.0.1:5555"
config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))

capture = ScrcpyCapture(config.capture)
capture.start()

ocr = OCREngine(config.ocr)
screen_id = ScreenIdentifier(ocr=ocr)

frame = capture.grab_frame()
print(f"Frame: {frame.shape[1]}x{frame.shape[0]}")

screen = screen_id.identify(frame)
print(f"Screen: {screen}")

is_stat = screen_id.is_stat_selection(frame)
print(f"Is stat selection: {is_stat}")

capture.stop()
