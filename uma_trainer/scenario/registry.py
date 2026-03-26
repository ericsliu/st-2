"""Scenario registry: loads a scenario by name from YAML + handler."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from uma_trainer.scenario.base import ScenarioConfig, ScenarioHandler, parse_scenario_config

logger = logging.getLogger(__name__)

# Handler registry: scenario name -> handler class.
# Scenarios without a specialized handler use the base ScenarioHandler.
_HANDLER_REGISTRY: dict[str, type[ScenarioHandler]] = {}


def register_handler(name: str, handler_cls: type[ScenarioHandler]) -> None:
    """Register a scenario-specific handler class."""
    _HANDLER_REGISTRY[name] = handler_cls


def load_scenario(
    name: str,
    scenarios_dir: str = "data/scenarios",
) -> ScenarioHandler:
    """Load a scenario definition from YAML and return the appropriate handler.

    Falls back to the base ScenarioHandler if no specialized handler exists.
    Falls back to a minimal default config if no YAML file exists.
    """
    yaml_path = Path(scenarios_dir) / f"{name}.yaml"

    if yaml_path.exists():
        raw = yaml.safe_load(yaml_path.read_text()) or {}
        config = parse_scenario_config(raw)
        logger.info("Loaded scenario '%s' from %s", name, yaml_path)
    else:
        logger.warning(
            "No scenario YAML found for '%s' at %s — using defaults",
            name, yaml_path,
        )
        config = ScenarioConfig(name=name, display_name=name)

    handler_cls = _HANDLER_REGISTRY.get(name, ScenarioHandler)
    return handler_cls(config)


# Register built-in handlers on import
def _register_builtins() -> None:
    from uma_trainer.scenario.trackblazer import TrackblazerHandler
    register_handler("trackblazer", TrackblazerHandler)


_register_builtins()
