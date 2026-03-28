"""Quick test of the full perception pipeline on the current screen."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
for noisy in ("PIL", "easyocr", "httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

from uma_trainer.config import load_config
from uma_trainer.capture import get_capture_backend
from uma_trainer.perception.screen_identifier import ScreenIdentifier
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.assembler import StateAssembler

cfg = load_config("config/default.yaml")
capture = get_capture_backend(cfg.capture)
ocr = OCREngine(cfg.ocr)
screen_id = ScreenIdentifier(ocr=ocr)
assembler = StateAssembler(screen_id, ocr, cfg)

capture.start()
frame = capture.grab_frame()
print(f"Frame: {frame.shape}")

state = assembler.assemble(frame)
print(f"Screen: {state.screen}")
print(f"Turn: {state.current_turn}")
print(f"Energy: {state.energy}")
if state.stats:
    s = state.stats
    print(f"Stats: spd={s.speed} sta={s.stamina} pow={s.power} gut={s.guts} wit={s.wit}")
print(f"Mood: {state.mood}")
print(f"Tiles: {len(state.training_tiles)}")
print(f"Races: {len(state.available_races)}")
print(f"Events: {len(state.event_choices)}")
