"""Test skill row parsing with fuzzy matching on the live skill screen."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.action.input_injector import InputInjector
from uma_trainer.action.adb_client import ADBClient
from uma_trainer.knowledge.skill_matcher import SkillMatcher
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
matcher = SkillMatcher()

from do_one_turn import _parse_skill_rows

# Open skill screen
print("Tapping Skills...")
injector.tap(918, 1540)
time.sleep(3.0)

for page in range(6):
    frame = capture.grab_frame()
    Image.fromarray(frame).save(f"screenshots/skill_parse_p{page}.png")
    print(f"\n=== Page {page} ===")
    skills = _parse_skill_rows(frame, ocr, skill_matcher=matcher)
    for skill in skills:
        status = "(obtained)" if skill["obtained"] else f"cost={skill['cost']}"
        hint = f" hint_lvl={skill['hint_level']}" if skill["hint_level"] else ""
        match_note = ""
        if skill["matched_name"] != skill["name"]:
            match_note = f" [OCR: '{skill['name']}']"
        print(f"  '{skill['matched_name']}' {status}{hint}{match_note}")

    if page < 5:
        injector.swipe(540, 1300, 540, 1020, duration_ms=400)
        time.sleep(2.0)

# Go back
print("\nGoing back...")
injector.tap(50, 1870)
time.sleep(2.0)
capture.stop()
