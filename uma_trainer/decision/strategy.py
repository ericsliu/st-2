"""Decision engine: routes game states to the appropriate decision handler."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from uma_trainer.decision.event_handler import EventHandler
from uma_trainer.decision.race_selector import RaceSelector
from uma_trainer.decision.scorer import TrainingScorer
from uma_trainer.decision.shop_manager import ShopManager
from uma_trainer.decision.skill_buyer import SkillBuyer
from uma_trainer.types import ActionType, BotAction, GameState, ScreenState

if TYPE_CHECKING:
    from uma_trainer.scenario.base import ScenarioHandler

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
        shop_manager: ShopManager | None = None,
        scenario: "ScenarioHandler | None" = None,
    ) -> None:
        self.scorer = scorer
        self.event_handler = event_handler
        self.skill_buyer = skill_buyer
        self.race_selector = race_selector
        self.shop_manager = shop_manager or ShopManager()
        self.scenario = scenario

    def decide(self, state: GameState) -> BotAction:
        """Return the best action for the current game state."""
        screen = state.screen

        if screen == ScreenState.TRAINING:
            # Check if an owned item should be used before acting
            if self.shop_manager:
                item_action = self.shop_manager.get_item_to_use(state)
                if item_action:
                    logger.info("Using item: %s", item_action.reason)
                    return item_action

            # Score training first so we can compare against racing
            train_action = self.scorer.best_action(state)

            # Check if we should race instead of train
            race_action = self.race_selector.should_race_this_turn(state)
            if race_action:
                is_goal_race = "Goal race" in race_action.reason
                can_train = train_action.action_type == ActionType.TRAIN

                # Exceptional training (high raw stat gains) beats
                # non-urgent racing. Goal races always take priority.
                if (
                    can_train
                    and not is_goal_race
                    and self.shop_manager.is_exceptional_training(state)
                ):
                    best_gain = self.shop_manager._best_training_gain(state)
                    logger.info(
                        "Exceptional training (gain=%d) overrides race",
                        best_gain,
                    )
                    return train_action

                # High bond urgency (hint + low-bond cards) also beats
                # non-goal rhythm races — friendship is critical.
                if (
                    can_train
                    and not is_goal_race
                    and self.scorer.has_high_bond_urgency(state)
                ):
                    logger.info(
                        "High bond urgency overrides race — friendship building"
                    )
                    return train_action

                logger.debug("Racing this turn: %s", race_action.reason)
                return race_action

            # Check if we should visit the shop
            has_shop = (
                self.scenario.has_feature("shop") if self.scenario else False
            )
            if has_shop and self.shop_manager.should_visit_shop(state):
                from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
                logger.debug("Visiting shop this turn")
                return BotAction(
                    action_type=ActionType.SHOP,
                    tap_coords=get_tap_center(TURN_ACTION_REGIONS.get(
                        "btn_shop", (0, 0, 0, 0)
                    )),
                    reason="Shop refresh — checking for items",
                    tier_used=1,
                )

            logger.debug("Training decision: %s (%s)", train_action.action_type.value, train_action.reason)
            return train_action

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
