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
    PRE_RACE = "pre_race"
    POST_RACE = "post_race"
    WARNING_POPUP = "warning_popup"
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


class Condition(str, Enum):
    """Negative conditions affecting the trainee."""

    NIGHT_OWL = "night_owl"          # Random energy drain each turn
    MIGRAINE = "migraine"            # Blocks mood improvement
    SKIN_OUTBREAK = "skin_outbreak"  # Random mood decrease
    SLACKER = "slacker"              # May skip training
    PRACTICE_POOR = "practice_poor"  # Reduced training gains
    OVERWEIGHT = "overweight"        # Reduced speed, stamina
    SHARP = "sharp"                  # Positive: +training gains (NOT negative)
    CHARMING = "charming"            # Positive: bond gain bonus


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
    SHOP = "shop"
    CHOOSE_EVENT = "choose_event"
    BUY_SKILL = "buy_skill"
    SKIP_SKILL = "skip_skill"
    USE_ITEM = "use_item"
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
    # Stat gains OCR'd from the tile: {stat_name: gain_value}
    stat_gains: dict[str, int] = field(default_factory=dict)
    # Bond meter levels per card (0-100), read from portrait gauge bars.
    # Ordered top to bottom matching support_cards list.
    bond_levels: list[int] = field(default_factory=list)

    @property
    def total_stat_gain(self) -> int:
        """Sum of all stat gains from this tile."""
        return sum(self.stat_gains.values())


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
class RaceOption:
    """A race available in the race list screen."""
    name: str = ""
    grade: str = ""              # G1, G2, G3, OP, Pre-OP
    distance: int = 0            # metres
    surface: str = "turf"        # turf | dirt
    season: str = ""
    fan_reward: int = 0
    is_goal_race: bool = False   # Part of career goals
    position: int = 0            # Index in the visible list (for tapping)
    tap_coords: tuple[int, int] = (0, 0)
    # True if the distance/surface text is highlighted yellow on the race list,
    # indicating B or better aptitude. White text = C or worse.
    is_aptitude_ok: bool = True
    is_rival_race: bool = False  # Trackblazer "VS" rival race
    # Banner id from the plaque matcher. Multiple races may share a banner
    # (e.g. same race across Classic/Senior year), so this alone is not a
    # unique race id -- combine with current-turn context to disambiguate.
    banner_id: int | None = None


@dataclass
class ScenarioShopItem:
    """One offering in the Trackblazer rotating shop, sourced from
    ``free_data_set.pick_up_item_info_array``.

    Mirrors the typed schema's ``TrackblazerShopItem`` but with the bot's
    semantic key (``ITEM_CATALOGUE`` key) attached when known. Consumers
    that don't care about the semantic key can read ``item_id`` directly.
    """

    shop_item_id: int = 0
    item_id: int = 0                 # master.mdb item_id
    item_key: str = ""               # ITEM_CATALOGUE key, "" if unmapped
    coin_num: int = 0                # current price (post-sale)
    original_coin_num: int = 0       # pre-sale price
    item_buy_num: int = 0            # copies already bought this rotation
    limit_buy_count: int = 0         # max purchases this rotation
    limit_turn: int = 0              # rotation expiry turn (0 = no limit)

    @property
    def stock_remaining(self) -> int:
        return max(0, self.limit_buy_count - self.item_buy_num)

    @property
    def is_on_sale(self) -> bool:
        return self.original_coin_num > 0 and self.coin_num < self.original_coin_num


@dataclass
class ScenarioInventoryEntry:
    """One owned item from the Trackblazer career inventory
    (``free_data_set.user_item_info_array``)."""

    item_id: int = 0          # master.mdb item_id
    item_key: str = ""        # ITEM_CATALOGUE key, "" if unmapped
    num: int = 0              # quantity owned


@dataclass
class ActiveItemEffect:
    """One active item effect from ``free_data_set.item_effect_array``.

    The server emits one entry per (use_id, effect_type) pair, so a single
    item activation may produce multiple entries (e.g. ankle weights have
    both a +50% stat-gain effect and a -20% failure-rate effect on the same
    use_id). Consumers that just want "is item X currently active?" should
    dedupe by ``item_id`` / ``item_key``.
    """

    use_id: int = 0              # server-assigned activation serial
    item_id: int = 0             # master.mdb item_id
    item_key: str = ""           # ITEM_CATALOGUE key, "" if unmapped
    effect_type: int = 0         # raw effect category (11=stat-mult, 12=failure-rate, 14=...)
    effect_value_1: int = 0      # primary parameter (e.g. stat_type for ankle weights)
    effect_value_2: int = 0      # magnitude (e.g. 50 = +50%)
    effect_value_3: int = 0
    effect_value_4: int = 0
    begin_turn: int = 0          # first turn the effect is live (inclusive)
    end_turn: int = 0            # last turn the effect is live (inclusive)

    def turns_remaining(self, current_turn: int) -> int:
        """How many turns the effect still covers, including ``current_turn``."""
        if current_turn <= 0:
            return 0
        return max(0, self.end_turn - current_turn + 1)


