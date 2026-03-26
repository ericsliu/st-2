"""Tests for the DecisionLogger."""

import json
import pytest

from uma_trainer.decision.logger import DecisionLogger, _tile_to_dict
from uma_trainer.types import (
    ActionType,
    BotAction,
    GameState,
    Mood,
    StatType,
    TraineeStats,
    TrainingTile,
)


class TestTileToDict:
    def test_basic_serialization(self):
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            support_cards=["c0", "c1"],
            is_rainbow=True,
            stat_gains={"speed": 20, "stamina": 5},
        )
        d = _tile_to_dict(tile)
        assert d["stat"] == "speed"
        assert d["cards"] == 2
        assert d["rainbow"] is True
        assert d["gold"] is False
        assert d["total_gain"] == 25
        assert d["stat_gains"] == {"speed": 20, "stamina": 5}

    def test_empty_tile(self):
        tile = TrainingTile(stat_type=StatType.WIT)
        d = _tile_to_dict(tile)
        assert d["cards"] == 0
        assert d["total_gain"] == 0
        assert d["stat_gains"] == {}


class TestDecisionLogger:
    def test_log_and_retrieve(self, tmp_db):
        logger = DecisionLogger(tmp_db)
        state = GameState(
            current_turn=15,
            energy=65,
            mood=Mood.GOOD,
            stats=TraineeStats(speed=200, stamina=150, power=120, guts=100, wit=180),
            scenario="trackblazer",
        )
        action = BotAction(
            action_type=ActionType.TRAIN,
            target="speed",
            reason="test reason",
            tier_used=1,
        )
        logger.log_decision("run-001", state, action)

        decisions = logger.get_run_decisions("run-001")
        assert len(decisions) == 1
        d = decisions[0]
        assert d["turn"] == 15
        assert d["energy"] == 65
        assert d["mood"] == "good"
        assert d["stat_speed"] == 200
        assert d["action_type"] == "train"
        assert d["action_target"] == "speed"

    def test_tile_data_stored_as_json(self, tmp_db):
        logger = DecisionLogger(tmp_db)
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            is_rainbow=True,
            stat_gains={"speed": 20},
        )
        state = GameState(
            current_turn=10,
            training_tiles=[tile],
            scenario="trackblazer",
        )
        action = BotAction(action_type=ActionType.TRAIN, target="speed")
        logger.log_decision("run-002", state, action)

        decisions = logger.get_run_decisions("run-002")
        tiles = json.loads(decisions[0]["tiles"])
        assert len(tiles) == 1
        assert tiles[0]["rainbow"] is True
        assert tiles[0]["total_gain"] == 20

    def test_tile_scores_stored(self, tmp_db):
        logger = DecisionLogger(tmp_db)
        state = GameState(current_turn=10, scenario="test")
        action = BotAction(action_type=ActionType.REST, reason="tired")
        scores = [{"stat": "speed", "score": 42.5}]

        logger.log_decision("run-003", state, action, tile_scores=scores)

        decisions = logger.get_run_decisions("run-003")
        stored_scores = json.loads(decisions[0]["tile_scores"])
        assert stored_scores[0]["score"] == 42.5

    def test_run_count(self, tmp_db):
        logger = DecisionLogger(tmp_db)
        state = GameState(current_turn=1, scenario="test")
        action = BotAction(action_type=ActionType.REST)

        logger.log_decision("run-a", state, action)
        logger.log_decision("run-a", state, action)
        logger.log_decision("run-b", state, action)

        assert logger.get_run_count() == 2
        assert logger.get_decision_count() == 3

    def test_empty_db_counts(self, tmp_db):
        logger = DecisionLogger(tmp_db)
        assert logger.get_run_count() == 0
        assert logger.get_decision_count() == 0

    def test_multiple_turns_ordered(self, tmp_db):
        logger = DecisionLogger(tmp_db)
        action = BotAction(action_type=ActionType.TRAIN, target="speed")

        for turn in [5, 10, 15, 20]:
            state = GameState(current_turn=turn, scenario="test")
            logger.log_decision("run-order", state, action)

        decisions = logger.get_run_decisions("run-order")
        turns = [d["turn"] for d in decisions]
        assert turns == [5, 10, 15, 20]

    def test_scenario_field_stored(self, tmp_db, trackblazer):
        logger = DecisionLogger(tmp_db, scenario=trackblazer)
        state = GameState(current_turn=30, scenario="trackblazer")
        action = BotAction(action_type=ActionType.REST)

        logger.log_decision("run-sc", state, action)

        decisions = logger.get_run_decisions("run-sc")
        assert decisions[0]["scenario"] == "trackblazer"
        assert decisions[0]["phase"] == "classic"
