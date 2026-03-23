"""Pytest fixtures shared across all test files."""

import pytest

from uma_trainer.types import (
    EventChoice,
    GameState,
    Mood,
    ScreenState,
    SkillOption,
    StatType,
    TraineeStats,
    TrainingTile,
)
from uma_trainer.config import AppConfig, ScorerConfig


@pytest.fixture
def sample_stats() -> TraineeStats:
    return TraineeStats(speed=400, stamina=350, power=300, guts=250, wit=200)


@pytest.fixture
def sample_training_tiles() -> list[TrainingTile]:
    return [
        TrainingTile(
            stat_type=StatType.SPEED,
            support_cards=["card_a", "card_b"],
            is_rainbow=False,
            is_gold=True,
            has_hint=False,
            position=0,
            tap_coords=(200, 600),
        ),
        TrainingTile(
            stat_type=StatType.STAMINA,
            support_cards=["card_c"],
            is_rainbow=False,
            is_gold=False,
            has_hint=True,
            position=1,
            tap_coords=(400, 600),
        ),
        TrainingTile(
            stat_type=StatType.POWER,
            support_cards=[],
            is_rainbow=True,
            is_gold=False,
            has_hint=False,
            position=2,
            tap_coords=(640, 600),
        ),
        TrainingTile(
            stat_type=StatType.GUTS,
            support_cards=["card_d"],
            is_rainbow=False,
            is_gold=False,
            has_hint=False,
            position=3,
            tap_coords=(880, 600),
        ),
        TrainingTile(
            stat_type=StatType.WIT,
            support_cards=[],
            is_rainbow=False,
            is_gold=False,
            has_hint=False,
            position=4,
            tap_coords=(1080, 600),
        ),
    ]


@pytest.fixture
def sample_game_state(sample_stats, sample_training_tiles) -> GameState:
    return GameState(
        screen=ScreenState.TRAINING,
        stats=sample_stats,
        energy=70,
        mood=Mood.GOOD,
        training_tiles=sample_training_tiles,
        current_turn=15,
        max_turns=72,
    )


@pytest.fixture
def low_energy_state(sample_stats, sample_training_tiles) -> GameState:
    return GameState(
        screen=ScreenState.TRAINING,
        stats=sample_stats,
        energy=10,
        mood=Mood.NORMAL,
        training_tiles=sample_training_tiles,
        current_turn=30,
    )


@pytest.fixture
def event_state() -> GameState:
    return GameState(
        screen=ScreenState.EVENT,
        stats=TraineeStats(speed=300, stamina=250, power=200, guts=150, wit=100),
        energy=60,
        mood=Mood.NORMAL,
        event_text="You're feeling fired up today!",
        event_choices=[
            EventChoice(index=0, text="Accept the challenge", tap_coords=(640, 450)),
            EventChoice(index=1, text="Decline and rest", tap_coords=(640, 530)),
        ],
        current_turn=20,
    )


@pytest.fixture
def scorer_config() -> ScorerConfig:
    return ScorerConfig(
        stat_weights={"speed": 1.2, "stamina": 1.0, "power": 0.9, "guts": 0.8, "wit": 0.7},
        rainbow_bonus=2.0,
        gold_bonus=1.5,
        hint_bonus=1.2,
        card_stack_per_card=0.8,
        energy_penalty_threshold=30,
        rest_energy_threshold=20,
        bond_priority_turns=24,
    )


@pytest.fixture
def tmp_db(tmp_path):
    """A temporary SQLite database for testing."""
    db_path = str(tmp_path / "test.db")
    from uma_trainer.knowledge.database import KnowledgeBase
    kb = KnowledgeBase(db_path)
    yield kb
    kb.close()
