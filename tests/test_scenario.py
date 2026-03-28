"""Tests for the scenario system (base, trackblazer, registry)."""

import pytest

from uma_trainer.scenario.base import (
    EventWindow,
    PhaseRange,
    ScenarioConfig,
    ScenarioHandler,
    parse_scenario_config,
)
from uma_trainer.scenario.trackblazer import TrackblazerHandler
from uma_trainer.scenario import load_scenario
from uma_trainer.types import ActionType, GameState, Mood, TrainingTile, StatType


class TestPhaseRange:
    def test_fraction_to_turns(self):
        cfg = ScenarioConfig(max_turns=72, phases={
            "junior": PhaseRange(0.0, 0.333),
            "classic": PhaseRange(0.333, 0.667),
            "senior": PhaseRange(0.667, 1.0),
        })
        h = ScenarioHandler(cfg)
        assert h.phase_at(0) == "junior"
        assert h.phase_at(23) == "junior"
        assert h.phase_at(24) == "classic"
        assert h.phase_at(47) == "classic"
        assert h.phase_at(48) == "senior"
        assert h.phase_at(71) == "senior"

    def test_is_phase_with_alias(self):
        cfg = ScenarioConfig(
            max_turns=72,
            phases={"junior": PhaseRange(0.0, 0.333)},
            phase_aliases={"early_game": PhaseRange(0.0, 0.333)},
        )
        h = ScenarioHandler(cfg)
        assert h.is_phase(10, "early_game") is True
        assert h.is_phase(50, "early_game") is False

    def test_is_phase_direct_name(self):
        cfg = ScenarioConfig(
            max_turns=72,
            phases={"classic": PhaseRange(0.333, 0.667)},
        )
        h = ScenarioHandler(cfg)
        assert h.is_phase(30, "classic") is True
        assert h.is_phase(10, "classic") is False

    def test_nonexistent_phase_returns_false(self):
        h = ScenarioHandler(ScenarioConfig(max_turns=72))
        assert h.is_phase(10, "nonexistent") is False


class TestYearCalculations:
    def test_current_year(self, trackblazer):
        assert trackblazer.current_year(0) == 1
        assert trackblazer.current_year(23) == 1
        assert trackblazer.current_year(24) == 2
        assert trackblazer.current_year(48) == 3
        assert trackblazer.current_year(71) == 3

    def test_turns_left_in_year(self, trackblazer):
        # Turn 20 in junior (ends at turn 24)
        assert trackblazer.turns_left_in_year(20) == 4
        # Turn 47 in classic (ends at turn 48)
        assert trackblazer.turns_left_in_year(47) == 1
        # Turn 71 in senior (ends at turn 72)
        assert trackblazer.turns_left_in_year(71) == 1

    def test_is_year_end(self, trackblazer):
        assert trackblazer.is_year_end(22) is True
        assert trackblazer.is_year_end(23) is True
        assert trackblazer.is_year_end(20) is False
        assert trackblazer.is_year_end(46) is True
        assert trackblazer.is_year_end(70) is True


class TestEventCalendar:
    def test_get_event_turns(self, trackblazer):
        summer = trackblazer.get_event_turns("summer_camp")
        assert 12 in summer
        assert 15 in summer
        assert 36 in summer
        assert 11 not in summer
        assert 16 not in summer

    def test_event_start(self, trackblazer):
        assert trackblazer.is_event_start("summer_camp", 12) is True
        assert trackblazer.is_event_start("summer_camp", 13) is False
        assert trackblazer.is_event_start("summer_camp", 36) is True

    def test_turns_until_event(self, trackblazer):
        assert trackblazer.turns_until_event("summer_camp", 8) == 4
        assert trackblazer.turns_until_event("summer_camp", 12) == 24
        assert trackblazer.turns_until_event("summer_camp", 64) is None

    def test_nonexistent_event(self, trackblazer):
        assert trackblazer.get_event_turns("nonexistent") == set()
        assert trackblazer.turns_until_event("nonexistent", 0) is None


class TestFeatures:
    def test_trackblazer_features(self, trackblazer):
        assert trackblazer.has_feature("shop") is True
        assert trackblazer.has_feature("grade_points") is True
        assert trackblazer.has_feature("nonexistent") is False

    def test_ura_finale_no_shop(self, ura_finale):
        assert ura_finale.has_feature("shop") is False
        assert ura_finale.has_feature("grade_points") is False


class TestRaceConfig:
    def test_trackblazer_grade_points(self, trackblazer):
        gp = trackblazer.get_grade_points("G1")
        assert gp == [100, 60, 40, 20, 10]
        assert trackblazer.get_grade_points("nonexistent") == []

    def test_grade_value(self, trackblazer):
        assert trackblazer.get_grade_value("G1") == 10.0
        assert trackblazer.get_grade_value("Debut") == 0.5

    def test_min_score_differs_by_scenario(self, trackblazer, ura_finale):
        assert trackblazer.get_race_min_score() == 1.0
        assert ura_finale.get_race_min_score() == 5.0

    def test_grade_point_target(self, trackblazer):
        assert trackblazer.get_grade_point_target(1, "turf") == 60
        assert trackblazer.get_grade_point_target(2, "dirt") == 200
        assert trackblazer.get_grade_point_target(1, "unknown") == 0


class TestRestThreshold:
    def test_trackblazer_low_threshold(self, trackblazer):
        assert trackblazer.get_rest_threshold() == 5

    def test_ura_standard_threshold(self, ura_finale):
        assert ura_finale.get_rest_threshold() == 20


