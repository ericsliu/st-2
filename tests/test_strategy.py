"""Tests for the DecisionEngine (strategy routing)."""

import pytest
from unittest.mock import MagicMock

from uma_trainer.decision.strategy import DecisionEngine
from uma_trainer.decision.scorer import TrainingScorer
from uma_trainer.decision.shop_manager import ShopManager
from uma_trainer.types import (
    ActionType,
    BotAction,
    EventChoice,
    GameState,
    Mood,
    ScreenState,
    StatType,
    TrainingTile,
)


def _make_engine(scorer_config, trackblazer):
    """Build a DecisionEngine with mocked sub-components."""
    scorer = TrainingScorer(scorer_config, scenario=trackblazer)
    event_handler = MagicMock()
    event_handler.decide.return_value = BotAction(
        action_type=ActionType.CHOOSE_EVENT,
        target="0",
        reason="Mock event choice",
    )
    skill_buyer = MagicMock()
    skill_buyer.decide.return_value = []

    race_selector = MagicMock()
    race_selector.should_race_this_turn.return_value = None
    race_selector.on_non_race_action.return_value = None
    race_selector.decide.return_value = BotAction(
        action_type=ActionType.WAIT, reason="No races",
    )

    shop = ShopManager(scenario=trackblazer)
    engine = DecisionEngine(
        scorer, event_handler, skill_buyer, race_selector, shop,
        scenario=trackblazer,
    )
    return engine


class TestDecisionRouting:
    def test_training_screen_returns_train_or_rest(
        self, scorer_config, trackblazer, sample_training_tiles,
    ):
        engine = _make_engine(scorer_config, trackblazer)
        state = GameState(
            screen=ScreenState.TRAINING,
            energy=70,
            mood=Mood.GOOD,
            training_tiles=sample_training_tiles,
            current_turn=15,
            scenario="trackblazer",
        )
        action = engine.decide(state)
        assert action.action_type in (ActionType.TRAIN, ActionType.REST)

    def test_event_screen_routes_to_handler(
        self, scorer_config, trackblazer,
    ):
        engine = _make_engine(scorer_config, trackblazer)
        state = GameState(
            screen=ScreenState.EVENT,
            event_text="Test event",
            event_choices=[
                EventChoice(index=0, text="Choice A", tap_coords=(640, 450)),
            ],
        )
        action = engine.decide(state)
        assert action.action_type == ActionType.CHOOSE_EVENT

    def test_loading_screen_returns_wait(
        self, scorer_config, trackblazer,
    ):
        engine = _make_engine(scorer_config, trackblazer)
        for screen in (ScreenState.LOADING, ScreenState.CUTSCENE, ScreenState.RACE):
            state = GameState(screen=screen)
            action = engine.decide(state)
            assert action.action_type == ActionType.WAIT

    def test_result_screen_returns_wait_with_coords(
        self, scorer_config, trackblazer,
    ):
        engine = _make_engine(scorer_config, trackblazer)
        state = GameState(screen=ScreenState.RESULT_SCREEN)
        action = engine.decide(state)
        assert action.action_type == ActionType.WAIT
        assert action.tap_coords != (0, 0)

    def test_unknown_screen_returns_wait(
        self, scorer_config, trackblazer,
    ):
        engine = _make_engine(scorer_config, trackblazer)
        state = GameState(screen=ScreenState.UNKNOWN)
        action = engine.decide(state)
        assert action.action_type == ActionType.WAIT

    def test_skill_shop_no_skills_returns_skip(
        self, scorer_config, trackblazer,
    ):
        engine = _make_engine(scorer_config, trackblazer)
        state = GameState(screen=ScreenState.SKILL_SHOP)
        action = engine.decide(state)
        assert action.action_type == ActionType.SKIP_SKILL


class TestExceptionalTrainingOverride:
    def test_exceptional_training_overrides_race(
        self, scorer_config, trackblazer,
    ):
        engine = _make_engine(scorer_config, trackblazer)
        # Mock race selector to want to race
        engine.race_selector.should_race_this_turn.return_value = BotAction(
            action_type=ActionType.RACE,
            tap_coords=(500, 500),
            reason="Trackblazer rhythm",
        )
        # Create a tile with exceptional gains
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            support_cards=["c0", "c1"],
            is_rainbow=True,
            stat_gains={"speed": 25, "power": 10},
            tap_coords=(200, 600),
        )
        state = GameState(
            screen=ScreenState.TRAINING,
            energy=70,
            training_tiles=[tile],
            current_turn=15,
            scenario="trackblazer",
        )
        action = engine.decide(state)
        # Should train instead of race
        assert action.action_type == ActionType.TRAIN

    def test_goal_race_beats_exceptional(
        self, scorer_config, trackblazer,
    ):
        engine = _make_engine(scorer_config, trackblazer)
        engine.race_selector.should_race_this_turn.return_value = BotAction(
            action_type=ActionType.RACE,
            tap_coords=(500, 500),
            reason="Goal race due: Japan Cup",
        )
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            stat_gains={"speed": 25, "power": 10},
            tap_coords=(200, 600),
        )
        state = GameState(
            screen=ScreenState.TRAINING,
            energy=70,
            training_tiles=[tile],
            current_turn=15,
            scenario="trackblazer",
        )
        action = engine.decide(state)
        # Goal race should still take priority
        assert action.action_type == ActionType.RACE


class TestShopGating:
    def test_no_shop_without_feature(self, scorer_config, ura_finale):
        """URA Finale has no shop feature, so shop should never be visited."""
        scorer = TrainingScorer(scorer_config, scenario=ura_finale)
        engine = DecisionEngine(
            scorer,
            MagicMock(),
            MagicMock(decide=MagicMock(return_value=[])),
            MagicMock(should_race_this_turn=MagicMock(return_value=None)),
            ShopManager(scenario=ura_finale),
            scenario=ura_finale,
        )
        state = GameState(
            screen=ScreenState.TRAINING,
            energy=70,
            training_tiles=[
                TrainingTile(stat_type=StatType.SPEED, tap_coords=(200, 600)),
            ],
            current_turn=12,  # Would be a shop refresh turn
            scenario="ura_finale",
        )
        action = engine.decide(state)
        assert action.action_type != ActionType.SHOP
