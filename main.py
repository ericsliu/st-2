#!/usr/bin/env python3
"""Uma Trainer — Autonomous Uma Musume: Pretty Derby Bot.

Entry point for all bot operations.

Usage:
    python main.py run                  # Start autonomous Career Mode run
    python main.py dashboard            # Start web monitoring dashboard
    python main.py import-kb            # Import knowledge base JSON files
    python main.py --help
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

# Load .env before anything else
load_dotenv()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress noisy third-party loggers
    for noisy in ("PIL", "easyocr", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@click.group()
@click.version_option(version="0.1.0", prog_name="uma-trainer")
def cli() -> None:
    """Uma Trainer — Autonomous Uma Musume: Pretty Derby Bot."""


@cli.command()
@click.option("--config", default="config/default.yaml", show_default=True, help="Config file")
@click.option("--scenario", default=None, help="Scenario name (trackblazer, ura_finale, unity_cup)")
@click.option("--preset", default=None, help="Training preset name (from data/presets/)")
@click.option("--headless", is_flag=True, help="Run without web dashboard")
@click.option("--log-level", default="INFO", show_default=True)
def run(config: str, scenario: str | None, preset: str | None, headless: bool, log_level: str) -> None:
    """Start an autonomous Career Mode training run."""
    _setup_logging(log_level)
    logger = logging.getLogger("main")

    from uma_trainer.config import load_config
    cfg = load_config(config)
    if headless:
        cfg.headless = True
    if log_level:
        cfg.log_level = log_level

    # Apply preset if specified
    if preset:
        preset_path = Path(f"data/presets/{preset}.json")
        if not preset_path.exists():
            logger.error("Preset not found: %s", preset_path)
            sys.exit(1)
        import json
        preset_data = json.loads(preset_path.read_text())
        logger.info("Applying preset: %s", preset_data.get("name", preset))
    else:
        preset_data = None

    # Load scenario
    from uma_trainer.scenario import load_scenario
    scenario_name = scenario or cfg.scenario
    scenario_handler = load_scenario(scenario_name)
    logger.info("Scenario: %s (%s)", scenario_handler.config.display_name, scenario_name)

    # Build the dependency graph
    from uma_trainer.capture import get_capture_backend
    from uma_trainer.perception.screen_identifier import ScreenIdentifier
    from uma_trainer.perception.ocr import OCREngine
    from uma_trainer.perception.assembler import StateAssembler
    from uma_trainer.decision.scorer import TrainingScorer
    from uma_trainer.decision.event_handler import EventHandler
    from uma_trainer.decision.skill_buyer import SkillBuyer
    from uma_trainer.decision.race_selector import RaceSelector
    from uma_trainer.decision.shop_manager import ShopManager
    from uma_trainer.decision.strategy import DecisionEngine
    from uma_trainer.action.adb_client import ADBClient
    from uma_trainer.action.input_injector import InputInjector
    from uma_trainer.action.sequences import ActionSequences
    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.knowledge.advice_loader import AdviceLoader
    from uma_trainer.knowledge.overrides import OverridesLoader
    from uma_trainer.llm.cache import LLMCache
    from uma_trainer.llm.local_llm import OllamaClient
    from uma_trainer.llm.claude_client import ClaudeAPIClient
    from uma_trainer.fsm.machine import GameFSM

    logger.info("Initializing Uma Trainer...")

    capture = get_capture_backend(cfg.capture)
    screen_id = ScreenIdentifier(
        template_dir=cfg.regions.template_dir,
        tolerance=cfg.regions.screen_anchor_tolerance,
    )
    ocr = OCREngine(cfg.ocr)
    assembler = StateAssembler(screen_id, ocr, cfg)

    kb = KnowledgeBase(cfg.db_path, master_mdb_path=cfg.master_mdb_path)
    advice = AdviceLoader(Path("data/advice"))
    overrides = OverridesLoader(Path("data/overrides"))
    llm_cache = LLMCache(cfg.db_path, cfg.llm.cache_ttl_hours)
    local_llm = OllamaClient(cfg.llm, llm_cache, advice=advice)
    claude = ClaudeAPIClient(cfg.llm, llm_cache, advice=advice)

    scorer = TrainingScorer(cfg.scorer, overrides=overrides, scenario=scenario_handler)
    if preset_data:
        scorer.apply_preset(preset_data)

    event_handler = EventHandler(kb, local_llm, claude, overrides=overrides)
    skill_buyer = SkillBuyer(kb, scorer, overrides=overrides)
    race_selector = RaceSelector(kb, overrides=overrides, scenario=scenario_handler)
    shop_manager = ShopManager(overrides=overrides, scenario=scenario_handler)
    engine = DecisionEngine(
        scorer, event_handler, skill_buyer, race_selector, shop_manager,
        scenario=scenario_handler,
    )

    adb = ADBClient(cfg.capture.device_serial)
    injector = InputInjector(adb, cfg)
    sequences = ActionSequences(injector)

    fsm = GameFSM(capture, assembler, engine, injector, sequences, kb, cfg)

    # Start dashboard in background if not headless
    if not cfg.headless:
        import threading
        import uvicorn
        from uma_trainer.web.app import create_app
        app = create_app(cfg, fsm.status, kb=kb, advice=advice, overrides=overrides)
        dashboard_thread = threading.Thread(
            target=uvicorn.run,
            kwargs={"app": app, "host": "127.0.0.1", "port": cfg.web_port, "log_level": "warning"},
            daemon=True,
        )
        dashboard_thread.start()
        logger.info("Dashboard: http://127.0.0.1:%d", cfg.web_port)

    logger.info("Starting bot loop (Ctrl+C to stop)...")
    try:
        capture.start()
        fsm.run()
    finally:
        kb.close()
        logger.info("Bot stopped.")


@cli.command()
@click.option("--config", default="config/default.yaml", show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8080, show_default=True)
@click.option("--log-level", default="INFO", show_default=True)
def dashboard(config: str, host: str, port: int, log_level: str) -> None:
    """Start the web monitoring dashboard (standalone, no bot)."""
    _setup_logging(log_level)

    from uma_trainer.config import load_config
    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.knowledge.advice_loader import AdviceLoader
    from uma_trainer.knowledge.overrides import OverridesLoader
    from uma_trainer.web.app import create_app
    import uvicorn

    cfg = load_config(config)
    kb = KnowledgeBase(cfg.db_path)
    advice = AdviceLoader(Path("data/advice"))
    overrides_loader = OverridesLoader(Path("data/overrides"))
    app = create_app(cfg, bot_status=None, kb=kb, advice=advice, overrides=overrides_loader)

    print(f"Uma Trainer Dashboard → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


@cli.command("import-kb")
@click.option("--data", default="data", show_default=True, help="Data directory with JSON files")
@click.option("--db", default="data/uma_trainer.db", show_default=True)
@click.option("--clear", is_flag=True, help="Clear existing data before importing")
@click.option("--log-level", default="INFO", show_default=True)
def import_kb(data: str, db: str, clear: bool, log_level: str) -> None:
    """Import knowledge base JSON files into SQLite."""
    _setup_logging(log_level)

    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.knowledge.loaders import KnowledgeBaseLoader

    kb = KnowledgeBase(db)
    if clear:
        for table in ["events", "skills", "support_cards", "characters", "race_calendar"]:
            kb.execute(f"DELETE FROM {table}")
        print("Cleared existing data.")

    loader = KnowledgeBaseLoader(kb, data)
    loader.load_all()
    kb.close()


if __name__ == "__main__":
    cli()
