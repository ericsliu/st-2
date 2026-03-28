"""Tier 1 decision engine: rule-based training tile scoring.

Handles ~90% of decisions. Sub-millisecond, deterministic, tunable via
config presets.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from uma_trainer.config import ScorerConfig
from uma_trainer.decision.runspec import RunSpec
from uma_trainer.types import (
    ActionType,
    BotAction,
    GameState,
    StatType,
    TrainingTile,
)

if TYPE_CHECKING:
    from uma_trainer.decision.shop_manager import ShopManager
    from uma_trainer.knowledge.overrides import OverridesLoader
    from uma_trainer.scenario.base import ScenarioHandler

logger = logging.getLogger(__name__)

# Typical stat gains per training type when OCR data is unavailable.
# Each training boosts multiple stats — the primary stat gets the largest
# gain, but secondary/tertiary stats contribute meaningful value too.
# Values are rough averages for mid-game with moderate support card stacking.
ESTIMATED_TRAINING_GAINS: dict[str, dict[str, int]] = {
    "speed":   {"speed": 12, "stamina": 0, "power": 5,  "guts": 0,  "wit": 0},
    "stamina": {"speed": 0,  "stamina": 12, "power": 0, "guts": 5,  "wit": 0},
    "power":   {"speed": 0,  "stamina": 5,  "power": 12, "guts": 0, "wit": 0},
    "guts":    {"speed": 5,  "stamina": 0,  "power": 0, "guts": 12, "wit": 0},
    "wit":     {"speed": 0,  "stamina": 0,  "power": 0, "guts": 0,  "wit": 12},
}


class TrainingScorer:
    """Scores training tiles and decides the best action for a given game state."""

    def __init__(
        self,
        config: ScorerConfig,
        overrides: "OverridesLoader | None" = None,
        scenario: "ScenarioHandler | None" = None,
        runspec: RunSpec | None = None,
        shop_manager: "ShopManager | None" = None,
    ) -> None:
        self.config = config
        self.overrides = overrides
        self.scenario = scenario
        self.runspec = runspec
        self.shop_manager = shop_manager

    # ------------------------------------------------------------------
    # Main decision entry points
    # ------------------------------------------------------------------

    def best_action(self, state: GameState) -> BotAction:
        """Return the highest-value action for the current training screen state."""
        if self.should_rest(state):
            return BotAction(
                action_type=ActionType.REST,
                reason=f"Energy too low ({state.energy} < {self.config.rest_energy_threshold})",
            )

        tiles_scored = self.score_tiles(state)
        if not tiles_scored:
            logger.warning("No training tiles found — defaulting to rest")
            return BotAction(action_type=ActionType.REST, reason="No tiles detected")

        best_tile, best_score = tiles_scored[0]

        # If top score is very low and energy penalty hasn't kicked in,
        # consider resting anyway
        if best_score < 5.0 and state.energy < 50:
            return BotAction(
                action_type=ActionType.REST,
                reason=f"Low score ({best_score:.1f}) + moderate energy ({state.energy})",
            )

        reason_parts = [f"score={best_score:.1f}", f"stat={best_tile.stat_type.value}"]
        if best_tile.is_rainbow:
            reason_parts.append("rainbow")
        if best_tile.is_gold:
            reason_parts.append("gold")
        if best_tile.support_cards:
            reason_parts.append(f"cards={len(best_tile.support_cards)}")

        return BotAction(
            action_type=ActionType.TRAIN,
            target=best_tile.stat_type.value,
            tap_coords=best_tile.tap_coords,
            reason=", ".join(reason_parts),
            tier_used=1,
        )

    def should_rest(self, state: GameState) -> bool:
        """True if energy is too low to safely train.

        NEVER rests during summer camp — use wit training for energy recovery
        and items (energy drinks) instead. Resting wastes a precious summer turn.

        Priority: summer camp check > strategy.yaml override > RunSpec > scenario > config.
        """
        # During summer camp, never rest — wit training recovers energy
        if self._is_summer_camp(state):
            return False

        threshold = self.config.rest_energy_threshold

        # Scenario definition provides the base threshold
        if self.scenario:
            threshold = self.scenario.get_rest_threshold()

        # RunSpec constraints override scenario defaults
        if self.runspec:
            threshold = self.runspec.constraints.rest_energy_threshold

        # strategy.yaml can override for live-tuning
        if self.overrides:
            strategy = self.overrides.get_strategy()
            if strategy.rest_energy_override is not None:
                threshold = strategy.rest_energy_override

        return state.energy < threshold

    def _is_summer_camp(self, state: GameState) -> bool:
        """True if the current turn is within a summer camp window."""
        if not self.scenario:
            return False
        camps = self.scenario.config.event_calendar.get("summer_camp", [])
        for window in camps:
            if window.start_turn <= state.current_turn <= window.end_turn:
                return True
        return False

    def _get_effective_weights(self, state: GameState) -> dict[str, float]:
        """Return stat weights merged with any active override weights."""
        if self.overrides is None:
            return self.config.stat_weights

        phase_checker = None
        if self.scenario:
            phase_checker = lambda phase: self.scenario.is_phase(state.current_turn, phase)

        return self.overrides.get_stat_weights(
            self.config.stat_weights, state.current_turn, state.max_turns,
            phase_checker=phase_checker,
        )

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_tiles(
        self, state: GameState
    ) -> list[tuple[TrainingTile, float]]:
        """Score all tiles and return them sorted best-first."""
        scored = [(tile, self._score_tile(tile, state)) for tile in state.training_tiles]
        return sorted(scored, key=lambda x: x[1], reverse=True)

    def _score_tile(self, tile: TrainingTile, state: GameState) -> float:
        score = 0.0

        # 0. Get item training boost (Trackblazer shop items)
        boost_mult = 1.0
        boost_zero_failure = False
        if self.shop_manager:
            from uma_trainer.decision.shop_manager import TrainingBoost
            boost = self.shop_manager.get_training_boost(state)
            boost_mult = boost.multiplier
            boost_zero_failure = boost.zero_failure

        # 1. Base stat value — RunSpec piecewise utility if available, else flat weights
        #    Item boost (Megaphones, Ankle Weights) scales the effective gain.
        if self.runspec and tile.stat_gains:
            for stat_name, gain in tile.stat_gains.items():
                current = state.stats.get(StatType(stat_name))
                score += self.runspec.stat_utility(
                    stat_name, current, int(gain * boost_mult),
                )
        elif self.runspec:
            estimated = ESTIMATED_TRAINING_GAINS.get(tile.stat_type.value, {})
            for stat_name, gain in estimated.items():
                if gain > 0:
                    current = state.stats.get(StatType(stat_name))
                    score += self.runspec.stat_utility(
                        stat_name, current, int(gain * boost_mult),
                    )
        else:
            weights = self._get_effective_weights(state)
            estimated = ESTIMATED_TRAINING_GAINS.get(tile.stat_type.value, {})
            for stat_name, gain in estimated.items():
                if gain > 0:
                    stat_weight = weights.get(stat_name, 1.0)
                    score += stat_weight * int(gain * boost_mult) * 0.7

        # 2. Support card stacking bonus
        score += len(tile.support_cards) * self.config.card_stack_per_card * 5.0

        # 3. Rainbow/gold indicators — the stat gains already reflect the
        #    boosted values from the preview, so we only add a small flat
        #    bonus for the "this is an unusually good turn" signal rather
        #    than multiplying (which would double-count the inflated gains).
        if tile.is_rainbow:
            score += 8.0
        elif tile.is_gold:
            score += 4.0

        # 4. Hint bonus — hints unlock skills AND boost friendship gauge increase.
        #    Base value for skill unlock, plus friendship amplification in step 6.
        if tile.has_hint:
            score += self.config.hint_bonus * 10.0

        # 5. Director bonus (special NPC that boosts training)
        if tile.has_director:
            score += 6.0

        # 6. Bond-building priority: maximize friendship before Classic summer camp.
        #    Friendship activates at bond >= 80. The bonus scales with urgency
        #    as the deadline approaches (turn 36 = Classic year summer camp).
        bond_deadline = self._get_friendship_deadline(state)
        turns_left = max(1, bond_deadline - state.current_turn)
        if state.current_turn < bond_deadline:
            low_bond_cards = [
                c for c in tile.support_cards
                if self._get_card_bond(c, state) < 80
            ]
            # Urgency: bonus ramps from ~4 to ~12 as deadline nears
            urgency = min(3.0, bond_deadline / turns_left)
            bond_score = len(low_bond_cards) * 4.0 * urgency

            # Hint icon means extra friendship gauge increase on this tile,
            # so low-bond cards benefit more from hint training.
            if tile.has_hint and low_bond_cards:
                bond_score *= 1.5

            score += bond_score

        # 7. Summer camp wit energy recovery bonus.
        #    During summer camp, wit training recovers ~5 energy per use.
        #    When energy is low, this makes wit significantly more valuable
        #    because resting wastes a precious summer turn.
        if self._is_summer_camp(state) and tile.stat_type == StatType.WIT:
            if state.energy < 50:
                score += 15.0  # Strong push toward wit when energy is critical
            elif state.energy < 80:
                score += 8.0   # Moderate push when energy is moderate

        # 8. Mood multiplier (good/great moods boost value of training)
        score *= state.mood.multiplier

        # 9. Energy penalty — high-risk tile if energy is moderate-low.
        #    During summer camp, suppress this penalty — we don't want energy
        #    concerns to penalize training when we should be maxing gains.
        if state.energy < self.config.energy_penalty_threshold and not self._is_summer_camp(state):
            score -= (self.config.energy_penalty_threshold - state.energy) * 0.5

        # 9. Failure rate penalty (Good-Luck Charm zeroes this out)
        effective_failure = 0.0 if boost_zero_failure else tile.failure_rate
        if effective_failure > 0:
            if self.runspec:
                penalty = self.runspec.policy.failure_risk_penalty
                score *= (1.0 - effective_failure * penalty)
                if effective_failure > self.runspec.constraints.max_failure_rate:
                    score *= 0.1
            else:
                score *= (1.0 - effective_failure * 0.5)

        return max(0.0, score)

    def has_high_bond_urgency(self, state: GameState) -> bool:
        """True if the best training tile has significant bond-building value.

        Used by the strategy engine to override non-goal races when friendship
        building is critical (hint tiles with low-bond cards present).
        """
        bond_deadline = self._get_friendship_deadline(state)
        if state.current_turn >= bond_deadline:
            return False

        turns_left = max(1, bond_deadline - state.current_turn)
        urgency = min(3.0, bond_deadline / turns_left)

        for tile in state.training_tiles:
            low_bond_cards = [
                c for c in tile.support_cards
                if self._get_card_bond(c, state) < 80
            ]
            if not low_bond_cards:
                continue
            # A tile with hint + low-bond cards is high-urgency (hint boosts
            # friendship gain). Without hint, need 3+ low-bond cards.
            if tile.has_hint and len(low_bond_cards) >= 1:
                return True
            if len(low_bond_cards) >= 3:
                return True

        return False

    # Default friendship deadline: Classic year summer camp (turn 36).
    # Used when no scenario calendar is available.
    _DEFAULT_FRIENDSHIP_DEADLINE = 36

    def _get_friendship_deadline(self, state: GameState) -> int:
        """Turn by which all support cards should be at friendship (bond 80).

        Uses the second summer_camp window (Classic year) from the scenario
        calendar if available, otherwise falls back to turn 36.
        """
        if self.scenario:
            camps = self.scenario.config.event_calendar.get("summer_camp", [])
            if len(camps) >= 2:
                return camps[1].start_turn
        return self._DEFAULT_FRIENDSHIP_DEADLINE

    def _get_card_bond(self, card_id: str, state: GameState) -> int:
        """Look up bond level for a card from state.support_cards."""
        for card in state.support_cards:
            if card.card_id == card_id:
                return card.bond_level
        return 0  # Unknown card defaults to 0

    # ------------------------------------------------------------------
    # Preset management
    # ------------------------------------------------------------------

    def apply_preset(self, preset: dict) -> None:
        """Update scorer weights from a loaded training preset dict."""
        if "stat_weights" in preset:
            self.config.stat_weights.update(preset["stat_weights"])
        if "energy_penalty_threshold" in preset:
            self.config.energy_penalty_threshold = preset["energy_penalty_threshold"]
        if "rest_energy_threshold" in preset:
            self.config.rest_energy_threshold = preset["rest_energy_threshold"]
        if "bond_priority_turns" in preset:
            self.config.bond_priority_turns = preset["bond_priority_turns"]
        logger.info("Applied preset: %s", preset.get("name", "unnamed"))
