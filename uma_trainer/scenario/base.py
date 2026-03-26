"""Base scenario configuration and handler.

ScenarioConfig holds declarative data loaded from YAML.
ScenarioHandler provides default implementations for all scenario-dependent
behaviour. Scenario-specific subclasses override only what they need.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from uma_trainer.types import ActionType, BotAction

if TYPE_CHECKING:
    from uma_trainer.types import GameState

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Scenario configuration (loaded from YAML)
# ------------------------------------------------------------------

@dataclass
class PhaseRange:
    """A game phase expressed as fractions of max_turns."""
    start: float  # 0.0–1.0
    end: float    # 0.0–1.0


@dataclass
class EventWindow:
    """A calendar event expressed as absolute turn numbers."""
    start_turn: int
    end_turn: int

    def turns(self) -> set[int]:
        return set(range(self.start_turn, self.end_turn + 1))


@dataclass
class RaceConfig:
    """Race-related configuration from the scenario YAML."""
    min_score: float = 5.0
    race_interval: int = 3
    skip_early_turns: int = 5
    safe_race_chain: int = 3
    energy_cost: int = 27
    grade_points: dict[str, list[int]] = field(default_factory=dict)
    grade_value: dict[str, float] = field(default_factory=dict)
    grade_point_targets: dict[str, int] = field(default_factory=dict)
    fan_targets: dict[int, int] = field(default_factory=dict)
    shop_coins: list[int] = field(default_factory=list)


@dataclass
class ShopConfig:
    """Shop-related configuration (only for scenarios with the shop feature)."""
    refresh_interval: int = 6
    unlock_turn: int = 6
    exceptional_gain_threshold: int = 30


@dataclass
class ScenarioConfig:
    """Complete scenario definition loaded from YAML."""
    name: str = ""
    display_name: str = ""
    max_turns: int = 72
    phases: dict[str, PhaseRange] = field(default_factory=dict)
    phase_aliases: dict[str, PhaseRange] = field(default_factory=dict)
    features: list[str] = field(default_factory=list)
    available_actions: list[str] = field(default_factory=list)
    event_calendar: dict[str, list[EventWindow]] = field(default_factory=dict)
    rest_threshold: int = 20
    race: RaceConfig = field(default_factory=RaceConfig)
    shop: ShopConfig = field(default_factory=ShopConfig)


# ------------------------------------------------------------------
# YAML parsing
# ------------------------------------------------------------------

def parse_scenario_config(raw: dict) -> ScenarioConfig:
    """Parse a raw YAML dict into a ScenarioConfig."""
    cfg = ScenarioConfig(
        name=raw.get("name", ""),
        display_name=raw.get("display_name", ""),
        max_turns=raw.get("max_turns", 72),
        features=raw.get("features", []),
        available_actions=raw.get("available_actions", []),
        rest_threshold=raw.get("rest_threshold", 20),
    )

    # Phases
    for name, val in raw.get("phases", {}).items():
        cfg.phases[name] = PhaseRange(start=val["start"], end=val["end"])

    # Phase aliases
    for name, val in raw.get("phase_aliases", {}).items():
        cfg.phase_aliases[name] = PhaseRange(start=val["start"], end=val["end"])

    # Event calendar
    for event_name, windows in raw.get("event_calendar", {}).items():
        cfg.event_calendar[event_name] = [
            EventWindow(start_turn=w["start_turn"], end_turn=w["end_turn"])
            for w in windows
        ]

    # Race config
    race_raw = raw.get("race", {})
    cfg.race = RaceConfig(
        min_score=race_raw.get("min_score", 5.0),
        race_interval=race_raw.get("race_interval", 3),
        skip_early_turns=race_raw.get("skip_early_turns", 5),
        safe_race_chain=race_raw.get("safe_race_chain", 3),
        energy_cost=race_raw.get("energy_cost", 27),
        grade_points=race_raw.get("grade_points", {}),
        grade_value=race_raw.get("grade_value", {}),
        grade_point_targets={
            k: v for k, v in race_raw.get("grade_point_targets", {}).items()
        },
        fan_targets={
            int(k): v for k, v in race_raw.get("fan_targets", {}).items()
        },
        shop_coins=race_raw.get("shop_coins", []),
    )

    # Shop config
    shop_raw = raw.get("shop", {})
    if shop_raw:
        cfg.shop = ShopConfig(
            refresh_interval=shop_raw.get("refresh_interval", 6),
            unlock_turn=shop_raw.get("unlock_turn", 6),
            exceptional_gain_threshold=shop_raw.get("exceptional_gain_threshold", 30),
        )

    return cfg


# ------------------------------------------------------------------
# Base scenario handler
# ------------------------------------------------------------------

class ScenarioHandler:
    """Default scenario handler. All behaviour is derived from ScenarioConfig.

    Subclass and override specific methods for scenario-specific mechanics
    (e.g. Trackblazer shop, Grade Point racing).
    """

    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

    # -- Phase queries --------------------------------------------------

    def _turn_range(self, phase: PhaseRange) -> tuple[int, int]:
        """Convert fractional phase to integer turn range [start, end)."""
        mt = self.config.max_turns
        return (round(phase.start * mt), round(phase.end * mt))

    def phase_at(self, turn: int) -> str:
        """Return the named phase for a given turn."""
        for name, phase in self.config.phases.items():
            t_start, t_end = self._turn_range(phase)
            if t_start <= turn < t_end:
                return name
        # Last phase includes the final turn
        if self.config.phases:
            last = list(self.config.phases.keys())[-1]
            return last
        return "unknown"

    def is_phase(self, turn: int, phase_name: str) -> bool:
        """Check if a turn falls within a named phase or alias."""
        # Check aliases first
        alias = self.config.phase_aliases.get(phase_name)
        if alias:
            t_start, t_end = self._turn_range(alias)
            return t_start <= turn < t_end

        # Check direct phases
        phase = self.config.phases.get(phase_name)
        if phase:
            t_start, t_end = self._turn_range(phase)
            return t_start <= turn < t_end

        return False

    def current_year(self, turn: int) -> int:
        """Map turn to in-game year number (1-based)."""
        phase = self.phase_at(turn)
        phase_names = list(self.config.phases.keys())
        if phase in phase_names:
            return phase_names.index(phase) + 1
        return 1

    def turns_left_in_year(self, turn: int) -> int:
        """How many turns remain in the current phase/year."""
        for phase in self.config.phases.values():
            t_start, t_end = self._turn_range(phase)
            if t_start <= turn < t_end:
                return max(0, t_end - turn)
        return 0

    def is_year_end(self, turn: int) -> bool:
        """True if this turn is in the last 2 turns of a phase/year."""
        return self.turns_left_in_year(turn) <= 2

    # -- Feature queries ------------------------------------------------

    def has_feature(self, feature: str) -> bool:
        return feature in self.config.features

    # -- Event calendar -------------------------------------------------

    def get_event_turns(self, event_name: str) -> set[int]:
        """Expand an event calendar entry into a set of turns."""
        turns: set[int] = set()
        for window in self.config.event_calendar.get(event_name, []):
            turns.update(window.turns())
        return turns

    def is_event_start(self, event_name: str, turn: int) -> bool:
        """True if this turn is the first turn of an event window."""
        for window in self.config.event_calendar.get(event_name, []):
            if window.start_turn == turn:
                return True
        return False

    def turns_until_event(self, event_name: str, turn: int) -> int | None:
        """Turns until the next occurrence of an event. None if no future occurrence."""
        for window in self.config.event_calendar.get(event_name, []):
            if turn < window.start_turn:
                return window.start_turn - turn
        return None

    # -- Rest threshold -------------------------------------------------

    def get_rest_threshold(self) -> int:
        return self.config.rest_threshold

    # -- Race decisions -------------------------------------------------

    def get_race_min_score(self) -> float:
        return self.config.race.min_score

    def get_grade_points(self, grade: str) -> list[int]:
        """Grade Points awarded for each placement bucket."""
        return self.config.race.grade_points.get(grade, [])

    def get_grade_value(self, grade: str) -> float:
        """Scoring weight for a grade when choosing races."""
        return self.config.race.grade_value.get(grade, 1.0)

    def get_grade_point_target(self, year: int, surface: str) -> int:
        """Target Grade Points for a given year and surface."""
        key = f"{year},{surface}"
        return self.config.race.grade_point_targets.get(key, 0)

    def get_fan_target(self, year: int) -> int:
        return self.config.race.fan_targets.get(year, 0)

    def should_race_this_turn(self, state: "GameState", races_btn: tuple[int, int]) -> BotAction | None:
        """Scenario-specific race-vs-train decision from the turn action screen.

        Base implementation: only race for fan boosts. Override in subclasses
        for aggressive racing (e.g. Trackblazer).
        """
        return None

    def on_race_completed(self) -> None:
        """Called when a race finishes. Override for side effects."""
        pass

    def on_non_race_action(self) -> None:
        """Called when a non-race action is taken."""
        pass

    # -- Shop decisions -------------------------------------------------

    def should_visit_shop(self, state: "GameState") -> bool:
        """Whether the bot should tap the Shop button this turn.
        Default: False (most scenarios don't have a shop)."""
        return False

    def get_item_to_use(self, state: "GameState", inventory: dict[str, int]) -> BotAction | None:
        """Check if any owned item should be used this turn.
        Default: None (no item usage logic)."""
        return None

    def get_exceptional_threshold(self) -> int:
        """Stat gain threshold for exceptional training."""
        if self.has_feature("shop"):
            return self.config.shop.exceptional_gain_threshold
        return 30
