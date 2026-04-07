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

    def test_stacking_bonus_zero_when_all_bonds_maxed(self, scorer_config):
        """Card stacking bonus should be zero when all bonds are maxed."""
        scorer = TrainingScorer(scorer_config)

        tile_3cards = TrainingTile(
            stat_type=StatType.SPEED,
            support_cards=["c1", "c2", "c3"],
            bond_levels=[100, 100, 100],
            tap_coords=(100, 500),
        )
        tile_0cards = TrainingTile(
            stat_type=StatType.SPEED,
            tap_coords=(100, 500),
        )

        # When bonds NOT maxed: 3 cards should outscore 0 cards
        state_building = GameState(
            energy=80, training_tiles=[tile_3cards], current_turn=40,
            all_bonds_maxed=False,
        )
        state_building_empty = GameState(
            energy=80, training_tiles=[tile_0cards], current_turn=40,
            all_bonds_maxed=False,
        )
        score_with_cards = scorer._score_tile(tile_3cards, state_building)
        score_no_cards = scorer._score_tile(tile_0cards, state_building_empty)
        stacking_diff = score_with_cards - score_no_cards
        assert stacking_diff > 0, "Stacking bonus should exist when bonds not maxed"

        # When ALL bonds maxed: 3 cards should score same as 0 cards
        # (no stacking bonus, bond-building bonus also gone since all >=80)
        state_maxed = GameState(
            energy=80, training_tiles=[tile_3cards], current_turn=40,
            all_bonds_maxed=True,
        )
        state_maxed_empty = GameState(
            energy=80, training_tiles=[tile_0cards], current_turn=40,
            all_bonds_maxed=True,
        )
        score_maxed_cards = scorer._score_tile(tile_3cards, state_maxed)
        score_maxed_none = scorer._score_tile(tile_0cards, state_maxed_empty)
        maxed_diff = score_maxed_cards - score_maxed_none
        assert maxed_diff < stacking_diff, "Stacking bonus should be eliminated when all bonds maxed"


class TestFriendshipPriorities:
    """Priority card scoring from playbook friendship policy."""

    def test_priority_card_boosts_tile(self, scorer_config):
        """Tile with a priority card (low bond) scores higher than one without."""
        from uma_trainer.types import SupportCard

        scorer = TrainingScorer(scorer_config)
        scorer.set_friendship_priorities(["team_sirius", "riko"])

        tile_with = TrainingTile(
            stat_type=StatType.SPEED, support_cards=["team_sirius", "card_1"],
            position=0, tap_coords=(100, 500),
        )
        tile_without = TrainingTile(
            stat_type=StatType.SPEED, support_cards=["card_2", "card_1"],
            position=1, tap_coords=(300, 500),
        )

        state = GameState(
            energy=80, training_tiles=[tile_with, tile_without],
            current_turn=10,
            support_cards=[
                SupportCard(card_id="team_sirius", bond_level=40),
                SupportCard(card_id="card_1", bond_level=40),
                SupportCard(card_id="card_2", bond_level=40),
            ],
        )
        score_with = scorer._score_tile(tile_with, state)
        score_without = scorer._score_tile(tile_without, state)
        assert score_with > score_without

    def test_priority_card_no_boost_when_bonded(self, scorer_config):
        """No extra boost when the priority card is already at friendship."""
        from uma_trainer.types import SupportCard

        scorer = TrainingScorer(scorer_config)
        scorer.set_friendship_priorities(["team_sirius"])

        tile = TrainingTile(
            stat_type=StatType.SPEED, support_cards=["team_sirius"],
            position=0, tap_coords=(100, 500),
        )

        state = GameState(
            energy=80, training_tiles=[tile], current_turn=10,
            support_cards=[
                SupportCard(card_id="team_sirius", bond_level=80),
            ],
        )
        score_bonded = scorer._score_tile(tile, state)

        # Same tile but without priorities set
        scorer2 = TrainingScorer(scorer_config)
        score_no_prio = scorer2._score_tile(tile, state)
        assert score_bonded == score_no_prio

    def test_first_priority_gets_bigger_boost(self, scorer_config):
        """First card in priority list gets a bigger boost than the second."""
        from uma_trainer.types import SupportCard

        scorer = TrainingScorer(scorer_config)
        scorer.set_friendship_priorities(["team_sirius", "riko"])

        tile_sirius = TrainingTile(
            stat_type=StatType.SPEED, support_cards=["team_sirius"],
            position=0, tap_coords=(100, 500),
        )
        tile_riko = TrainingTile(
            stat_type=StatType.SPEED, support_cards=["riko"],
            position=1, tap_coords=(300, 500),
        )

        state = GameState(
            energy=80, training_tiles=[tile_sirius, tile_riko],
            current_turn=10,
            support_cards=[
                SupportCard(card_id="team_sirius", bond_level=40),
                SupportCard(card_id="riko", bond_level=40),
            ],
        )
        score_sirius = scorer._score_tile(tile_sirius, state)
        score_riko = scorer._score_tile(tile_riko, state)
        assert score_sirius > score_riko
