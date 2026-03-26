"""Trackblazer scenario handler.

Implements Trackblazer-specific mechanics:
- Shop system (purchase timing, item usage with event calendar)
- Grade Point racing (aggressive race cadence, fatigue chains)
- Event calendar integration (Summer Camp, Twinkle Star Climax)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from uma_trainer.scenario.base import ScenarioHandler
from uma_trainer.types import ActionType, BotAction, Mood

if TYPE_CHECKING:
    from uma_trainer.scenario.base import ScenarioConfig
    from uma_trainer.types import GameState

logger = logging.getLogger(__name__)


class TrackblazerHandler(ScenarioHandler):
    """Scenario handler for Trackblazer (~30 races, shop, Grade Points)."""

    def __init__(self, config: "ScenarioConfig") -> None:
        super().__init__(config)
        self._consecutive_races: int = 0
        self._just_raced: bool = False

    # ------------------------------------------------------------------
    # Race decisions
    # ------------------------------------------------------------------

    def should_race_this_turn(
        self, state: "GameState", races_btn: tuple[int, int],
    ) -> BotAction | None:
        """Trackblazer race-vs-train decision.

        Priority order:
        1. Fatigue check — break chain after safe_race_chain consecutive races
        2. First few turns — train to build base stats
        3. Grade Points urgency — must race if behind target
        4. G1 race window — always race (handled by caller's goal check)
        5. Default rhythm — race every race_interval turns
        6. Low energy — race instead of wasting a train turn
        """
        turn = state.current_turn
        energy = state.energy
        race_cfg = self.config.race

        # 1. Fatigue management: break after safe_race_chain consecutive races
        #    Exception: year-end turns (fatigue can't trigger after Late Dec)
        if (
            self._consecutive_races >= race_cfg.safe_race_chain
            and not self.is_year_end(turn)
        ):
            logger.debug(
                "Race chain at %d — taking a break", self._consecutive_races,
            )
            self._consecutive_races = 0
            return None

        # 2. First few turns: build base stats and bonds
        if turn < race_cfg.skip_early_turns:
            return None

        # 3. Grade Point urgency — if behind schedule, race aggressively
        gp_deficit = self._grade_point_deficit(state)
        if gp_deficit > 0:
            turns_left = self.turns_left_in_year(turn)
            if turns_left <= 12 and gp_deficit > 60:
                logger.info(
                    "Grade Points: %d behind with %d turns left — must race",
                    gp_deficit, turns_left,
                )
                return BotAction(
                    action_type=ActionType.RACE,
                    tap_coords=races_btn,
                    reason=f"GP deficit {gp_deficit}, {turns_left} turns left",
                    tier_used=1,
                )

        # 4. Race rhythm: every N turns (configurable)
        if turn % race_cfg.race_interval == 0:
            return BotAction(
                action_type=ActionType.RACE,
                tap_coords=races_btn,
                reason=f"Trackblazer rhythm (every {race_cfg.race_interval} turns)",
                tier_used=1,
            )

        # 5. Energy too low to train effectively — might as well race
        if energy < 30:
            return BotAction(
                action_type=ActionType.RACE,
                tap_coords=races_btn,
                reason=f"Low energy ({energy}) — race instead of train",
                tier_used=1,
            )

        return None

    def on_race_completed(self) -> None:
        self._consecutive_races += 1
        self._just_raced = True

    def on_non_race_action(self) -> None:
        self._consecutive_races = 0

    def _grade_point_deficit(self, state: "GameState") -> int:
        """How many Grade Points behind the current year's target."""
        year = self.current_year(state.current_turn)
        # TODO: read preferred_surface from overrides
        surface = "turf"
        target = self.get_grade_point_target(year, surface)
        # TODO: OCR Grade Points from the turn action screen
        # For now, return a moderate deficit to encourage racing.
        return max(0, target // 3)

    # ------------------------------------------------------------------
    # Shop decisions
    # ------------------------------------------------------------------

    def should_visit_shop(self, state: "GameState") -> bool:
        """True if the bot should tap the Shop button this turn."""
        shop_cfg = self.config.shop

        # Shop doesn't open until after the debut race
        if state.current_turn < shop_cfg.unlock_turn:
            return False

        # Visit on refresh turns
        if state.current_turn % shop_cfg.refresh_interval == 0:
            return True

        # Visit after a race victory (new items may have been added)
        if self._just_raced:
            self._just_raced = False
            return True

        # Visit if bad condition (might have a cure)
        if state.mood in (Mood.BAD, Mood.TERRIBLE):
            return True

        return False

    def get_item_to_use(
        self, state: "GameState", inventory: dict[str, int],
    ) -> BotAction | None:
        """Check if any owned item should be used this turn.

        Timing rules:
        1. Condition cures — use immediately
        2. Good-Luck Charm — before exceptional training
        3. Megaphones — at Summer Camp start
        4. Ankle Weights — stack with Megaphone at Summer Camp
        5. Master Cleat Hammer — during Twinkle Star Climax
        6. Vita items — when low energy + good training available
        """
        turn = state.current_turn

        def _has(key: str) -> bool:
            return inventory.get(key, 0) > 0

        # 1. Condition cures — TODO: detect specific conditions

        # 2. Good-Luck Charm — before exceptional training
        if _has("good_luck_charm"):
            best_gain = self._best_training_gain(state)
            if best_gain >= self.config.shop.exceptional_gain_threshold:
                return BotAction(
                    action_type=ActionType.USE_ITEM,
                    target="good_luck_charm",
                    reason=f"Charm before exceptional training (gain={best_gain})",
                    tier_used=1,
                )

        # 3. Megaphones — at Summer Camp start
        summer_camp = self.get_event_turns("summer_camp")
        if turn in summer_camp and self.is_event_start("summer_camp", turn):
            for mega_key in ("empowering_mega", "motivating_mega", "coaching_mega"):
                if _has(mega_key):
                    return BotAction(
                        action_type=ActionType.USE_ITEM,
                        target=mega_key,
                        reason=f"Megaphone at Summer Camp start (turn {turn})",
                        tier_used=1,
                    )

        # 4. Ankle Weights — stack with Megaphone at Summer Camp
        if turn in summer_camp and _has("ankle_weights"):
            return BotAction(
                action_type=ActionType.USE_ITEM,
                target="ankle_weights",
                reason="Ankle Weights stacked at Summer Camp",
                tier_used=1,
            )

        # 5. Master Cleat Hammer — during Twinkle Star Climax
        twinkle_star = self.get_event_turns("twinkle_star")
        if turn in twinkle_star and _has("master_hammer"):
            return BotAction(
                action_type=ActionType.USE_ITEM,
                target="master_hammer",
                reason="Master Hammer for Twinkle Star Climax",
                tier_used=1,
            )

        # 6. Vita items — low energy + decent training
        if state.energy < 30 and state.training_tiles:
            best_gain = self._best_training_gain(state)
            if best_gain >= 20:
                for vita_key in ("vita_65", "vita_40", "vita_20"):
                    if _has(vita_key):
                        return BotAction(
                            action_type=ActionType.USE_ITEM,
                            target=vita_key,
                            reason=f"Vita for low-energy training (gain={best_gain})",
                            tier_used=1,
                        )

        return None

    @staticmethod
    def _best_training_gain(state: "GameState") -> int:
        if not state.training_tiles:
            return 0
        return max(t.total_stat_gain for t in state.training_tiles)
