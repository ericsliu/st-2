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


# Training boost effects for items that modify training output.
# multiplier: multiplicative bonus to stat gains (1.5 = +50%)
# zero_failure: if True, failure rate is treated as 0%
# duration: how many turns the effect lasts after activation
ITEM_TRAINING_EFFECTS: dict[str, dict] = {
    "ankle_weights":    {"multiplier": 1.5, "duration": 1},
    "coaching_mega":    {"multiplier": 1.2, "duration": 4},
    "motivating_mega":  {"multiplier": 1.4, "duration": 3},
    "empowering_mega":  {"multiplier": 1.6, "duration": 2},
    "good_luck_charm":  {"multiplier": 1.0, "duration": 1, "zero_failure": True},
}


@dataclass
class ActiveEffect:
    """An item effect currently active on the trainee."""
    item_key: str
    turns_remaining: int
    multiplier: float = 1.0
    zero_failure: bool = False


@dataclass
class TrainingBoost:
    """Combined training boost from all active/pending item effects."""
    multiplier: float = 1.0
    zero_failure: bool = False


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
    use_immediately: bool = False  # Used on purchase, don't track in inventory


# Complete item catalogue with purchase/usage strategy.
# Items the bot should never buy are marked NEVER.
ITEM_CATALOGUE: dict[str, ShopItem] = {
    # -- Stat Boosts --
    "notepad":              ShopItem("Notepad", 10, ItemTier.NEVER, effect="+3 stat"),
    "manual":               ShopItem("Manual", 15, ItemTier.B, max_stock=99, effect="+7 stat", use_immediately=True),
    "scroll":               ShopItem("Scroll", 30, ItemTier.A, max_stock=99, effect="+15 stat", use_immediately=True),

    # -- Energy / Mood --
    "vita_20":              ShopItem("Vita 20", 35, ItemTier.A, max_stock=3, effect="Energy +20"),
    "vita_40":              ShopItem("Vita 40", 55, ItemTier.A, max_stock=2, effect="Energy +40"),
    "vita_65":              ShopItem("Vita 65", 75, ItemTier.A, max_stock=1, effect="Energy +65"),
    "royal_kale":           ShopItem("Royal Kale Juice", 70, ItemTier.B, max_stock=1, effect="Energy +100, Mood -1"),
    "energy_drink_max":     ShopItem("Energy Drink MAX", 30, ItemTier.A, max_stock=2, effect="Max Energy +4"),
    "energy_drink_max_ex":  ShopItem("Energy Drink MAX EX", 50, ItemTier.NEVER, max_stock=1, effect="Max Energy +8"),
    "plain_cupcake":        ShopItem("Plain Cupcake", 30, ItemTier.A, max_stock=1, effect="Mood +1"),
    "berry_cupcake":        ShopItem("Berry Sweet Cupcake", 55, ItemTier.A, max_stock=1, effect="Mood +2"),

    # -- Training Items --
    # Empowering (2-turn) is top priority until we have 2 stockpiled for summer
    "empowering_mega":      ShopItem("Empowering Megaphone", 70, ItemTier.SS, max_stock=2, effect="+60% training, 2 turns", save_for="summer_camp"),
    # Motivating (3-turn) useful for good random training days
    "motivating_mega":      ShopItem("Motivating Megaphone", 55, ItemTier.S, max_stock=2, effect="+40% training, 3 turns"),
    "coaching_mega":        ShopItem("Coaching Megaphone", 40, ItemTier.NEVER, max_stock=0, effect="+20% training, 4 turns"),
    # Ankle weights: only useful for stats with support cards
    "ankle_weights":        ShopItem("Ankle Weights", 50, ItemTier.A, max_stock=2, effect="+50% stat / +20% energy, 1 turn", save_for="summer_camp"),
    "training_application": ShopItem("Training Application", 150, ItemTier.NEVER, max_stock=1, effect="Training level +1"),
    "good_luck_charm":      ShopItem("Good-Luck Charm", 40, ItemTier.S, max_stock=2, effect="0% failure, 1 turn", save_for="exceptional_training"),
    "reset_whistle":        ShopItem("Reset Whistle", 20, ItemTier.SS, max_stock=5, effect="Rearrange support cards", save_for="summer_no_rainbow"),

    # -- Race Items --
    # Master Cleat Hammer: need 3 for Twinkle Star Climax races (sizable stat boost)
    "master_hammer":        ShopItem("Master Cleat Hammer", 40, ItemTier.SS, max_stock=3, effect="+35% race stat gain", save_for="twinkle_star"),
    "artisan_hammer":       ShopItem("Artisan Cleat Hammer", 25, ItemTier.B, max_stock=2, effect="+20% race stat gain"),
    "glow_sticks":          ShopItem("Glow Sticks", 15, ItemTier.NEVER, effect="+50% fan gain"),

    # -- Condition Cures --
    "rich_hand_cream":      ShopItem("Rich Hand Cream", 15, ItemTier.S, max_stock=1, effect="Cure Skin Outbreak"),
    "miracle_cure":         ShopItem("Miracle Cure", 40, ItemTier.S, max_stock=1, effect="Cure all conditions"),
    "practice_dvd":         ShopItem("Practice Drills DVD", 15, ItemTier.B, max_stock=1, effect="Cure Practice Poor"),
    "pocket_planner":       ShopItem("Pocket Planner", 15, ItemTier.B, max_stock=1, effect="Cure Slacker"),
    "smart_scale":          ShopItem("Smart Scale", 15, ItemTier.B, max_stock=1, effect="Cure Slow Metabolism"),
    "aroma_diffuser":       ShopItem("Aroma Diffuser", 15, ItemTier.B, max_stock=1, effect="Cure Migraine"),
    "fluffy_pillow":        ShopItem("Fluffy Pillow", 15, ItemTier.B, max_stock=1, effect="Cure Night Owl"),

    # -- Bond / Status --
    "grilled_carrots":      ShopItem("Grilled Carrots", 40, ItemTier.A, max_stock=99, effect="All bond +5", use_immediately=True),
    "cat_food":             ShopItem("Cat Food", 10, ItemTier.NEVER, effect="Director bond +5"),
    "practice_perfect":     ShopItem("Tips for Efficient Training", 150, ItemTier.B, max_stock=1, effect="Grants Practice Perfect"),
    "hot_topic":            ShopItem("Reporter's Binoculars", 150, ItemTier.NEVER, effect="Grants Hot Topic"),
    "charming":             ShopItem("Pretty Mirror", 150, ItemTier.NEVER, effect="Grants Charming"),
    "scholar_hat":          ShopItem("Scholar's Hat", 280, ItemTier.NEVER, effect="10% skill cost reduction"),
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
        # Currently active item effects (used items with remaining duration)
        self._active_effects: list[ActiveEffect] = []
        # Last turn effects were ticked (prevents double-tick in same turn)
        self._last_tick_turn: int = -1

    # ------------------------------------------------------------------
    # Inventory persistence
    # ------------------------------------------------------------------

    def load_inventory(self, path: str = "data/inventory.yaml") -> None:
        """Load item inventory from a YAML file."""
        import yaml
        from pathlib import Path

        p = Path(path)
        if not p.exists():
            logger.warning("No inventory file at %s", path)
            return

        with open(p) as f:
            raw = yaml.safe_load(f) or {}

        for key, count in raw.items():
            if key.startswith("#") or key.startswith("_"):
                continue
            if key not in ITEM_CATALOGUE:
                logger.warning("Unknown item key in inventory: %s", key)
                continue
            self._inventory[key] = int(count)

        if self._inventory:
            logger.info("Loaded inventory: %s", self._inventory)
        else:
            logger.info("Inventory is empty")

    def save_inventory(self, path: str = "data/inventory.yaml") -> None:
        """Persist current inventory back to YAML."""
        import yaml
        from pathlib import Path

        data = {k: v for k, v in sorted(self._inventory.items()) if v > 0}
        header = (
            "# Items owned. Auto-updated after each item use.\n"
            "# Keys must match ITEM_CATALOGUE in shop_manager.py\n"
        )
        with open(Path(path), "w") as f:
            f.write(header)
            yaml.dump(data, f, default_flow_style=False)

    @property
    def inventory(self) -> dict[str, int]:
        """Read-only view of current inventory."""
        return dict(self._inventory)

    # ------------------------------------------------------------------
    # Scenario-delegated decisions
    # ------------------------------------------------------------------

    def should_visit_shop(self, state: GameState) -> bool:
        """True if the bot should tap the Shop button this turn."""
        if self.scenario:
            return self.scenario.should_visit_shop(state)
        return False

    def get_item_to_use(self, state: GameState) -> BotAction | None:
        """Check if any owned item should be used this turn.

        Does NOT decrement inventory — call consume_item() after
        successfully executing the item use in the UI.
        Deprecated: prefer get_item_queue() for multi-item planning.
        """
        if self.scenario:
            return self.scenario.get_item_to_use(state, self._inventory)
        return None

    def get_item_queue(self, state: GameState) -> list[BotAction]:
        """Plan a queue of items to use this turn.

        Returns an ordered list. Items are planned together so combos
        (e.g. Vita + Ankle Weights) are validated before committing.
        Does NOT decrement inventory — call consume_item() after each.
        """
        if self.scenario:
            return self.scenario.get_item_queue(state, self._inventory)
        return []

    def consume_item(self, item_key: str) -> None:
        """Decrement inventory after an item is successfully used."""
        self._use_item(item_key)

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
            self.save_inventory()

    # ------------------------------------------------------------------
    # Active item effects (Trackblazer shop items)
    # ------------------------------------------------------------------

    def activate_item(self, item_key: str) -> None:
        """Register an item as active after USE_ITEM is executed.

        Only items in ITEM_TRAINING_EFFECTS have training-relevant effects.
        """
        effect_def = ITEM_TRAINING_EFFECTS.get(item_key)
        if not effect_def:
            return
        effect = ActiveEffect(
            item_key=item_key,
            turns_remaining=effect_def["duration"],
            multiplier=effect_def.get("multiplier", 1.0),
            zero_failure=effect_def.get("zero_failure", False),
        )
        self._active_effects.append(effect)
        logger.info(
            "Item activated: %s (×%.1f, %d turns%s)",
            item_key, effect.multiplier, effect.turns_remaining,
            ", 0%% failure" if effect.zero_failure else "",
        )

    def tick_effects(self, current_turn: int) -> None:
        """Decrement active effect durations. Call once per turn.

        Uses current_turn to prevent double-ticking when the FSM sees
        the TRAINING screen multiple times in the same turn.
        """
        if current_turn <= self._last_tick_turn:
            return
        self._last_tick_turn = current_turn
        expired = []
        for effect in self._active_effects:
            effect.turns_remaining -= 1
            if effect.turns_remaining <= 0:
                expired.append(effect)
                logger.debug("Item effect expired: %s", effect.item_key)
        for e in expired:
            self._active_effects.remove(e)

    def get_training_boost(self, state: GameState) -> TrainingBoost:
        """Get the combined training boost from active item effects.

        Includes both currently active effects (from items used on prior
        turns) and the pending item that get_item_to_use() would return
        this turn (so the scorer can factor it in before execution).
        """
        multiplier = 1.0
        zero_failure = False

        # Active effects from items already used
        for effect in self._active_effects:
            multiplier *= effect.multiplier
            zero_failure = zero_failure or effect.zero_failure

        # Pending: preview all items in this turn's queue
        if self.scenario:
            for pending in self.scenario.get_item_queue(state, self._inventory):
                if pending.target in ITEM_TRAINING_EFFECTS:
                    effect_def = ITEM_TRAINING_EFFECTS[pending.target]
                    multiplier *= effect_def.get("multiplier", 1.0)
                    zero_failure = zero_failure or effect_def.get("zero_failure", False)

        return TrainingBoost(multiplier=multiplier, zero_failure=zero_failure)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _best_training_gain(state: GameState) -> int:
        """Return the total stat gain of the best training tile."""
        if not state.training_tiles:
            return 0
        return max(t.total_stat_gain for t in state.training_tiles)
