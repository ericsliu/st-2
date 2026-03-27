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
    ) -> None:
        self.config = config
        self.overrides = overrides
        self.scenario = scenario
        self.runspec = runspec

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

        Priority: strategy.yaml override > RunSpec constraints > scenario YAML > scorer config.
        """
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

        # 1. Base stat value — RunSpec piecewise utility if available, else flat weights
        if self.runspec and tile.stat_gains:
            # Use piecewise utility for each stat gain on this tile
            for stat_name, gain in tile.stat_gains.items():
                current = state.stats.get(StatType(stat_name))
                score += self.runspec.stat_utility(stat_name, current, gain)
        elif self.runspec:
            # RunSpec available but no OCR'd stat gains — estimate from
            # the typical multi-stat distribution for this training type.
            estimated = ESTIMATED_TRAINING_GAINS.get(tile.stat_type.value, {})
            for stat_name, gain in estimated.items():
                if gain > 0:
                    current = state.stats.get(StatType(stat_name))
                    score += self.runspec.stat_utility(stat_name, current, gain)
        else:
            # Legacy: flat stat weights, applied to all stats this training boosts
            weights = self._get_effective_weights(state)
            estimated = ESTIMATED_TRAINING_GAINS.get(tile.stat_type.value, {})
            for stat_name, gain in estimated.items():
                if gain > 0:
                    stat_weight = weights.get(stat_name, 1.0)
                    score += stat_weight * gain * 0.7

        # 2. Support card stacking bonus
        score += len(tile.support_cards) * self.config.card_stack_per_card * 5.0

        # 3. Special tile indicator multipliers
        if tile.is_rainbow:
            score *= self.config.rainbow_bonus
        elif tile.is_gold:
            score *= self.config.gold_bonus

        # 4. Hint bonus (unlocks a new skill)
        if tile.has_hint:
            score += self.config.hint_bonus * 8.0

        # 5. Director bonus (special NPC that boosts training)
        if tile.has_director:
            score += 6.0

        # 6. Bond-building priority (early game: prefer tiles with low-bond cards)
        is_early = (
            self.scenario.is_phase(state.current_turn, "early_game")
            if self.scenario
            else state.is_early_game
        )
        if is_early:
            low_bond_cards = [
                c for c in tile.support_cards
                if self._get_card_bond(c, state) < 60
            ]
            score += len(low_bond_cards) * 4.0

        # 7. Mood multiplier (good/great moods boost value of training)
        score *= state.mood.multiplier

        # 8. Energy penalty — high-risk tile if energy is moderate-low
        if state.energy < self.config.energy_penalty_threshold:
            score -= (self.config.energy_penalty_threshold - state.energy) * 0.5

        # 9. Failure rate penalty
        if tile.failure_rate > 0:
            if self.runspec:
                penalty = self.runspec.policy.failure_risk_penalty
                score *= (1.0 - tile.failure_rate * penalty)
                # Hard constraint: reject tiles above max failure rate
                if tile.failure_rate > self.runspec.constraints.max_failure_rate:
                    score *= 0.1
            else:
                score *= (1.0 - tile.failure_rate * 0.5)

        return max(0.0, score)

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
