"""Execute one or more turns using the shared TurnExecutor.

Thin wrapper around uma_trainer.core.TurnExecutor — initializes components,
builds the engine, and delegates all game logic to the shared modules.

Default is dry-run (shows decision only). Pass --execute to act.
Pass --turns N to run N consecutive turns.

SAFETY: Screen state is confirmed before any tap. Abort = zero taps.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.action.adb_client import ADBClient
from uma_trainer.action.game_actions import GameActionExecutor
from uma_trainer.action.input_injector import InputInjector
from uma_trainer.action.sequences import ActionSequences
from uma_trainer.capture.scrcpy_capture import ScrcpyCapture
from uma_trainer.config import AppConfig, CaptureConfig
from uma_trainer.core.run_context import RunContext
from uma_trainer.core.turn_executor import TurnExecutor
from uma_trainer.perception.assembler import StateAssembler
from uma_trainer.perception.ocr import OCREngine
from uma_trainer.perception.screen_identifier import ScreenIdentifier
from uma_trainer.state.ocr_provider import OCRStateProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-30s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("do_one_turn")

DEVICE = "127.0.0.1:5555"


def parse_args():
    parser = argparse.ArgumentParser(description="Execute one turn using the real DecisionEngine")
    parser.add_argument("--execute", action="store_true", help="Actually execute (default is dry-run)")
    parser.add_argument("--turns", type=int, default=1, help="Number of turns to run")
    parser.add_argument("--force-rest", action="store_true", help="Force rest this turn")
    return parser.parse_args()


def build_engine(config: AppConfig):
    """Initialize the full DecisionEngine with all real components."""
    from uma_trainer.decision.event_handler import EventHandler
    from uma_trainer.decision.race_selector import RaceSelector
    from uma_trainer.decision.runspec import load_runspec
    from uma_trainer.decision.scorer import TrainingScorer
    from uma_trainer.decision.shop_manager import ShopManager
    from uma_trainer.decision.skill_buyer import SkillBuyer
    from uma_trainer.decision.strategy import DecisionEngine
    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.knowledge.overrides import OverridesLoader
    from uma_trainer.scenario.registry import load_scenario

    kb = KnowledgeBase(db_path=config.db_path)
    overrides = OverridesLoader()
    scenario = load_scenario(config.scenario)
    runspec = load_runspec(config.runspec)
    shop_manager = ShopManager(scenario=scenario, overrides=overrides)
    shop_manager.load_inventory()

    scorer = TrainingScorer(
        config=config.scorer,
        overrides=overrides,
        scenario=scenario,
        runspec=runspec,
        shop_manager=shop_manager,
    )
    race_selector = RaceSelector(kb=kb, overrides=overrides, scenario=scenario)
    event_handler = EventHandler(kb=kb, local_llm=None, claude_client=None, overrides=overrides)
    skill_buyer = SkillBuyer(kb=kb, scorer=scorer, overrides=overrides)
    engine = DecisionEngine(
        scorer=scorer,
        event_handler=event_handler,
        skill_buyer=skill_buyer,
        race_selector=race_selector,
        shop_manager=shop_manager,
        scenario=scenario,
    )
    return engine


def main():
    args = parse_args()
    config = AppConfig(capture=CaptureConfig(device_serial=DEVICE))

    # Initialize perception
    capture = ScrcpyCapture(config.capture)
    capture.start()
    ocr = OCREngine(config.ocr)
    screen_id = ScreenIdentifier(ocr=ocr)
    assembler = StateAssembler(screen_id, ocr, config)

    # Trainee aptitudes are now read live from Full Stats during check_conditions

    # Initialize action
    adb = ADBClient(device_serial=DEVICE)
    injector = InputInjector(adb, config)
    sequences = ActionSequences(injector)

    # Initialize state provider
    provider = OCRStateProvider(capture, assembler, screen_id)

    # Initialize game action executor
    actions = GameActionExecutor(
        injector=injector,
        sequences=sequences,
        provider=provider,
        assembler=assembler,
        screen_id=screen_id,
        ocr=ocr,
    )

    # Initialize decision engine
    engine = build_engine(config)

    # Restore run context from previous invocations
    context = RunContext.load_from_disk()
    if engine.race_selector.scenario:
        engine.race_selector.scenario._consecutive_races = context.consecutive_races
        if context.just_raced:
            engine.race_selector.scenario._just_raced = True

    logger.info("Decision engine ready (scenario=%s, runspec=%s)",
                config.scenario, config.runspec)

    # Build turn executor
    executor = TurnExecutor(
        provider=provider,
        actions=actions,
        engine=engine,
        context=context,
    )

    if not args.execute:
        logger.info("DRY RUN mode — pass --execute to act")

    try:
        for turn_num in range(1, args.turns + 1):
            logger.info("=" * 60)
            logger.info("TURN %d / %d", turn_num, args.turns)
            logger.info("=" * 60)

            success = executor.execute_turn(
                execute=args.execute,
                force_rest=args.force_rest,
            )

            if not success:
                logger.error("Turn %d failed — stopping", turn_num)
                break

            # Post-turn skill check
            if args.execute:
                executor.post_turn_skill_check()

            if turn_num < args.turns:
                logger.info("Waiting before next turn...")
                time.sleep(2.0)

        logger.info("Done — %d turn(s) completed", turn_num)
    finally:
        capture.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