class TestRegistry:
    def test_load_trackblazer_returns_handler(self):
        h = load_scenario("trackblazer")
        assert isinstance(h, TrackblazerHandler)

    def test_load_ura_returns_base(self):
        h = load_scenario("ura_finale")
        assert isinstance(h, ScenarioHandler)
        assert not isinstance(h, TrackblazerHandler)

    def test_unknown_scenario_returns_base(self):
        h = load_scenario("nonexistent_scenario")
        assert isinstance(h, ScenarioHandler)
        assert h.config.name == "nonexistent_scenario"

    def test_config_display_name(self):
        h = load_scenario("trackblazer")
        assert h.config.display_name == "Trackblazer"


class TestTrackblazerHandler:
    def test_should_visit_shop_on_refresh(self, trackblazer):
        state = GameState(current_turn=12, scenario="trackblazer")
        assert trackblazer.should_visit_shop(state) is True

    def test_no_shop_before_debut(self, trackblazer):
        state = GameState(current_turn=3, scenario="trackblazer")
        assert trackblazer.should_visit_shop(state) is False

    def test_shop_on_bad_mood(self, trackblazer):
        state = GameState(
            current_turn=10, scenario="trackblazer", mood=Mood.BAD,
        )
        assert trackblazer.should_visit_shop(state) is True

    def test_shop_after_race(self, trackblazer):
        trackblazer.on_race_completed()
        state = GameState(current_turn=10, scenario="trackblazer")
        assert trackblazer.should_visit_shop(state) is True
        # Second call should be False (flag consumed)
        assert trackblazer.should_visit_shop(state) is False

    def test_item_charm_on_exceptional(self, trackblazer):
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            stat_gains={"speed": 25, "power": 10},
        )
        state = GameState(
            current_turn=20, training_tiles=[tile], scenario="trackblazer",
        )
        action = trackblazer.get_item_to_use(state, {"good_luck_charm": 1})
        assert action is not None
        assert action.target == "good_luck_charm"

    def test_no_charm_on_low_gain(self, trackblazer):
        tile = TrainingTile(
            stat_type=StatType.SPEED,
            stat_gains={"speed": 10},
        )
        state = GameState(
            current_turn=20, training_tiles=[tile], scenario="trackblazer",
        )
        action = trackblazer.get_item_to_use(state, {"good_luck_charm": 1})
        assert action is None

    def test_megaphone_at_summer_camp_start(self, trackblazer):
        state = GameState(current_turn=12, scenario="trackblazer")
        action = trackblazer.get_item_to_use(state, {"empowering_mega": 1})
        assert action is not None
        assert action.target == "empowering_mega"

    def test_megaphone_mid_camp(self, trackblazer):
        state = GameState(current_turn=13, scenario="trackblazer")
        action = trackblazer.get_item_to_use(state, {"empowering_mega": 1})
        # Megaphone used any turn during summer camp
        assert action is not None
        assert action.target == "empowering_mega"

    def test_hammer_at_twinkle_star(self, trackblazer):
        state = GameState(current_turn=70, scenario="trackblazer")
        action = trackblazer.get_item_to_use(state, {"master_hammer": 1})
        assert action is not None
        assert action.target == "master_hammer"

    def test_race_rhythm(self, trackblazer):
        state = GameState(
            current_turn=10, energy=50, scenario="trackblazer",
        )
        btn = (500, 500)
        action = trackblazer.should_race_this_turn(state, btn)
        # Turn 10 % 2 == 0, so should race
        assert action is not None
        assert action.action_type == ActionType.RACE

    def test_skip_early_turns(self, trackblazer):
        state = GameState(
            current_turn=3, energy=50, scenario="trackblazer",
        )
        action = trackblazer.should_race_this_turn(state, (500, 500))
        assert action is None

    def test_fatigue_break(self, trackblazer):
        """After 2 consecutive races, should take a break."""
        trackblazer._consecutive_races = 2
        state = GameState(
            current_turn=10, energy=50, scenario="trackblazer",
        )
        action = trackblazer.should_race_this_turn(state, (500, 500))
        assert action is None

    def test_fatigue_exception_year_end(self, trackblazer):
        """Year-end turns bypass fatigue break."""
        trackblazer._consecutive_races = 2
        state = GameState(
            current_turn=22, energy=50, scenario="trackblazer",
        )
        # Turn 22 is year-end, and 22 % 2 == 0 → should race
        action = trackblazer.should_race_this_turn(state, (500, 500))
        assert action is not None

    def test_on_non_race_resets_counter(self, trackblazer):
        trackblazer._consecutive_races = 2
        trackblazer.on_non_race_action()
        assert trackblazer._consecutive_races == 0


class TestBaseDefaults:
    def test_base_no_shop(self, ura_finale):
        state = GameState(current_turn=12)
        assert ura_finale.should_visit_shop(state) is False

    def test_base_no_item_usage(self, ura_finale):
        state = GameState(current_turn=12)
        assert ura_finale.get_item_to_use(state, {}) is None

    def test_base_no_race(self, ura_finale):
        state = GameState(current_turn=12)
        assert ura_finale.should_race_this_turn(state, (500, 500)) is None

    def test_base_on_race_completed_no_error(self, ura_finale):
        """Base handler should not crash on race completion."""
        ura_finale.on_race_completed()
        ura_finale.on_non_race_action()
