"""Shop item purchase and usage strategy.

The ShopManager owns the item inventory and purchase priority logic.
Scenario-specific timing (when to visit, when to use items) is delegated
to the scenario handler via should_visit_shop() and get_item_to_use().

The ITEM_CATALOGUE is game-global (shared across all scenarios that have
a shop feature).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from uma_trainer.types import ActionType, BotAction, GameState

if TYPE_CHECKING:
    from uma_trainer.knowledge.overrides import OverridesLoader
    from uma_trainer.scenario.base import ScenarioHandler

logger = logging.getLogger(__name__)


class ItemTier(str, Enum):
    """Purchase priority tier."""
    SS = "SS"   # Buy on sight
    S = "S"     # Buy early / when needed
    A = "A"     # Buy if coins allow
    B = "B"     # Low priority
    NEVER = "X" # Never buy


@dataclass
class ShopItem:
    """Definition of a shop item and its strategy."""
    name: str
    cost: int
    tier: ItemTier
    max_stock: int = 5      # Max copies to hold
    save_for: str = ""      # Context when to use (empty = use immediately)
    effect: str = ""


# Complete item catalogue with purchase/usage strategy.
# Items the bot should never buy are marked NEVER.
ITEM_CATALOGUE: dict[str, ShopItem] = {
    # -- Stat Boosts --
    "notepad":              ShopItem("Notepad", 10, ItemTier.NEVER, effect="+3 stat"),
    "manual":               ShopItem("Manual", 15, ItemTier.A, effect="+7 stat"),
    "scroll":               ShopItem("Scroll", 30, ItemTier.SS, effect="+15 stat"),

    # -- Energy / Mood --
    "vita_20":              ShopItem("Vita 20", 35, ItemTier.B, effect="Energy +20"),
    "vita_40":              ShopItem("Vita 40", 55, ItemTier.A, effect="Energy +40"),
    "vita_65":              ShopItem("Vita 65", 75, ItemTier.A, effect="Energy +65"),
    "royal_kale":           ShopItem("Royal Kale Juice", 70, ItemTier.B, effect="Energy +100, Mood -1"),
    "energy_drink_max":     ShopItem("Energy Drink MAX", 30, ItemTier.A, effect="Max Energy +4"),
    "energy_drink_max_ex":  ShopItem("Energy Drink MAX EX", 50, ItemTier.A, effect="Max Energy +8"),
    "plain_cupcake":        ShopItem("Plain Cupcake", 30, ItemTier.B, effect="Mood +1"),
    "berry_cupcake":        ShopItem("Berry Sweet Cupcake", 55, ItemTier.B, effect="Mood +2"),

    # -- Training Items --
    "coaching_mega":        ShopItem("Coaching Megaphone", 40, ItemTier.A, effect="+20% training, 4 turns"),
    "motivating_mega":      ShopItem("Motivating Megaphone", 55, ItemTier.S, effect="+40% training, 3 turns", save_for="summer_camp"),
    "empowering_mega":      ShopItem("Empowering Megaphone", 70, ItemTier.SS, effect="+60% training, 2 turns", save_for="summer_camp"),
    "ankle_weights":        ShopItem("Ankle Weights", 50, ItemTier.A, effect="+50% stat / +20% energy, 1 turn", save_for="summer_camp"),
    "training_application": ShopItem("Training Application", 150, ItemTier.S, effect="Training level +1"),
    "good_luck_charm":      ShopItem("Good-Luck Charm", 40, ItemTier.SS, effect="0% failure, 1 turn", save_for="exceptional_training"),
    "reset_whistle":        ShopItem("Reset Whistle", 20, ItemTier.S, effect="Rearrange support cards"),

    # -- Race Items --
    "artisan_hammer":       ShopItem("Artisan Cleat Hammer", 25, ItemTier.A, effect="+20% race stat gain"),
    "master_hammer":        ShopItem("Master Cleat Hammer", 40, ItemTier.SS, max_stock=3, effect="+35% race stat gain", save_for="twinkle_star"),
    "glow_sticks":          ShopItem("Glow Sticks", 15, ItemTier.B, effect="+50% fan gain"),

    # -- Condition Cures --
    "practice_dvd":         ShopItem("Practice Drills DVD", 15, ItemTier.S, effect="Cure Practice Poor"),
    "pocket_planner":       ShopItem("Pocket Planner", 15, ItemTier.S, effect="Cure Slacker"),
    "smart_scale":          ShopItem("Smart Scale", 15, ItemTier.S, effect="Cure Slow Metabolism"),
    "rich_hand_cream":      ShopItem("Rich Hand Cream", 15, ItemTier.SS, effect="Cure Skin Outbreak"),
    "aroma_diffuser":       ShopItem("Aroma Diffuser", 15, ItemTier.S, effect="Cure Migraine"),
    "fluffy_pillow":        ShopItem("Fluffy Pillow", 15, ItemTier.S, effect="Cure Night Owl"),
    "miracle_cure":         ShopItem("Miracle Cure", 40, ItemTier.S, effect="Cure all conditions"),

    # -- Bond / Status --
    "grilled_carrots":      ShopItem("Grilled Carrots", 40, ItemTier.SS, effect="All bond +5"),
    "cat_food":             ShopItem("Cat Food", 10, ItemTier.B, effect="Director bond +5"),
    "practice_perfect":     ShopItem("Tips for Efficient Training", 150, ItemTier.S, effect="Grants Practice Perfect"),
    "hot_topic":            ShopItem("Reporter's Binoculars", 150, ItemTier.A, effect="Grants Hot Topic"),
    "charming":             ShopItem("Pretty Mirror", 150, ItemTier.A, effect="Grants Charming"),
    "scholar_hat":          ShopItem("Scholar's Hat", 280, ItemTier.SS, effect="10% skill cost reduction"),
}


class ShopManager:
    """Manages shop item purchases and usage decisions.

    Scenario-specific shop timing is delegated to the scenario handler.
    This class owns the inventory and generic purchase priority logic.
    """

    def __init__(
        self,
        overrides: "OverridesLoader | None" = None,
        scenario: "ScenarioHandler | None" = None,
    ) -> None:
        self.overrides = overrides
        self.scenario = scenario
        # Track owned items: {item_key: count}
        self._inventory: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Scenario-delegated decisions
    # ------------------------------------------------------------------

    def should_visit_shop(self, state: GameState) -> bool:
        """True if the bot should tap the Shop button this turn."""
        if self.scenario:
            return self.scenario.should_visit_shop(state)
        return False

    def get_item_to_use(self, state: GameState) -> BotAction | None:
        """Check if any owned item should be used this turn."""
        if self.scenario:
            action = self.scenario.get_item_to_use(state, self._inventory)
            if action:
                # Decrement inventory
                self._use_item(action.target)
            return action
        return None

    def is_exceptional_training(self, state: GameState) -> bool:
        """True if the best available training tile has stat gains above
        the exceptional threshold."""
        best_gain = self._best_training_gain(state)
        threshold = (
            self.scenario.get_exceptional_threshold()
            if self.scenario else 30
        )
        return best_gain >= threshold

    # ------------------------------------------------------------------
    # Purchase decisions (on shop screen)
    # ------------------------------------------------------------------

    def get_purchase_priorities(self, state: GameState) -> list[ShopItem]:
        """Return items to buy, ordered by priority.

        Call this when on the shop screen. The bot should attempt to buy
        items in this order, stopping when coins run out.
        """
        # Base priority from tier
        tier_order = {ItemTier.SS: 0, ItemTier.S: 1, ItemTier.A: 2, ItemTier.B: 3}
        buyable = [
            item for item in ITEM_CATALOGUE.values()
            if item.tier != ItemTier.NEVER
        ]

        # Context-aware adjustments
        adjusted: list[tuple[ShopItem, int]] = []
        for item in buyable:
            priority = tier_order[item.tier]

            # Grilled Carrots: much higher priority in early game
            is_early = (
                self.scenario.is_phase(state.current_turn, "early_game")
                if self.scenario
                else state.is_early_game
            )
            if item.name == "Grilled Carrots" and is_early:
                priority = -1  # Buy first

            # Scholar's Hat: high priority late game when SP is tight
            is_late = (
                self.scenario.is_phase(state.current_turn, "late_game")
                if self.scenario
                else state.is_late_game
            )
            if item.name == "Scholar's Hat" and is_late:
                priority = 0

            # Master Cleat Hammer: cap at max_stock
            if item.name == "Master Cleat Hammer":
                owned = self._inventory.get("master_hammer", 0)
                if owned >= item.max_stock:
                    continue

            # Megaphones: lower priority if next camp is far away
            if "Megaphone" in item.name and self.scenario:
                next_camp = self.scenario.turns_until_event(
                    "summer_camp", state.current_turn,
                )
                if next_camp is not None and next_camp > 6:
                    priority += 1

            adjusted.append((item, priority))

        adjusted.sort(key=lambda x: x[1])
        return [item for item, _ in adjusted]

    # ------------------------------------------------------------------
    # Inventory tracking
    # ------------------------------------------------------------------

    def add_item(self, item_key: str, count: int = 1) -> None:
        self._inventory[item_key] = self._inventory.get(item_key, 0) + count

    def _has_item(self, item_key: str) -> bool:
        return self._inventory.get(item_key, 0) > 0

    def _use_item(self, item_key: str) -> None:
        if self._inventory.get(item_key, 0) > 0:
            self._inventory[item_key] -= 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _best_training_gain(state: GameState) -> int:
        """Return the total stat gain of the best training tile."""
        if not state.training_tiles:
            return 0
        return max(t.total_stat_gain for t in state.training_tiles)
