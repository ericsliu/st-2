"""Race entry decision logic."""

from __future__ import annotations

import logging

from uma_trainer.types import ActionType, BotAction, GameState

logger = logging.getLogger(__name__)


class RaceSelector:
    """Decides whether to enter a race and which race to choose."""

    def __init__(self, kb) -> None:
        self.kb = kb

    def decide(self, state: GameState) -> BotAction:
        """Determine the best race entry action.

        Strategy:
        1. If a mandatory career goal race is available → enter it
        2. If fan count is below the next goal threshold → enter a fan race
        3. Otherwise → skip / train instead
        """
        required_race = self._find_required_race(state)
        if required_race:
            logger.info("Race: entering required goal race '%s'", required_race)
            return BotAction(
                action_type=ActionType.RACE,
                target=required_race,
                reason=f"Career goal race: {required_race}",
                tier_used=1,
            )

        # Check if we need fans for the next goal
        next_goal = self._next_incomplete_goal(state)
        if next_goal and self._needs_fan_boost(state, next_goal):
            logger.info("Race: fan boost race (need %d fans)", next_goal.required_fans)
            return BotAction(
                action_type=ActionType.RACE,
                target="any",
                reason=f"Fan boost: need {next_goal.required_fans} fans",
                tier_used=1,
            )

        # Skip — prefer training
        return BotAction(
            action_type=ActionType.SKIP_SKILL,
            reason="Skipping race — no goals require it",
            tier_used=1,
        )

    def _find_required_race(self, state: GameState) -> str | None:
        """Return a race name if a career goal race must be entered this turn."""
        for goal in state.career_goals:
            if not goal.completed and goal.race_name:
                return goal.race_name
        return None

    def _next_incomplete_goal(self, state: GameState):
        """Return the next incomplete CareerGoal, or None."""
        for goal in state.career_goals:
            if not goal.completed:
                return goal
        return None

    def _needs_fan_boost(self, state: GameState, goal) -> bool:
        """True if we're well below the fan count needed for the next goal."""
        # Heuristic: enter race if we're below 70% of the goal's fan requirement
        # We don't track current fans yet — this will be populated by assembler
        return False  # TODO: OCR current fan count
