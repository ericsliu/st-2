"""Decision engine: routes game states to the appropriate decision handler."""

from __future__ import annotations

import logging

from uma_trainer.decision.event_handler import EventHandler
from uma_trainer.decision.race_selector import RaceSelector
from uma_trainer.decision.scorer import TrainingScorer
from uma_trainer.decision.skill_buyer import SkillBuyer
from uma_trainer.types import ActionType, BotAction, GameState, ScreenState

logger = logging.getLogger(__name__)


class DecisionEngine:
    """Top-level strategy coordinator.

    Routes incoming GameState to the appropriate decision handler and returns
    a BotAction.
    """

    def __init__(
        self,
        scorer: TrainingScorer,
        event_handler: EventHandler,
        skill_buyer: SkillBuyer,
        race_selector: RaceSelector,
    ) -> None:
        self.scorer = scorer
        self.event_handler = event_handler
        self.skill_buyer = skill_buyer
        self.race_selector = race_selector

    def decide(self, state: GameState) -> BotAction:
        """Return the best action for the current game state."""
        screen = state.screen

        if screen == ScreenState.TRAINING:
            action = self.scorer.best_action(state)
            logger.debug("Training decision: %s (%s)", action.action_type.value, action.reason)
            return action

        elif screen == ScreenState.EVENT:
            action = self.event_handler.decide(state)
            logger.debug("Event decision: choice=%s (%s)", action.target, action.reason)
            return action

        elif screen == ScreenState.SKILL_SHOP:
            # Returns the first action from the skill buyer queue
            actions = self.skill_buyer.decide(state)
            if actions:
                return actions[0]
            return BotAction(ActionType.SKIP_SKILL, reason="No skill actions")

        elif screen == ScreenState.RACE_ENTRY:
            return self.race_selector.decide(state)

        elif screen in (ScreenState.LOADING, ScreenState.CUTSCENE, ScreenState.RACE):
            return BotAction(ActionType.WAIT, reason=f"Passive screen: {screen.value}")

        elif screen == ScreenState.RESULT_SCREEN:
            return BotAction(
                action_type=ActionType.WAIT,
                tap_coords=(640, 400),  # Tap center to advance
                reason="Result screen — tap to advance",
            )

        elif screen == ScreenState.MAIN_MENU:
            return BotAction(ActionType.WAIT, reason="On main menu")

        else:
            logger.warning("Unknown screen state: %s", screen.value)
            return BotAction(ActionType.WAIT, reason=f"Unknown screen: {screen.value}")

    def get_skill_actions(self, state: GameState) -> list[BotAction]:
        """Return the full ordered list of skill shop actions."""
        return self.skill_buyer.decide(state)
