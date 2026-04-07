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
    Condition,
    GameState,
    Mood,
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
        self._friendship_priorities: list[str] = []

    def set_friendship_priorities(self, card_names: list[str]) -> None:
        """Set priority card names for bond building (from playbook friendship policy).

        Tiles containing these cards get a scoring boost when their bond is below
        friendship level. Earlier in the list = higher priority.
        """
        self._friendship_priorities = list(card_names)
        logger.info("Friendship priorities set: %s", card_names)

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

    def should_go_out(self, state: GameState) -> BotAction | None:
        """Check if we should Go Out (recreation) to improve mood.

        Mood priorities:
        - Before Classic Summer (turn 36): Go Out if BAD or TERRIBLE
        - After Classic Summer (turn 36+): Go Out if not GOOD or GREAT
        - During summer camp: NEVER Go Out — too valuable to waste

        Returns a BotAction for GO_OUT, or None if mood is acceptable.
        """
        if self._is_summer_camp(state):
            return None  # Never waste summer camp turns on mood

        # Check for conditions that block mood improvement
        mood_blocked = any(
            c in state.active_conditions
            for c in (Condition.MIGRAINE,)
        )
        if mood_blocked:
            return None  # Go Out won't help — need Infirmary first

        is_post_classic_summer = state.current_turn >= 36
        mood = state.mood

        need_go_out = False
        if is_post_classic_summer:
            # After Classic Summer: need GOOD or GREAT
            if mood in (Mood.BAD, Mood.TERRIBLE, Mood.NORMAL):
                need_go_out = True
        else:
            # Before Classic Summer: only if BAD or TERRIBLE
            if mood in (Mood.BAD, Mood.TERRIBLE):
                need_go_out = True

        if not need_go_out:
            return None

        return BotAction(
            action_type=ActionType.GO_OUT,
            reason=f"Mood recovery: {mood.value} (turn {state.current_turn})",
        )

    def should_visit_infirmary(self, state: GameState) -> BotAction | None:
        """Check if we should visit the Infirmary to cure a condition.

        Conditions that warrant Infirmary:
        - Migraine (blocks mood improvement)
        - Night Owl (random energy drain)
        - Slacker (may skip training)
        - Practice Poor (reduced training gains)

        During summer camp: only visit for Migraine (critical blocker).
        """
        negative_conditions = [
            c for c in state.active_conditions
            if c in (
                Condition.MIGRAINE,
                Condition.NIGHT_OWL,
                Condition.SLACKER,
                Condition.PRACTICE_POOR,
                Condition.OVERWEIGHT,
                Condition.SKIN_OUTBREAK,
            )
        ]

        if not negative_conditions:
            return None

        # During summer camp, only Infirmary for critical blockers
        if self._is_summer_camp(state):
            critical = [c for c in negative_conditions if c == Condition.MIGRAINE]
            if not critical:
                return None
            negative_conditions = critical

        return BotAction(
            action_type=ActionType.INFIRMARY,
            reason=f"Cure conditions: {[c.value for c in negative_conditions]}",
        )

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
        """Return stat weights merged with any active phase weight overrides.

        Resolution order:
        1. Start with base weights from RunSpec phase_weights (if runspec loaded),
           falling back to strategy.yaml overrides, then config defaults.
        2. Zero out any stat that has reached its hard cap (from RunSpec stat_targets).
        """
        phase_checker = None
        if self.scenario:
            phase_checker = lambda phase: self.scenario.is_phase(state.current_turn, phase)

        base = dict(self.config.stat_weights)

        if self.runspec and self.runspec.phase_weights:
            weights = self.runspec.get_phase_weights(
                base, phase_checker=phase_checker,
                turn=state.current_turn, max_turns=state.max_turns,
            )
        elif self.overrides is not None:
            weights = self.overrides.get_stat_weights(
                base, state.current_turn, state.max_turns,
                phase_checker=phase_checker,
            )
        else:
            weights = base

        # Zero out stats that have reached their hard cap (from runspec)
        if self.runspec:
            stat_caps = self.runspec.get_stat_caps()
        else:
            stat_caps = {}
        current = {
            "speed": state.stats.speed, "stamina": state.stats.stamina,
            "power": state.stats.power, "guts": state.stats.guts,
            "wit": state.stats.wit,
        }
        for stat, cap in stat_caps.items():
            if current.get(stat, 0) >= cap:
                weights[stat] = 0.0

        return weights

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
        _valid_stats = {s.value for s in StatType}
        if self.runspec and tile.stat_gains:
            for stat_name, gain in tile.stat_gains.items():
                if stat_name not in _valid_stats:
                    continue
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

        # 2. Support card stacking bonus — only valuable while bonds are building.
        #    Once all cards hit friendship (>=80), stacking adds no bond value.
        if not state.all_bonds_maxed:
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

        # 6. Bond-building priority: maximize friendship (bond >= 80).
        #    Before the friendship deadline (Classic Summer), bond is the
        #    PRIMARY scoring factor. Card count dominates; stat gains are
        #    just a tiebreaker. One extra unbonded card should always beat
        #    any stat difference between tiles.
        #    After deadline: reduced but still significant bonus.
        bond_deadline = self._get_friendship_deadline(state)
        if tile.bond_levels:
            card_bonds = tile.bond_levels
        else:
            card_bonds = [
                self._get_card_bond(c, state)
                for c in tile.support_cards
            ]
        low_bond_values = [b for b in card_bonds if b < 80]
        if low_bond_values and not self._is_summer_camp(state):
            turn = state.current_turn
            n = len(low_bond_values)
            # Small tiebreaker: prefer lower-bond cards (more room to grow)
            bond_tiebreaker = sum(
                (80 - b) / 80.0 * 0.5
                for b in low_bond_values
            )
            if turn < bond_deadline:
                # Pre-deadline: 50 per card makes bond the dominant factor.
                # Max stat utility per tile is ~40-50, so 1 extra card
                # always outweighs any stat difference.
                bond_score = n * 50.0 + bond_tiebreaker
            else:
                bond_score = n * 6.0 + bond_tiebreaker

            if tile.has_hint and low_bond_values:
                bond_score *= 1.5

            score += bond_score

        # 6b. Priority card bonus — playbook can specify cards whose friendship
        #     matters most (e.g., Team Sirius before Riko). In Junior year,
        #     the #1 priority card is THE deciding factor — stat gains are
        #     meaningless compared to getting that bond to friendship level.
        #     Only 3+ support cards on another tile should override this.
        #     Pre-deadline: +80 for first priority (beats 1 extra generic card's +50).
        #     Post-deadline: reduced to +15 (bond still matters, but less dominant).
        if self._friendship_priorities and not self._is_summer_camp(state):
            turn = state.current_turn
            for i, pcard in enumerate(self._friendship_priorities):
                if pcard in tile.support_cards:
                    bond = self._get_card_bond(pcard, state)
                    if bond < 80:
                        if turn < bond_deadline:
                            # Pre-deadline: #1 priority card is THE deciding factor.
                            # Below green bond (<60): 150 boost — nothing else matters.
                            # Green bond (60-79): 80 boost — still dominant.
                            # #2 priority: half of #1's boost.
                            if i == 0 and bond < 60:
                                priority_boost = 150.0
                            elif i == 0:
                                priority_boost = 80.0
                            else:
                                priority_boost = 40.0 if bond < 60 else 20.0
                        else:
                            priority_boost = 15.0 - (i * 5.0)
                        score += max(priority_boost, 5.0)
                        if tile.has_hint:
                            score += 15.0

        # 7. Summer camp wit energy recovery bonus.
        #    During summer camp, wit training recovers ~5 energy per use.
        #    Wit is always valuable in summer (energy regen sustains training),
        #    and even more so when energy is low.
        if self._is_summer_camp(state) and tile.stat_type == StatType.WIT:
            score += 5.0   # Base summer wit bonus (energy regen value)
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
        #    Flat penalty: 10 points per 1% failure rate.
        #    5% failure = -50, 10% = -100. Harsh enough to override bond scores.
        effective_failure = 0.0 if boost_zero_failure else tile.failure_rate
        if effective_failure > 0:
            failure_pct = effective_failure * 100  # 0.05 → 5
            score -= failure_pct * 10.0

        return max(0.0, score)

    def has_high_bond_urgency(self, state: GameState) -> bool:
        """True if the best training tile has significant bond-building value.

        Used by the strategy engine to override non-goal races when friendship
        building is critical (hint tiles with low-bond cards present).
        """
        for tile in state.training_tiles:
            if tile.bond_levels:
                low_bond_count = sum(1 for b in tile.bond_levels if b < 80)
            else:
                low_bond_count = sum(
                    1 for c in tile.support_cards
                    if self._get_card_bond(c, state) < 80
                )
            if low_bond_count == 0:
                continue
            # A tile with hint + low-bond cards is high-urgency (hint boosts
            # friendship gain). Without hint, need 3+ low-bond cards.
            if tile.has_hint and low_bond_count >= 1:
                return True
            if low_bond_count >= 3:
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
        return 80  # Unknown card assumed bonded (avoids phantom bond scores)

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
