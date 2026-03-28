"""Debug bond urgency and hint detection for current screen."""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.action.adb_client import ADBClient
from uma_trainer.action.input_injector import InputInjector
from uma_trainer.action.sequences import ActionSequences
from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.decision.runspec import load_runspec
from uma_trainer.decision.scorer import TrainingScorer
from uma_trainer.decision.shop_manager import ShopManager
from uma_trainer.knowledge.overrides import OverridesLoader
from uma_trainer.perception.assembler import StateAssembler
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.screen_identifier import ScreenIdentifier
from uma_trainer.scenario.registry import load_scenario

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)-40s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("debug_bond")

DEVICE = "127.0.0.1:5555"

config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))
capture = ScrcpyCapture(config.capture)
capture.start()

ocr = OCREngine(config.ocr)
screen_id = ScreenIdentifier(ocr=ocr)
assembler = StateAssembler(screen_id, ocr, config)
adb = ADBClient(device_serial=DEVICE)
injector = InputInjector(adb, config)
sequences = ActionSequences(injector)

overrides = OverridesLoader()
scenario = load_scenario(config.scenario)
runspec = load_runspec(config.runspec)
shop_manager = ShopManager(scenario=scenario, overrides=overrides)
scorer = TrainingScorer(
    config=config.scorer, overrides=overrides,
    scenario=scenario, runspec=runspec, shop_manager=shop_manager,
)

# Navigate to stat selection
from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
train_btn = get_tap_center(TURN_ACTION_REGIONS["btn_training"])
injector.tap(*train_btn)
time.sleep(2.0)

frame = capture.grab_frame()
state = assembler.assemble(frame)
is_stat = screen_id.is_stat_selection(frame)

if not is_stat:
    print("Not on stat selection!")
    capture.stop()
    sys.exit(1)

# Scan tiles
sequences.scan_training_gains(state, capture, assembler)

# Show bond info
bond_deadline = scorer._get_friendship_deadline(state)
turns_left = max(1, bond_deadline - state.current_turn)
urgency = min(3.0, bond_deadline / turns_left)
print(f"\nBond deadline: turn {bond_deadline}")
print(f"Current turn: {state.current_turn}")
print(f"Turns left to deadline: {turns_left}")
print(f"Urgency: {urgency:.2f}")

print(f"\nSupport cards in state: {len(state.support_cards)}")
for card in state.support_cards:
    print(f"  Card {card.card_id}: bond={card.bond_level}")

print("\nTile details:")
for tile in state.training_tiles:
    low_bond = [c for c in tile.support_cards if scorer._get_card_bond(c, state) < 80]
    print(f"  {tile.stat_type.value}: cards={len(tile.support_cards)}, "
          f"low_bond={len(low_bond)}, hint={tile.has_hint}, "
          f"rainbow={tile.is_rainbow}, gold={tile.is_gold}")
    if tile.support_cards:
        for cid in tile.support_cards:
            bond = scorer._get_card_bond(cid, state)
            print(f"    card {cid}: bond={bond}")

print(f"\nhas_high_bond_urgency: {scorer.has_high_bond_urgency(state)}")

# Go back
injector.tap(95, 1875)
time.sleep(1.5)
capture.stop()
