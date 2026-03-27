"""Dry-run evaluation: capture screen, assemble state, score tiles.

Does NOT tap or execute any action. Use this to inspect the bot's
decision-making and tune parameters.

Usage:
    .venv/bin/python scripts/dry_run.py              # snapshot current screen
    .venv/bin/python scripts/dry_run.py --scan       # tap tiles to scan gains (reads only)
    .venv/bin/python scripts/dry_run.py --scan --go  # scan + go to stat selection first
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.action.adb_client import ADBClient
from uma_trainer.action.input_injector import InputInjector
from uma_trainer.action.sequences import ActionSequences
from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig, load_config
from uma_trainer.decision.runspec import load_runspec
from uma_trainer.decision.scorer import TrainingScorer, ESTIMATED_TRAINING_GAINS
from uma_trainer.decision.shop_manager import ShopManager
from uma_trainer.perception.assembler import StateAssembler
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.screen_identifier import ScreenIdentifier
from uma_trainer.scenario.registry import load_scenario
from uma_trainer.types import ScreenState, StatType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-30s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dry_run")

DEVICE = "127.0.0.1:5555"


def print_state_summary(state):
    """Print a compact view of the assembled game state."""
    print()
    print("=" * 75)
    print(f"  Screen: {state.screen.value}")
    print(f"  Turn:   {state.current_turn} / {state.max_turns}")
    print(f"  Energy: {state.energy}   Mood: {state.mood.value}")
    s = state.stats
    print(f"  Stats:  Spd={s.speed} Sta={s.stamina} Pow={s.power} Gut={s.guts} Wit={s.wit} (total={s.total()})")
    if state.support_cards:
        print(f"  Bonds:  {', '.join(f'{c.name}={c.bond_level}' for c in state.support_cards)}")
    if state.training_tiles:
        print(f"  Tiles:  {len(state.training_tiles)} detected")
    if state.event_text:
        print(f"  Event:  {state.event_text[:60]}...")
    print("=" * 75)


def print_tile_scores(scored, state, scorer):
    """Print detailed per-tile scoring breakdown."""
    print()
    print(f"{'Tile':<10} {'Gains':<35} {'Tot':>4} {'Fail%':>5} {'Bond':>5} {'Score':>8}")
    print("-" * 75)

    deadline = scorer._get_friendship_deadline(state)
    turns_left = max(1, deadline - state.current_turn)
    urgency = min(3.0, deadline / turns_left) if state.current_turn < deadline else 0.0

    for tile, score in scored:
        if tile.stat_gains:
            gains_str = ", ".join(f"{s}:+{g}" for s, g in tile.stat_gains.items() if g > 0)
            total = sum(tile.stat_gains.values())
        else:
            gains_str = "(estimated)"
            total = sum(ESTIMATED_TRAINING_GAINS.get(tile.stat_type.value, {}).values())

        fail_pct = f"{tile.failure_rate * 100:.0f}%" if tile.failure_rate > 0 else "-"

        # Count low-bond cards on this tile
        low_bond = sum(
            1 for c in tile.support_cards
            if scorer._get_card_bond(c, state) < 80
        )
        bond_str = f"+{low_bond}" if low_bond > 0 and state.current_turn < deadline else "-"

        print(f"{tile.stat_type.value:<10} {gains_str:<35} {total:>4} {fail_pct:>5} {bond_str:>5} {score:>8.1f}")

    print("-" * 75)
    print(f"  Bond urgency: {urgency:.1f}x (deadline turn {deadline}, {max(0, deadline - state.current_turn)} turns away)")

    # Show boost info if any
    if scorer.shop_manager:
        from uma_trainer.decision.shop_manager import TrainingBoost
        boost = scorer.shop_manager.get_training_boost(state)
        if boost.multiplier != 1.0 or boost.zero_failure:
            parts = []
            if boost.multiplier != 1.0:
                parts.append(f"×{boost.multiplier:.1f} gains")
            if boost.zero_failure:
                parts.append("0% failure")
            print(f"  Item boost: {', '.join(parts)}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Dry-run turn evaluation")
    parser.add_argument("--scan", action="store_true",
                        help="Tap tiles to scan gains (does NOT confirm training)")
    parser.add_argument("--go", action="store_true",
                        help="Tap Training button first (use with --scan from turn action screen)")
    parser.add_argument("--scenario", default=None,
                        help="Override scenario (default: from config)")
    parser.add_argument("--runspec", default=None,
                        help="Override runspec (default: from config)")
    args = parser.parse_args()

    config = load_config()
    config.capture.device_serial = DEVICE
    scenario_name = args.scenario or config.scenario
    runspec_name = args.runspec or config.runspec

    # Build pipeline
    capture = ScrcpyCapture(config.capture)
    capture.start()

    ocr = OCREngine(config.ocr)
    screen_id = ScreenIdentifier(ocr=ocr)
    assembler = StateAssembler(screen_id, ocr, config)
    adb = ADBClient(device_serial=DEVICE)
    injector = InputInjector(adb, config)
    sequences = ActionSequences(injector)

    scenario = load_scenario(scenario_name)
    runspec = load_runspec(runspec_name)
    shop_mgr = ShopManager(scenario=scenario)
    scorer = TrainingScorer(
        config.scorer, scenario=scenario, runspec=runspec,
        shop_manager=shop_mgr,
    )

    logger.info("Scenario: %s, RunSpec: %s", scenario_name, runspec_name)

    # Step 1: Capture and assemble
    frame = capture.grab_frame()
    state = assembler.assemble(frame)
    print_state_summary(state)

    # Step 2: Navigate to stat selection if requested
    if args.go and state.screen == ScreenState.TRAINING and not state.training_tiles:
        logger.info("Tapping Training button to enter stat selection...")
        injector.tap(510, 1525)
        time.sleep(2.0)
        frame = capture.grab_frame()
        state = assembler.assemble(frame)
        print_state_summary(state)

    # Step 3: Scan tiles if requested and tiles are present
    if args.scan and state.training_tiles:
        logger.info("Scanning %d tiles (tap to preview, no confirm)...", len(state.training_tiles))
        sequences.scan_training_gains(state, capture, assembler)

    # Step 4: Score and display
    if state.training_tiles:
        scored = scorer.score_tiles(state)
        print_tile_scores(scored, state, scorer)

        best_tile, best_score = scored[0]
        rest_rec = scorer.should_rest(state)
        if rest_rec:
            print(f"  >>> RECOMMENDATION: REST (energy {state.energy} below threshold)")
        else:
            print(f"  >>> RECOMMENDATION: Train {best_tile.stat_type.value} (score={best_score:.1f})")
        print()
    elif state.screen == ScreenState.EVENT:
        print("  Event screen detected. Choices:")
        for c in state.event_choices:
            print(f"    [{c.index}] {c.text}")
        print()
    else:
        print(f"  Screen: {state.screen.value} — no training tiles to score.")
        print()

    capture.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
