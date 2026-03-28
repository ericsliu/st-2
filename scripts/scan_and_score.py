"""Scan all training tiles and show scores.

Works from either career home OR stat selection screen.
Default is dry-run (read-only). Pass --execute to confirm training.

SAFETY: Never taps blindly. All taps require confirmed screen state.
"""

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
from uma_trainer.decision.scorer import TrainingScorer, ESTIMATED_TRAINING_GAINS
from uma_trainer.perception.assembler import StateAssembler
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.screen_identifier import ScreenIdentifier
from uma_trainer.types import ScreenState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-30s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scan_score")

DEVICE = "127.0.0.1:5555"
EXECUTE = "--execute" in sys.argv


def abort(capture, msg):
    """Log error and exit without tapping anything."""
    logger.error(msg)
    capture.stop()
    return 1


def main():
    config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))

    capture = ScrcpyCapture(config.capture)
    capture.start()

    ocr = OCREngine(config.ocr)
    screen_id = ScreenIdentifier(ocr=ocr)
    assembler = StateAssembler(screen_id, ocr, config)
    adb = ADBClient(device_serial=DEVICE)
    injector = InputInjector(adb, config)
    sequences = ActionSequences(injector)
    scorer = TrainingScorer(config.scorer)

    # ── Identify screen (read-only) ─────────────────────────────────
    logger.info("Identifying screen (no taps)...")
    frame = capture.grab_frame()
    state = assembler.assemble(frame)
    is_stat_select = screen_id.is_stat_selection(frame)

    logger.info("Screen: %s, stat_selection: %s, energy: %d, tiles: %d",
                state.screen.value, is_stat_select, state.energy,
                len(state.training_tiles))

    if state.screen != ScreenState.TRAINING:
        return abort(capture,
                     f"Not on training screen (got {state.screen.value}). "
                     "No taps issued.")

    # ── Navigate to stat selection if on career home ─────────────────
    if is_stat_select and state.training_tiles:
        logger.info("Already on stat selection")
    elif not is_stat_select:
        logger.info("On career home — tapping Training button")
        injector.tap(510, 1525)
        time.sleep(2.0)

        frame = capture.grab_frame()
        state = assembler.assemble(frame)
        is_stat_select = screen_id.is_stat_selection(frame)

        if not is_stat_select or not state.training_tiles:
            return abort(capture,
                         "Failed to reach stat selection. No further taps.")
    else:
        return abort(capture,
                     "Stat selection detected but 0 tiles. No taps issued.")

    # ── Scan tiles (safe) ────────────────────────────────────────────
    logger.info("Scanning all training tiles...")
    sequences.scan_training_gains(state, capture, assembler)

    # ── Score and display ────────────────────────────────────────────
    scored = scorer.score_tiles(state)

    print()
    print("=" * 80)
    print(f"{'Tile':<10} {'Gains':<45} {'Total':>5} {'Fail%':>6} {'Score':>8}")
    print("-" * 80)

    for tile, score in scored:
        if tile.stat_gains:
            gains_str = ", ".join(
                f"{s}:+{g}" for s, g in tile.stat_gains.items() if g > 0
            )
            total = sum(tile.stat_gains.values())
        else:
            gains_str = "(estimated)"
            total = sum(
                ESTIMATED_TRAINING_GAINS.get(tile.stat_type.value, {}).values()
            )
        fail_pct = (f"{tile.failure_rate * 100:.0f}%"
                    if tile.failure_rate > 0 else "0%")
        print(f"{tile.stat_type.value:<10} {gains_str:<45} "
              f"{total:>5} {fail_pct:>6} {score:>8.1f}")

    print("=" * 80)

    best_tile, best_score = scored[0]
    print(f"\nBest: {best_tile.stat_type.value} (score={best_score:.1f})")

    if not EXECUTE:
        print("\n[DRY RUN] Pass --execute to confirm training.")
        capture.stop()
        return 0

    # ── Execute ──────────────────────────────────────────────────────
    currently_raised = assembler.detect_selected_tile(capture.grab_frame())
    if currently_raised != best_tile.position:
        logger.info("Selecting %s", best_tile.stat_type.value)
        injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
        time.sleep(0.8)

    logger.info("Confirming %s", best_tile.stat_type.value)
    injector.tap(best_tile.tap_coords[0], best_tile.tap_coords[1])
    time.sleep(3.0)

    frame = capture.grab_frame()
    result_state = assembler.assemble(frame)
    logger.info("Result — screen: %s", result_state.screen.value)

    capture.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