@dataclass
class ScenarioState:
    """Scenario-specific overlay state from the per-turn sidecar packet.

    Currently populated only for Trackblazer (``free_data_set``); other
    scenarios may extend this in the future. When ``GameState.scenario_state``
    is non-None, the shop manager prefers it over OCR-derived inventory and
    shop offerings.
    """

    scenario_key: str = ""              # "trackblazer" when known
    coin: int = 0                       # current scenario coin balance
    score: int = 0                      # Trackblazer Result Pts (a.k.a. win_points)
    pick_up_items: list[ScenarioShopItem] = field(default_factory=list)
    inventory: list[ScenarioInventoryEntry] = field(default_factory=list)
    # Active item effects (megaphones, ankle weights, etc.) sourced from
    # ``free_data_set.item_effect_array``. Replaces the OCR popup at
    # ``auto_turn._detect_active_effects`` when the packet is fresh.
    active_effects: list[ActiveItemEffect] = field(default_factory=list)


@dataclass
class UpcomingRace:
    """A race scheduled to be available this career, sourced from the
    server's ``race_condition_array`` (training-home response packet).

    This is the packet-driven counterpart to one entry of
    ``data/race_calendar.json``. When ``GameState.upcoming_races`` is
    non-empty, the race selector prefers it over the static JSON.

    Field shapes mirror ``data/race_calendar.json`` so consumers in
    ``race_selector.py`` can treat both sources uniformly:

    * ``grade`` is the human-readable string ("G1", "G2", "G3", "OP",
      "Pre-OP", "") — not the master.mdb integer code.
    * ``surface`` is "turf" or "dirt".
    * ``month`` is 1..12, ``half`` is "early" or "late".
    """

    program_id: int = 0          # single_mode_program.id
    race_id: int = 0             # race.id (master.mdb)
    name: str = ""               # localized race name from text_data category 38
    grade: str = ""              # G1 / G2 / G3 / OP / Pre-OP
    distance_m: int = 0          # race_course_set.distance
    surface: str = "turf"        # turf | dirt
    month: int = 0               # 1..12
    half: str = ""               # "early" | "late"
    weather: int = 0             # server-provided current weather (1..4)
    ground_condition: int = 0    # server-provided current ground (1..4)


@dataclass
class SkillOption:
    skill_id: str = ""
    name: str = ""
    cost: int = 0
    stat_boost: dict[str, int] = field(default_factory=dict)
    is_hint_skill: bool = False
    hint_level: int = 0  # 0 = no hint, 1-5 = hint level (30% OFF at lvl 1+)
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
    energy_post_training: int | None = None  # Energy after selected training
    energy_recovery: int = 0                 # Recovery preview (Wit-type training)
    mood: Mood = Mood.NORMAL
    training_tiles: list[TrainingTile] = field(default_factory=list)
    support_cards: list[SupportCard] = field(default_factory=list)
    career_goals: list[CareerGoal] = field(default_factory=list)
    current_turn: int = 0
    max_turns: int = 72
    scenario: str = "ura_finale"
    fan_count: int = 0
    event_text: str = ""
    event_choices: list[EventChoice] = field(default_factory=list)
    available_skills: list[SkillOption] = field(default_factory=list)
    # Packet-driven skill rosters (populated from chara_info + master.mdb when
    # SessionTailer is fresh). available_skills above stays for the OCR path.
    buyable_skills: list[Any] = field(default_factory=list)  # list[BuyableSkill]
    owned_skill_ids: set[int] = field(default_factory=set)
    disabled_skill_ids: set[int] = field(default_factory=set)
    available_races: list[RaceOption] = field(default_factory=list)
    # Server-authoritative race lookahead from race_condition_array; when
    # non-empty, race_selector prefers this over data/race_calendar.json.
    upcoming_races: list[UpcomingRace] = field(default_factory=list)
    skill_pts: int = 0
    # Trainee aptitudes read from the stats page at run start.
    # Keys: short, mile, medium, long, turf, dirt. Values: S/A/B/C/D/E/F/G.
    active_conditions: list[Condition] = field(default_factory=list)
    # Lowercase string keys mirroring scripts.auto_turn.CONDITION_CURES /
    # POSITIVE_KEYWORDS, populated by the packet path from
    # ``chara_info.chara_effect_id_array``. Parallel to ``active_conditions``
    # rather than a replacement so the OCR fallback path still works.
    condition_keys: list[str] = field(default_factory=list)
    positive_statuses: list[str] = field(default_factory=list)
    trainee_aptitudes: dict[str, str] = field(default_factory=dict)
    # TS Climax state (only populated during Twinkle Star Climax phase)
    ts_climax_races_done: int = 0   # e.g. 0 in "0/3 Races"
    ts_climax_races_total: int = 0  # e.g. 3 in "0/3 Races" (0 = not in TS Climax)
    ts_climax_pts: int = 0          # ranking points
    is_race_day: bool = False       # True when career home shows "Race Day" (no training/rest)
    result_pts: int = 0                 # Trackblazer Result Pts (e.g. 300)
    all_bonds_maxed: bool = False       # True when all support cards have bond >= 80
    result_pts_target: int = 0           # Target Result Pts for current year (e.g. 300)
    # Scenario-specific sidecar state from the live packet (Trackblazer for
    # now). Non-None when free_data_set or equivalent was present in the
    # response; consumers (shop_manager) prefer this over OCR fallback.
    scenario_state: "ScenarioState | None" = None
    confidence: float = 1.0  # Assembler confidence in this reading
    raw_detections: list[Any] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def is_early_game(self) -> bool:
        """Deprecated: use scenario.is_phase(turn, 'early_game') instead."""
        return self.current_turn < self.max_turns * 0.333

    @property
    def is_late_game(self) -> bool:
        """Deprecated: use scenario.is_phase(turn, 'late_game') instead."""
        return self.current_turn > self.max_turns * 0.694


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
