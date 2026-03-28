"""Test shop item OCR and purchase selection (dry run — no actual buying)."""
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
print(f"Matching against {len(name_to_key)} item names")

# Navigate to shop
print("\nTapping Shop button...")
injector.tap(650, 1665)
time.sleep(3.0)

frame = capture.grab_frame()
Image.fromarray(frame).save("screenshots/shop_buy_test.png")

coins = _get_shop_coins(frame, ocr)
print(f"\nCoins: {coins}")

print("\n=== Scanning visible items ===")
all_items = []
for page in range(4):
    if page > 0:
        injector.swipe(540, 1100, 540, 750, duration_ms=400)
        time.sleep(2.0)
        frame = capture.grab_frame()

    visible = _scan_shop_items(frame, ocr, name_to_key)
    for item_key, name_y, is_purchased in visible:
        item = ITEM_CATALOGUE[item_key]
        status = " PURCHASED" if is_purchased else ""
        print(f"  [{page}] {item.name} ({item_key}) cost={item.cost} tier={item.tier.value}{status} y={name_y}")
        all_items.append((item_key, is_purchased))

# Show what we'd buy
print("\n=== Purchase plan ===")
tier_order = {ItemTier.SS: 0, ItemTier.S: 1, ItemTier.A: 2, ItemTier.B: 3}
buyable = [(item_key, is_purchased) for item_key, is_purchased in all_items if not is_purchased]
total_cost = 0
for item_key, _ in sorted(buyable, key=lambda x: tier_order.get(ITEM_CATALOGUE[x[0]].tier, 99)):
    item = ITEM_CATALOGUE[item_key]
    total_cost += item.cost
    affordable = "YES" if coins and total_cost <= coins else "NO"
    print(f"  BUY: {item.name} ({item.cost} coins, running total={total_cost}) affordable={affordable}")

print(f"\nTotal cost: {total_cost}, coins available: {coins}")

# Go back
print("\nGoing back...")
injector.tap(50, 1870)
time.sleep(2.0)
capture.stop()
