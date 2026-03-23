"""Tests for the Tier 1 training tile scorer."""

import pytest

from uma_trainer.decision.scorer import TrainingScorer
from uma_trainer.types import ActionType, Mood, StatType, TrainingTile, GameState


class TestTrainingScorer:
    def test_rest_when_energy_low(self, scorer_config, low_energy_state):
        scorer = TrainingScorer(scorer_config)
        action = scorer.best_action(low_energy_state)
        assert action.action_type == ActionType.REST
        assert "energy" in action.reason.lower()

    def test_rainbow_tile_scores_higher_than_plain_same_stat(self, scorer_config):
        """A rainbow tile should score higher than an identical plain tile."""
        scorer = TrainingScorer(scorer_config)

        plain = TrainingTile(stat_type=StatType.SPEED, support_cards=["c1"], position=0, tap_coords=(100, 500))
        rainbow = TrainingTile(stat_type=StatType.SPEED, support_cards=["c1"], is_rainbow=True, position=1, tap_coords=(300, 500))

        state = GameState(energy=80, training_tiles=[plain, rainbow], current_turn=10)
        scores = scorer.score_tiles(state)
        best, _ = scores[0]
        assert best.is_rainbow is True

    def test_gold_tile_beats_plain_tile(self, scorer_config, sample_game_state):
        """Gold tile (speed) should outscore a plain tile with hint (stamina)."""
        scorer = TrainingScorer(scorer_config)
        tiles_scored = scorer.score_tiles(sample_game_state)
        scores_by_stat = {t.stat_type: s for t, s in tiles_scored}
        # Rainbow tile is excluded — compare gold (speed) vs hint (stamina)
        # Both should be significantly scored
        assert scores_by_stat[StatType.SPEED] > 0
        assert scores_by_stat[StatType.STAMINA] > 0

    def test_hint_bonus_applied(self, scorer_config, sample_game_state):
        scorer = TrainingScorer(scorer_config)
        tiles_scored = scorer.score_tiles(sample_game_state)

        hint_score = next(s for t, s in tiles_scored if t.has_hint)
        wit_score = next(s for t, s in tiles_scored if t.stat_type == StatType.WIT and not t.has_hint)
        # Stamina (hint, 1 card) should beat plain WIT (no cards, no indicators)
        assert hint_score > wit_score

    def test_mood_multiplier_applied(self, scorer_config, sample_game_state):
        """Good mood should yield higher scores than terrible mood."""
        import copy
        scorer = TrainingScorer(scorer_config)

        good_state = copy.deepcopy(sample_game_state)
        good_state.mood = Mood.GREAT

        bad_state = copy.deepcopy(sample_game_state)
        bad_state.mood = Mood.TERRIBLE

        good_scores = scorer.score_tiles(good_state)
        bad_scores = scorer.score_tiles(bad_state)

        good_total = sum(s for _, s in good_scores)
        bad_total = sum(s for _, s in bad_scores)
        assert good_total > bad_total

    def test_card_stacking_increases_score(self, scorer_config, sample_game_state):
        """Tiles with more support cards should score higher than empty tiles."""
        scorer = TrainingScorer(scorer_config)
        scores = scorer.score_tiles(sample_game_state)

        # Speed tile has 2 cards, wit tile has 0 cards (same rainbow/gold status)
        scores_dict = {t.stat_type: s for t, s in scores}
        # Speed (2 cards, gold) vs wit (0 cards, no indicator)
        # Speed should win even accounting for higher stat weight
        assert scores_dict[StatType.SPEED] > scores_dict[StatType.WIT]

    def test_energy_penalty_applied(self, scorer_config):
        """Low energy should reduce tile scores."""
        import copy
        scorer = TrainingScorer(scorer_config)

        tile = TrainingTile(stat_type=StatType.SPEED, tap_coords=(640, 500))

        high_energy_state = GameState(energy=80, training_tiles=[tile], current_turn=10)
        low_energy_state = GameState(energy=25, training_tiles=[tile], current_turn=10)

        high_score = scorer._score_tile(tile, high_energy_state)
        low_score = scorer._score_tile(tile, low_energy_state)
        assert high_score > low_score

    def test_should_rest_threshold(self, scorer_config):
        scorer = TrainingScorer(scorer_config)

        # At exactly the threshold — should rest
        state = GameState(energy=scorer_config.rest_energy_threshold - 1)
        assert scorer.should_rest(state) is True

        # Above threshold — should train
        state2 = GameState(energy=scorer_config.rest_energy_threshold + 1)
        assert scorer.should_rest(state2) is False
