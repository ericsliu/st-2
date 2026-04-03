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

        # 0. Pre-summer energy management — must save energy for Summer Camp.
        #    Early Jun (1 turn before camp): rest if energy < 50
        #    Late Jun (camp starts next turn): rest if energy < 80
        summer_camp = self.get_event_turns("summer_camp")
        turns_to_camp = self.turns_until_event("summer_camp", turn)
        if turns_to_camp == 2 and energy < 50:
            logger.info("Pre-summer (2 turns out), energy %d%% < 50 — no racing", energy)
            return None
        if turns_to_camp == 1 and energy < 80:
            logger.info("Pre-summer (1 turn out), energy %d%% < 80 — no racing", energy)
            return None

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

        # 3. Result Pts urgency — if behind schedule, race aggressively.
        #    Formula: urgent if missing > turns_left * 70 pts.
        #    BUT: never race during summer camp — those turns are the most
        #    valuable training turns in the entire run.
        in_summer = turn in summer_camp
        rp_deficit = self._result_pts_deficit(state)
        if rp_deficit > 0 and not in_summer:
            total_turns_left = state.max_turns - turn
            if rp_deficit > total_turns_left * 70:
                logger.info(
                    "Result Pts urgent: %d pts behind, %d turns left (threshold %d)",
                    rp_deficit, total_turns_left, total_turns_left * 70,
                )
                return BotAction(
                    action_type=ActionType.RACE,
                    tap_coords=races_btn,
                    reason=f"Result Pts deficit {rp_deficit}, {total_turns_left} turns left",
                    tier_used=1,
                )

        # 4. Race rhythm: every N turns (configurable).
        #    Skip during summer camp — training is far more valuable.
        if turn % race_cfg.race_interval == 0 and not in_summer:
            return BotAction(
                action_type=ActionType.RACE,
                tap_coords=races_btn,
                reason=f"Trackblazer rhythm (every {race_cfg.race_interval} turns)",
                tier_used=1,
            )

        # 5. Energy too low to train effectively — might as well race.
        #    During summer camp, use wit training for energy recovery instead.
        if energy < 30 and not in_summer:
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

    def _result_pts_deficit(self, state: "GameState") -> int:
        """How many Result Pts behind the current year's target.

        Uses OCR'd values from the turn action screen. If target isn't
        available, falls back to the scenario config grade point targets.
        """
        if state.result_pts_target > 0:
            return max(0, state.result_pts_target - state.result_pts)

        # Fallback: use scenario config targets
        year = self.current_year(state.current_turn)
        surface = "turf"
        target = self.get_grade_point_target(year, surface)
        if target > 0:
            return max(0, target // 3)
        return 0

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
        """Single-item compat — returns first item from the queue."""
        queue = self.get_item_queue(state, inventory)
        return queue[0] if queue else None

    def get_item_queue(
        self, state: "GameState", inventory: dict[str, int],
    ) -> list[BotAction]:
        """Plan a queue of items to use this turn.

        Plans items as combos — e.g. Ankle Weights are only queued if
        energy is sufficient or a Vita drink is available to pair with them.
        Items are returned in execution order.

        Priority:
        1. Vita for energy recovery (queued first so energy is restored
           before training-boost items increase energy cost)
        2. Megaphones — at Summer Camp start
        3. Ankle Weights — stack at Summer Camp (only if energy is safe)
        4. Reset Whistle — rearrange cards at Summer Camp start
        5. Good-Luck Charm — before exceptional training
        6. Master Cleat Hammer — during Twinkle Star Climax
        """
        turn = state.current_turn
        queue: list[BotAction] = []

        # Track remaining inventory as we plan (so we don't double-spend)
        remaining = dict(inventory)

        def _has(key: str) -> bool:
            return remaining.get(key, 0) > 0

        def _reserve(key: str) -> None:
            remaining[key] = remaining.get(key, 0) - 1

        def _best_vita() -> str | None:
            for k in ("vita_65", "vita_40", "vita_20"):
                if _has(k):
                    return k
            return None

        summer_camp = self.get_event_turns("summer_camp")
        in_summer = turn in summer_camp
        at_camp_start = in_summer and self.is_event_start("summer_camp", turn)
        turns_left = state.max_turns - turn if state.max_turns else 999
        in_finale = turns_left <= 3

        # ── Reset Whistle — rearranges cards, invalidates all other plans ──
        # Used during summer camp or final 3 turns. The caller handles whistle
        # separately: uses it first if training tiles are lacking, then re-scans
        # before using boost items.
        if (in_summer or in_finale) and _has("reset_whistle"):
            queue.append(BotAction(
                action_type=ActionType.USE_ITEM,
                target="reset_whistle",
                reason=f"Reset Whistle ({'finale' if in_finale else 'summer camp'}, {turns_left} turns left)",
                tier_used=1,
            ))
            _reserve("reset_whistle")

        # ── Determine if we need energy recovery ──
        # During summer: use Vita at energy < 50 (never rest)
        # Outside summer: use Vita at energy < 30
        vita_threshold = 50 if in_summer else 30
        needs_energy = state.energy < vita_threshold

        # ── Plan Ankle Weights (summer camp) ──
        # Ankle Weights increase energy cost by 20%, so we need to ensure
        # energy is safe. If energy is low, pair with a Vita drink.
        want_weights = in_summer and _has("ankle_weights")
        weights_energy_safe = state.energy >= 40  # enough to absorb +20% cost

        if want_weights and not weights_energy_safe:
            # Need a Vita to pair — check if one exists
            vita_key = _best_vita()
            if vita_key:
                # Queue Vita FIRST, then Ankle Weights
                queue.append(BotAction(
                    action_type=ActionType.USE_ITEM,
                    target=vita_key,
                    reason=f"Vita before Ankle Weights (energy={state.energy})",
                    tier_used=1,
                ))
                _reserve(vita_key)
                needs_energy = False  # Vita covers our energy need
            else:
                # No Vita available — skip Ankle Weights entirely
                logger.info(
                    "Skipping Ankle Weights: energy=%d, no Vita available",
                    state.energy,
                )
                want_weights = False

        # ── Queue Vita for energy recovery (if still needed) ──
        if needs_energy:
            vita_key = _best_vita()
            if vita_key:
                queue.append(BotAction(
                    action_type=ActionType.USE_ITEM,
                    target=vita_key,
                    reason=f"Vita for energy recovery (energy={state.energy})",
                    tier_used=1,
                ))
                _reserve(vita_key)

        # ── Queue Megaphone during Summer Camp ──
        # Use at first opportunity during summer camp (multi-turn effect)
        if in_summer:
            for mega_key in ("empowering_mega", "motivating_mega"):
                if _has(mega_key):
                    queue.append(BotAction(
                        action_type=ActionType.USE_ITEM,
                        target=mega_key,
                        reason=f"Megaphone at Summer Camp (turn {turn})",
                        tier_used=1,
                    ))
                    _reserve(mega_key)
                    break

        # ── Queue Ankle Weights (validated above) ──
        if want_weights:
            queue.append(BotAction(
                action_type=ActionType.USE_ITEM,
                target="ankle_weights",
                reason="Ankle Weights stacked at Summer Camp",
                tier_used=1,
            ))
            _reserve("ankle_weights")

        # ── Good-Luck Charm — during summer camp or before exceptional training ──
        if _has("good_luck_charm"):
            if in_summer:
                queue.append(BotAction(
                    action_type=ActionType.USE_ITEM,
                    target="good_luck_charm",
                    reason=f"Charm at Summer Camp (turn {turn})",
                    tier_used=1,
                ))
                _reserve("good_luck_charm")
            else:
                best_gain = self._best_training_gain(state)
                if best_gain >= self.config.shop.exceptional_gain_threshold:
                    queue.append(BotAction(
                        action_type=ActionType.USE_ITEM,
                        target="good_luck_charm",
                        reason=f"Charm before exceptional training (gain={best_gain})",
                        tier_used=1,
                    ))
                    _reserve("good_luck_charm")

        # ── Master Cleat Hammer — during Twinkle Star Climax ──
        twinkle_star = self.get_event_turns("twinkle_star")
        if turn in twinkle_star and _has("master_hammer"):
            queue.append(BotAction(
                action_type=ActionType.USE_ITEM,
                target="master_hammer",
                reason="Master Hammer for Twinkle Star Climax",
                tier_used=1,
            ))
            _reserve("master_hammer")

        return queue

    @staticmethod
    def _best_training_gain(state: "GameState") -> int:
        if not state.training_tiles:
            return 0
        return max(t.total_stat_gain for t in state.training_tiles)
