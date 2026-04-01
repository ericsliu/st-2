"""Tests for the ShopManager (scenario-agnostic inventory + delegation)."""

import pytest

from uma_trainer.decision.shop_manager import (
    ITEM_CATALOGUE,
    ITEM_TRAINING_EFFECTS,
    ActiveEffect,
    ItemTier,
    ShopManager,
    TrainingBoost,
)
from uma_trainer.types import GameState, Mood, TrainingTile, StatType


class TestItemCatalogue:
    def test_notepad_is_never_buy(self):
        assert ITEM_CATALOGUE["notepad"].tier == ItemTier.NEVER

    def test_scroll_is_a_tier(self):
        assert ITEM_CATALOGUE["scroll"].tier == ItemTier.A

    def test_all_items_have_effects(self):
        for key, item in ITEM_CATALOGUE.items():
            assert item.effect, f"{key} has no effect description"

    def test_master_hammer_max_stock(self):
        assert ITEM_CATALOGUE["master_hammer"].max_stock == 3


class TestShopManagerInventory:
    def test_add_and_check_item(self):
        sm = ShopManager()
        assert sm._has_item("scroll") is False
        sm.add_item("scroll", 2)
        assert sm._has_item("scroll") is True
        assert sm._inventory["scroll"] == 2

    def test_use_item_decrements(self):
        sm = ShopManager()
        sm.add_item("vita_40", 3)
        sm._use_item("vita_40")
        assert sm._inventory["vita_40"] == 2

    def test_use_item_at_zero_does_nothing(self):
        sm = ShopManager()
        sm._use_item("vita_40")
        assert sm._inventory.get("vita_40", 0) == 0


class TestShopManagerDelegation:
    def test_no_scenario_no_shop_visit(self):
        sm = ShopManager()
        state = GameState(current_turn=12, scenario="trackblazer")
        assert sm.should_visit_shop(state) is False

    def test_delegates_to_scenario(self, trackblazer):
        sm = ShopManager(scenario=trackblazer)
        state = GameState(current_turn=12, scenario="trackblazer")
        assert sm.should_visit_shop(state) is True

    def test_no_scenario_no_item_usage(self):
        sm = ShopManager()
        state = GameState(current_turn=12)
        assert sm.get_item_to_use(state) is None

    def test_item_usage_decrements_inventory(self, trackblazer):
        sm = ShopManager(scenario=trackblazer)
        sm.add_item("good_luck_charm", 2)
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            stat_gains={"speed": 25, "power": 10},
        )
        state = GameState(
            current_turn=20,
            training_tiles=[tile],
            scenario="trackblazer",
        )
        action = sm.get_item_to_use(state)
        assert action is not None
        assert action.target == "good_luck_charm"
        # get_item_to_use does NOT decrement; caller uses consume_item()
        sm.consume_item("good_luck_charm")
        assert sm._inventory["good_luck_charm"] == 1


class TestExceptionalTraining:
    def test_above_threshold(self, trackblazer):
        sm = ShopManager(scenario=trackblazer)
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            stat_gains={"speed": 20, "stamina": 15},
        )
        state = GameState(training_tiles=[tile])
        assert sm.is_exceptional_training(state) is True

    def test_below_threshold(self, trackblazer):
        sm = ShopManager(scenario=trackblazer)
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            stat_gains={"speed": 10},
        )
        state = GameState(training_tiles=[tile])
        assert sm.is_exceptional_training(state) is False

    def test_no_tiles(self, trackblazer):
        sm = ShopManager(scenario=trackblazer)
        state = GameState(training_tiles=[])
        assert sm.is_exceptional_training(state) is False

    def test_no_scenario_default_threshold(self):
        sm = ShopManager()
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            stat_gains={"speed": 20, "stamina": 15},
        )
        state = GameState(training_tiles=[tile])
        assert sm.is_exceptional_training(state) is True


class TestPurchasePriorities:
    def test_never_items_excluded(self, trackblazer):
        sm = ShopManager(scenario=trackblazer)
        state = GameState(current_turn=15, scenario="trackblazer")
        priorities = sm.get_purchase_priorities(state)
        names = [item.name for item in priorities]
        assert "Notepad" not in names

    def test_grilled_carrots_first_early_game(self, trackblazer):
        sm = ShopManager(scenario=trackblazer)
        state = GameState(current_turn=8, scenario="trackblazer")
        priorities = sm.get_purchase_priorities(state)
        assert priorities[0].name == "Grilled Carrots"


class TestActiveEffects:
    def test_activate_item_registers_effect(self):
        sm = ShopManager()
        sm.activate_item("ankle_weights")
        assert len(sm._active_effects) == 1
        assert sm._active_effects[0].multiplier == 1.5
        assert sm._active_effects[0].turns_remaining == 1

    def test_activate_non_training_item_ignored(self):
        sm = ShopManager()
        sm.activate_item("scroll")  # Not in ITEM_TRAINING_EFFECTS
        assert len(sm._active_effects) == 0

    def test_tick_decrements_and_expires(self):
        sm = ShopManager()
        sm.activate_item("ankle_weights")  # 1 turn
        sm.tick_effects(current_turn=1)
        assert len(sm._active_effects) == 0  # Expired

    def test_tick_multi_turn_effect(self):
        sm = ShopManager()
        sm.activate_item("motivating_mega")  # 3 turns
        sm.tick_effects(current_turn=1)
        assert len(sm._active_effects) == 1
        assert sm._active_effects[0].turns_remaining == 2
        sm.tick_effects(current_turn=2)
        assert sm._active_effects[0].turns_remaining == 1
        sm.tick_effects(current_turn=3)
        assert len(sm._active_effects) == 0

    def test_tick_deduplicates_same_turn(self):
        sm = ShopManager()
        sm.activate_item("motivating_mega")  # 3 turns
        sm.tick_effects(current_turn=5)
        sm.tick_effects(current_turn=5)  # Same turn — should not double-tick
        assert sm._active_effects[0].turns_remaining == 2

    def test_good_luck_charm_zero_failure(self):
        sm = ShopManager()
        sm.activate_item("good_luck_charm")
        assert sm._active_effects[0].zero_failure is True

    def test_get_training_boost_no_effects(self):
        sm = ShopManager()
        state = GameState()
        boost = sm.get_training_boost(state)
        assert boost.multiplier == 1.0
        assert boost.zero_failure is False

    def test_get_training_boost_active_effects(self):
        sm = ShopManager()
        sm.activate_item("empowering_mega")  # 1.6x
        sm.activate_item("ankle_weights")    # 1.5x
        state = GameState()
        boost = sm.get_training_boost(state)
        assert abs(boost.multiplier - 2.4) < 0.01  # 1.6 * 1.5

    def test_get_training_boost_with_zero_failure(self):
        sm = ShopManager()
        sm.activate_item("good_luck_charm")
        state = GameState()
        boost = sm.get_training_boost(state)
        assert boost.zero_failure is True
