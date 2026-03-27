"""Test the training tile scan pipeline end-to-end.

Requires: ADB connected to MuMuPlayer (adb connect 127.0.0.1:5555)
Starting state: either the career home (turn action) screen or
                the stat selection screen.

If on career home, taps Training to navigate to stat selection first.
Then scans all 5 tiles and prints gains + failure rates.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.action.adb_client import ADBClient
from uma_trainer.action.input_injector import InputInjector
from uma_trainer.action.sequences import ActionSequences
from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.perception.assembler import StateAssembler
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.screen_identifier import ScreenIdentifier
from uma_trainer.types import ScreenState

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)-30s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_training_scan")

DEVICE = "127.0.0.1:5555"


def main():
    # Build minimal config
    config = AppConfig(
        capture=CaptureConfig(device_serial=DEVICE),
    )

    # Init components
    capture = ScrcpyCapture(config.capture)
    capture.start()

    ocr = OCREngine(config.ocr)
    screen_id = ScreenIdentifier(ocr=ocr)
    assembler = StateAssembler(screen_id, ocr, config)

    adb = ADBClient(device_serial=DEVICE)
    injector = InputInjector(adb, config)
    sequences = ActionSequences(injector)

    # Step 1: capture and identify screen
    logger.info("=== Step 1: Identify current screen ===")
    frame = capture.grab_frame()
    state = assembler.assemble(frame)
    logger.info("Current screen: %s", state.screen.value)

    # Step 2: navigate to stat selection if needed
    if state.screen == ScreenState.TRAINING and not state.training_tiles:
        logger.info("=== Step 2: On career home, tapping Training ===")
        injector.tap(510, 1525)  # Training button
        import time
        time.sleep(2.0)

        frame = capture.grab_frame()
        state = assembler.assemble(frame)
        logger.info("After tap — screen: %s, tiles: %d",
                     state.screen.value, len(state.training_tiles))

    if not state.training_tiles:
        logger.error(
            "Not on stat selection screen (screen=%s, tiles=%d). "
            "Navigate to the training stat selection screen and re-run.",
            state.screen.value, len(state.training_tiles),
        )
        capture.stop()
        return 1

    # Step 3: scan all tiles
    logger.info("=== Step 3: Scanning all %d training tiles ===", len(state.training_tiles))
    sequences.scan_training_gains(state, capture, assembler)

    # Step 4: print results
    logger.info("=== Results ===")
    print()
    print("=" * 70)
    print(f"{'Tile':<10} {'Gains':<45} {'Total':>6} {'Fail%':>6}")
    print("-" * 70)

    for tile in state.training_tiles:
        if tile.stat_gains:
            gains_str = ", ".join(
                f"{s}: +{g}" for s, g in tile.stat_gains.items() if g > 0
            )
            total = sum(tile.stat_gains.values())
        else:
            gains_str = "(no data)"
            total = 0

        fail_pct = f"{tile.failure_rate * 100:.0f}%" if tile.failure_rate > 0 else "0%"

        print(f"{tile.stat_type.value:<10} {gains_str:<45} {total:>6} {fail_pct:>6}")

    print("=" * 70)
    print()

    # Summary
    scanned = sum(1 for t in state.training_tiles if t.stat_gains)
    logger.info("Scanned %d/%d tiles successfully", scanned, len(state.training_tiles))

    capture.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
