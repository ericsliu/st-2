"""Hot-reloading YAML override files for events and training strategy.

data/overrides/events.yaml   — Tier 0 event choice overrides
data/overrides/strategy.yaml — Stat weight, skill, energy threshold overrides

Both files are re-read whenever their mtime changes (checked every call).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event overrides
# ---------------------------------------------------------------------------

@dataclass
class EventOverride:
    """A single Tier 0 event choice override rule."""
    text_contains: str          # Case-insensitive substring match against event text
    choice: int                 # 0-based choice index to select
    note: str = ""              # Human-readable explanation
    # Optional conditions (all must be true if specified)
    energy_min: int | None = None    # Only apply if energy >= this
    energy_max: int | None = None    # Only apply if energy <= this
    turn_min: int | None = None      # Only apply if turn >= this
    turn_max: int | None = None      # Only apply if turn <= this


# ---------------------------------------------------------------------------
# Strategy overrides
# ---------------------------------------------------------------------------

@dataclass
class StatWeightOverride:
    condition: str              # "early_game" | "late_game" | "always"
    weights: dict[str, float] = field(default_factory=dict)


@dataclass
class SkillPriority:
    """A skill the bot should actively try to acquire."""
    name: str
    max_circle: int = 1   # 1 = single-circle only, 2 = allow double-circle


@dataclass
class StrategyOverrides:
    stat_weight_overrides: list[StatWeightOverride] = field(default_factory=list)
    skill_blacklist: list[str] = field(default_factory=list)
    skill_priority_list: list[SkillPriority] = field(default_factory=list)
    allow_double_circle: bool = False   # Global default; parent runs set False
    rest_energy_override: int | None = None
    energy_penalty_override: int | None = None
    bond_priority_turns_override: int | None = None
    scenario_rest_thresholds: dict[str, int] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)   # Raw YAML for the dashboard

    def is_priority_skill(self, name: str) -> SkillPriority | None:
        """Check if a skill is on the priority list (case-insensitive)."""
        name_lower = name.lower()
        for sp in self.skill_priority_list:
            if sp.name.lower() == name_lower:
                return sp
        return None

    def is_blacklisted(self, name: str) -> bool:
        name_lower = name.lower()
        return any(b.lower() in name_lower for b in self.skill_blacklist)

    def should_double_circle(self, name: str) -> bool:
        """Whether a specific skill should be double-circled.

        Only True if the global allow_double_circle is set AND the skill
        is on the priority list with max_circle >= 2.
        """
        if not self.allow_double_circle:
            return False
        sp = self.is_priority_skill(name)
        return sp is not None and sp.max_circle >= 2


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class OverridesLoader:
    """Loads and hot-reloads YAML override files."""

    EVENTS_FILENAME = "events.yaml"
    STRATEGY_FILENAME = "strategy.yaml"

    def __init__(self, overrides_dir: str = "data/overrides") -> None:
        self.overrides_dir = Path(overrides_dir)
        self.overrides_dir.mkdir(parents=True, exist_ok=True)

        self._events: list[EventOverride] = []
        self._events_mtime: float = 0.0

        self._strategy: StrategyOverrides = StrategyOverrides()
        self._strategy_mtime: float = 0.0

    # ------------------------------------------------------------------
    # Event overrides
    # ------------------------------------------------------------------

    def get_event_overrides(self) -> list[EventOverride]:
        """Return event overrides, reloading from disk if file changed."""
        self._maybe_reload_events()
        return self._events

    def match_event(
        self,
        event_text: str,
        energy: int = 100,
        turn: int = 0,
    ) -> EventOverride | None:
        """Return the first matching override for an event, or None."""
        overrides = self.get_event_overrides()
        text_lower = event_text.lower()
        for override in overrides:
            if override.text_contains.lower() not in text_lower:
                continue
            if override.energy_min is not None and energy < override.energy_min:
                continue
            if override.energy_max is not None and energy > override.energy_max:
                continue
            if override.turn_min is not None and turn < override.turn_min:
                continue
            if override.turn_max is not None and turn > override.turn_max:
                continue
            return override
        return None

    def save_event_overrides(self, overrides: list[dict]) -> None:
        """Write event overrides YAML and invalidate cache."""
        path = self.overrides_dir / self.EVENTS_FILENAME
        path.write_text(yaml.dump(overrides, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self._events_mtime = 0.0  # Force reload
        logger.info("Saved event overrides (%d rules)", len(overrides))

    def get_event_overrides_raw(self) -> list[dict]:
        """Return raw event overrides as a list of dicts (for the dashboard)."""
        self._maybe_reload_events()
        path = self.overrides_dir / self.EVENTS_FILENAME
        if path.exists():
            try:
                return yaml.safe_load(path.read_text()) or []
            except Exception:
                return []
        return []

    # ------------------------------------------------------------------
    # Strategy overrides
    # ------------------------------------------------------------------

    def get_strategy(self) -> StrategyOverrides:
        """Return strategy overrides, reloading from disk if file changed."""
        self._maybe_reload_strategy()
        return self._strategy

    def get_stat_weights(
        self,
        base_weights: dict[str, float],
        turn: int,
        max_turns: int,
        phase_checker: "Callable[[str], bool] | None" = None,
    ) -> dict[str, float]:
        """Merge base weights with any applicable override weights.

        Args:
            phase_checker: Optional function that takes a phase name
                (e.g. "early_game") and returns True if currently active.
                If not provided, falls back to fractional turn boundaries.
        """
        strategy = self.get_strategy()
        weights = dict(base_weights)

        for override in strategy.stat_weight_overrides:
            applies = False
            if override.condition == "always":
                applies = True
            elif phase_checker:
                applies = phase_checker(override.condition)
            else:
                # Fallback: fractional boundaries
                frac = turn / max(max_turns, 1)
                if override.condition == "early_game":
                    applies = frac < 0.333
                elif override.condition == "late_game":
                    applies = frac > 0.694

            if applies:
                weights.update(override.weights)

        return weights

    def save_strategy(self, data: dict) -> None:
        """Write strategy YAML and invalidate cache."""
        path = self.overrides_dir / self.STRATEGY_FILENAME
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self._strategy_mtime = 0.0  # Force reload
        logger.info("Saved strategy overrides")

    def get_strategy_raw(self) -> dict:
        """Return raw strategy YAML as dict (for the dashboard)."""
        path = self.overrides_dir / self.STRATEGY_FILENAME
        if path.exists():
            try:
                return yaml.safe_load(path.read_text()) or {}
            except Exception:
                return {}
        return {}

    # ------------------------------------------------------------------
    # Internal reload logic
    # ------------------------------------------------------------------

    def _maybe_reload_events(self) -> None:
        path = self.overrides_dir / self.EVENTS_FILENAME
        if not path.exists():
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime == self._events_mtime:
            return

        try:
            raw = yaml.safe_load(path.read_text()) or []
            self._events = [self._parse_event_override(item) for item in raw if isinstance(item, dict)]
            self._events_mtime = mtime
            logger.info("Reloaded event overrides (%d rules)", len(self._events))
        except Exception as e:
            logger.warning("Failed to parse event overrides: %s", e)

    def _maybe_reload_strategy(self) -> None:
        path = self.overrides_dir / self.STRATEGY_FILENAME
        if not path.exists():
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime == self._strategy_mtime:
            return

        try:
            raw = yaml.safe_load(path.read_text()) or {}
            self._strategy = self._parse_strategy(raw)
            self._strategy.raw = raw
            self._strategy_mtime = mtime
            logger.info("Reloaded strategy overrides")
        except Exception as e:
            logger.warning("Failed to parse strategy overrides: %s", e)

    @staticmethod
    def _parse_event_override(item: dict) -> EventOverride:
        return EventOverride(
            text_contains=str(item.get("text_contains", "")),
            choice=int(item.get("choice", 0)),
            note=str(item.get("note", "")),
            energy_min=item.get("energy_min"),
            energy_max=item.get("energy_max"),
            turn_min=item.get("turn_min"),
            turn_max=item.get("turn_max"),
        )

    @staticmethod
    def _parse_strategy(raw: dict) -> StrategyOverrides:
        s = StrategyOverrides()

        for item in raw.get("stat_weight_overrides", []):
            s.stat_weight_overrides.append(
                StatWeightOverride(
                    condition=str(item.get("condition", "always")),
                    weights={k: float(v) for k, v in item.get("weights", {}).items()},
                )
            )

        s.skill_blacklist = [str(x) for x in raw.get("skill_blacklist", [])]

        # Priority list: skills the AI will actively try to acquire
        for item in raw.get("skill_priority_list", []):
            if isinstance(item, str):
                s.skill_priority_list.append(SkillPriority(name=item))
            elif isinstance(item, dict):
                s.skill_priority_list.append(
                    SkillPriority(
                        name=str(item.get("name", "")),
                        max_circle=int(item.get("max_circle", 1)),
                    )
                )

        # Backwards compat: treat old skill_whitelist as priority list
        for name in raw.get("skill_whitelist", []):
            if not any(sp.name.lower() == str(name).lower() for sp in s.skill_priority_list):
                s.skill_priority_list.append(SkillPriority(name=str(name)))

        s.allow_double_circle = bool(raw.get("allow_double_circle", False))
        s.rest_energy_override = raw.get("rest_energy_override")
        s.energy_penalty_override = raw.get("energy_penalty_override")
        s.bond_priority_turns_override = raw.get("bond_priority_turns_override")

        # Per-scenario rest thresholds (e.g. trackblazer: 5)
        scenario_rest = raw.get("scenario_rest_thresholds", {})
        if isinstance(scenario_rest, dict):
            s.scenario_rest_thresholds = {str(k): int(v) for k, v in scenario_rest.items()}

        return s
