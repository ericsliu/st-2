"""Shared data structures and enums used across all modules."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScreenState(str, Enum):
    """Game screen states detected by the perception pipeline."""

    MAIN_MENU = "main_menu"
    CAREER_SETUP = "career_setup"
    TRAINING = "training"
    EVENT = "event"
    RACE = "race"
    RACE_ENTRY = "race_entry"
    SKILL_SHOP = "skill_shop"
    RESULT_SCREEN = "result_screen"
    LOADING = "loading"
    CUTSCENE = "cutscene"
    UNKNOWN = "unknown"


class Mood(str, Enum):
    """Trainee mood levels affecting training gains."""

    GREAT = "great"      # 絶好調 — +20% training gains
    GOOD = "good"        # 好調   — +10% training gains
    NORMAL = "normal"    # 普通   — baseline
    BAD = "bad"          # 不調   — -10% training gains
    TERRIBLE = "terrible"  # 絶不調 — -20% training gains

    @property
    def multiplier(self) -> float:
        return {
            Mood.GREAT: 1.20,
            Mood.GOOD: 1.10,
            Mood.NORMAL: 1.00,
            Mood.BAD: 0.90,
            Mood.TERRIBLE: 0.80,
        }[self]


class StatType(str, Enum):
    """The five trainable stats in Uma Musume."""

    SPEED = "speed"
    STAMINA = "stamina"
    POWER = "power"
    GUTS = "guts"
    WIT = "wit"


class ActionType(str, Enum):
    """Bot-level action types."""

    TRAIN = "train"
    REST = "rest"
    INFIRMARY = "infirmary"
    GO_OUT = "go_out"
    RACE = "race"
    CHOOSE_EVENT = "choose_event"
    BUY_SKILL = "buy_skill"
    SKIP_SKILL = "skip_skill"
    WAIT = "wait"  # No-op (loading, animation)


@dataclass
class TraineeStats:
    speed: int = 0
    stamina: int = 0
    power: int = 0
    guts: int = 0
    wit: int = 0

    def total(self) -> int:
        return self.speed + self.stamina + self.power + self.guts + self.wit

    def get(self, stat: StatType) -> int:
        return getattr(self, stat.value)

    def as_dict(self) -> dict[str, int]:
        return {
            "speed": self.speed,
            "stamina": self.stamina,
            "power": self.power,
            "guts": self.guts,
            "wit": self.wit,
        }


@dataclass
class TrainingTile:
    """One of the 5 training tiles on the training screen."""

    stat_type: StatType = StatType.SPEED
    support_cards: list[str] = field(default_factory=list)  # Card IDs present
    is_rainbow: bool = False
    is_gold: bool = False
    has_hint: bool = False
    has_director: bool = False
    failure_rate: float = 0.0  # 0.0–1.0
    position: int = 0  # 0–4 left to right
    tap_coords: tuple[int, int] = (0, 0)


@dataclass
class SupportCard:
    card_id: str = ""
    name: str = ""
    bond_level: int = 0  # 0–100
    is_friend: bool = False  # Friend-type card


@dataclass
class CareerGoal:
    race_name: str = ""
    required_fans: int = 0
    completed: bool = False


@dataclass
class SkillOption:
    skill_id: str = ""
    name: str = ""
    cost: int = 0
    stat_boost: dict[str, int] = field(default_factory=dict)
    is_hint_skill: bool = False
    priority: int = 5  # 1-10 from knowledge base
    tap_coords: tuple[int, int] = (0, 0)


@dataclass
class EventChoice:
    index: int = 0
    text: str = ""
    effects_hint: str = ""  # OCR'd effect preview text if visible
    tap_coords: tuple[int, int] = (0, 0)


@dataclass
class GameState:
    """Complete assembled state of the game at one point in time."""

    screen: ScreenState = ScreenState.UNKNOWN
    stats: TraineeStats = field(default_factory=TraineeStats)
    energy: int = 100
    mood: Mood = Mood.NORMAL
    training_tiles: list[TrainingTile] = field(default_factory=list)
    support_cards: list[SupportCard] = field(default_factory=list)
    career_goals: list[CareerGoal] = field(default_factory=list)
    current_turn: int = 0
    max_turns: int = 72
    scenario: str = "ura_finale"
    event_text: str = ""
    event_choices: list[EventChoice] = field(default_factory=list)
    available_skills: list[SkillOption] = field(default_factory=list)
    confidence: float = 1.0  # Assembler confidence in this reading
    raw_detections: list[Any] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def is_early_game(self) -> bool:
        return self.current_turn < 24

    @property
    def is_late_game(self) -> bool:
        return self.current_turn > 50


@dataclass
class BotAction:
    """An action decision produced by the decision engine."""

    action_type: ActionType = ActionType.WAIT
    target: str = ""  # Stat name, race name, skill_id, or choice index as str
    tap_coords: tuple[int, int] = (0, 0)
    reason: str = ""
    tier_used: int = 1  # 1=scorer, 2=local LLM, 3=Claude API


@dataclass
class RunResult:
    """Summary of a completed Career Mode run."""

    run_id: str = ""
    trainee_id: str = ""
    scenario: str = ""
    final_stats: TraineeStats = field(default_factory=TraineeStats)
    goals_completed: int = 0
    total_goals: int = 0
    turns_taken: int = 0
    success: bool = False
    timestamp: float = field(default_factory=time.time)
    notes: str = ""
