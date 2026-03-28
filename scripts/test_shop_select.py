"""Test shop item selection — taps checkboxes but does NOT confirm purchase."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.action.input_injector import InputInjector
from uma_trainer.action.adb_client import ADBClient
from uma_trainer.decision.shop_manager import ITEM_CATALOGUE, ItemTier
from PIL import Image
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-30s %(levelname)-5s %(message)s")

DEVICE = "127.0.0.1:5555"
config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))
capture = ScrcpyCapture(config.capture)
capture.start()
ocr = OCREngine(config.ocr)
adb = ADBClient(device_serial=DEVICE)
injector = InputInjector(adb, config)

from do_one_turn import _build_shop_name_matcher, _scan_shop_items, _get_shop_coins

name_to_key = _build_shop_name_matcher()

# Navigate to shop
print("Tapping Shop button...")
injector.tap(650, 1665)
time.sleep(3.0)

frame = capture.grab_frame()
coins = _get_shop_coins(frame, ocr)
print(f"Coins: {coins}")

# Scan first page items
visible = _scan_shop_items(frame, ocr, name_to_key)
print(f"\nFound {len(visible)} items on page 0")

# Select the first 2 items we find (just tap their checkboxes)
selected = 0
for item_key, name_y, is_purchased in visible:
    if is_purchased:
        continue
    if selected >= 2:
        break

    item = ITEM_CATALOGUE[item_key]
    checkbox_x = 950
    checkbox_y = name_y + 15
    print(f"  Tapping checkbox for '{item.name}' at ({checkbox_x}, {checkbox_y})")
    injector.tap(checkbox_x, checkbox_y)
    time.sleep(1.0)
    selected += 1

# Take screenshot to see if checkboxes changed
time.sleep(1.0)
frame = capture.grab_frame()
Image.fromarray(frame).save("screenshots/shop_selected.png")
print("\nScreenshot saved — check if checkboxes are now checked (blue)")

# Tap Reset to undo selections (don't actually buy)
print("Tapping Reset to undo...")
injector.tap(810, 1640)
time.sleep(1.5)

# Go back
print("Going back...")
injector.tap(50, 1870)
time.sleep(2.0)
capture.stop()
print("Done")
