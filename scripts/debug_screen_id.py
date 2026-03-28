"""Debug screen identification — show OCR text from all regions."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

# First check current state
frame = capture.grab_frame()
screen, details = screen_id.identify_with_details(frame)
print(f"Current screen: {screen.value}")
print(f"is_stat_selection: {screen_id.is_stat_selection(frame)}")
for region_name, text in details.items():
    print(f"  {region_name}: '{text}'")

# Now tap Training button and check again
from uma_trainer.action.adb_client import ADBClient
from uma_trainer.action.input_injector import InputInjector
from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center

adb = ADBClient(device_serial=DEVICE)
injector = InputInjector(adb, config)

train_btn = get_tap_center(TURN_ACTION_REGIONS["btn_training"])
print(f"\nTapping Training button at {train_btn}...")
injector.tap(*train_btn)
time.sleep(2.5)

frame = capture.grab_frame()
screen2, details2 = screen_id.identify_with_details(frame)
print(f"\nAfter tap screen: {screen2.value}")
print(f"is_stat_selection: {screen_id.is_stat_selection(frame)}")
for region_name, text in details2.items():
    print(f"  {region_name}: '{text}'")

# Go back
injector.tap(95, 1875)
time.sleep(1.5)
capture.stop()
