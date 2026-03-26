"""Tests for the ShopManager (scenario-agnostic inventory + delegation)."""

import pytest

from uma_trainer.decision.shop_manager import (
    ITEM_CATALOGUE,
    ItemTier,
    ShopManager,
)
from uma_trainer.types import GameState, Mood, TrainingTile, StatType


class TestItemCatalogue:
    def test_notepad_is_never_buy(self):
        assert ITEM_CATALOGUE["notepad"].tier == ItemTier.NEVER

    def test_scroll_is_ss_tier(self):
        assert ITEM_CATALOGUE["scroll"].tier == ItemTier.SS

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
