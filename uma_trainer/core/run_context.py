"""Persistent run context — state that lives across turns within a career run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONSECUTIVE_RACES_FILE = Path("data/consecutive_races.txt")
JUST_RACED_FILE = Path("data/just_raced.txt")
GOAL_RACE_URGENT_FILE = Path("data/goal_race_urgent.txt")


@dataclass
class RunContext:
    """State that persists across turns within a career run.

    Can be persisted to disk between script invocations (single-turn mode)
    or held in memory for the full FSM lifecycle.
    """

    consecutive_races: int = 0
    just_raced: bool = False
    goal_race_urgent: bool = False
    items_used_this_turn: list[str] = field(default_factory=list)

    def save_to_disk(self) -> None:
        """Persist state for between-invocation continuity (script mode)."""
        CONSECUTIVE_RACES_FILE.write_text(str(self.consecutive_races))
        JUST_RACED_FILE.write_text("1" if self.just_raced else "0")
        GOAL_RACE_URGENT_FILE.write_text("1" if self.goal_race_urgent else "0")

    @classmethod
    def load_from_disk(cls) -> "RunContext":
        """Load persisted state from previous invocations."""
        ctx = cls()
        if CONSECUTIVE_RACES_FILE.exists():
            try:
                ctx.consecutive_races = int(CONSECUTIVE_RACES_FILE.read_text().strip())
            except (ValueError, OSError):
                pass
        if JUST_RACED_FILE.exists():
            try:
                ctx.just_raced = JUST_RACED_FILE.read_text().strip() == "1"
            except OSError:
                pass
        if GOAL_RACE_URGENT_FILE.exists():
            try:
                ctx.goal_race_urgent = GOAL_RACE_URGENT_FILE.read_text().strip() == "1"
            except OSError:
                pass
        if ctx.consecutive_races > 0:
            logger.info("Restored consecutive race count: %d", ctx.consecutive_races)
        if ctx.just_raced:
            logger.info("Restored just_raced flag — shop visit due")
        if ctx.goal_race_urgent:
            logger.info("Restored goal_race_urgent flag — must race this turn")
        return ctx

    def on_race_completed(self, scenario) -> None:
        """Update context after a race is completed."""
        scenario.on_race_completed()
        self.consecutive_races = scenario._consecutive_races
        self.just_raced = True
        self.save_to_disk()

    def on_non_race_action(self, race_selector) -> None:
        """Update context after a non-race action."""
        race_selector.on_non_race_action()
        self.consecutive_races = 0
        self.save_to_disk()

    def clear_just_raced(self) -> None:
        self.just_raced = False
        self.save_to_disk()

    def reset_turn(self) -> None:
        """Reset per-turn state."""
        self.items_used_this_turn = []
