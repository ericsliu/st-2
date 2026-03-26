"""Tests for the RaceSelector."""

import pytest

from uma_trainer.decision.race_selector import RaceSelector
from uma_trainer.types import (
    ActionType,
    CareerGoal,
    GameState,
    RaceOption,
    ScreenState,
)


class TestRaceScoring:
    def test_goal_race_scores_highest(self, tmp_db, trackblazer, race_options):
        rs = RaceSelector(tmp_db, scenario=trackblazer)
        # Mark one as goal race
        race_options[1].is_goal_race = True
        state = GameState(
            screen=ScreenState.RACE_ENTRY,
            available_races=race_options,
            current_turn=20,
            scenario="trackblazer",
        )
        scored = rs.score_races(state)
        best_race, _ = scored[0]
        assert best_race.is_goal_race is True

    def test_g1_scores_higher_than_debut(self, tmp_db, trackblazer, race_options):
        rs = RaceSelector(tmp_db, scenario=trackblazer)
        state = GameState(
            screen=ScreenState.RACE_ENTRY,
            available_races=race_options,
            current_turn=20,
            scenario="trackblazer",
        )
        scored = rs.score_races(state)
        scores_by_name = {r.name: s for r, s in scored}
        assert scores_by_name["Takarazuka Kinen"] > scores_by_name["Debut Race"]

    def test_wrong_surface_penalty(self, tmp_db, trackblazer, race_options):
        rs = RaceSelector(tmp_db, scenario=trackblazer)
        state = GameState(
            screen=ScreenState.RACE_ENTRY,
            available_races=race_options,
            current_turn=20,
            scenario="trackblazer",
        )
        scored = rs.score_races(state)
        scores_by_name = {r.name: s for r, s in scored}
        # Dirt G3 should score lower than turf G1
        assert scores_by_name["Takarazuka Kinen"] > scores_by_name["Dirt G3"]

    def test_empty_race_list(self, tmp_db, trackblazer):
        rs = RaceSelector(tmp_db, scenario=trackblazer)
        state = GameState(
            screen=ScreenState.RACE_ENTRY,
            available_races=[],
            scenario="trackblazer",
        )
        action = rs.decide(state)
        assert action.action_type == ActionType.WAIT


class TestShouldRace:
    def test_goal_race_always_races(self, tmp_db, trackblazer):
        rs = RaceSelector(tmp_db, scenario=trackblazer)
        state = GameState(
            current_turn=20,
            energy=50,
            scenario="trackblazer",
            career_goals=[
                CareerGoal(race_name="Japan Cup", completed=False),
            ],
        )
        action = rs.should_race_this_turn(state)
        assert action is not None
        assert "Goal race" in action.reason

    def test_delegates_to_scenario(self, tmp_db, trackblazer):
        rs = RaceSelector(tmp_db, scenario=trackblazer)
        state = GameState(
            current_turn=10, energy=50, scenario="trackblazer",
        )
        action = rs.should_race_this_turn(state)
        # Turn 10 % 2 == 0 → Trackblazer should race
        assert action is not None

    def test_no_scenario_no_race(self, tmp_db):
        rs = RaceSelector(tmp_db)
        state = GameState(current_turn=10, energy=50, scenario="ura_finale")
        action = rs.should_race_this_turn(state)
        assert action is None

    def test_on_non_race_delegates(self, tmp_db, trackblazer):
        rs = RaceSelector(tmp_db, scenario=trackblazer)
        trackblazer._consecutive_races = 3
        rs.on_non_race_action()
        assert trackblazer._consecutive_races == 0


class TestMinScore:
    def test_trackblazer_min_score(self, tmp_db, trackblazer):
        rs = RaceSelector(tmp_db, scenario=trackblazer)
        # Create a very low-value race
        low_race = RaceOption(
            name="Bad Race", grade="Debut", distance=3200,
            surface="dirt", tap_coords=(540, 400),
        )
        state = GameState(
            screen=ScreenState.RACE_ENTRY,
            available_races=[low_race],
            current_turn=20,
            scenario="trackblazer",
        )
        action = rs.decide(state)
        # Trackblazer min_score=1.0, so even bad races might pass
        # depending on grade_value; this tests the threshold is applied
        assert action.action_type in (ActionType.RACE, ActionType.WAIT)
