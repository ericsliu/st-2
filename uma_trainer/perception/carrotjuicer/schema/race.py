"""Race response dataclasses.

Two distinct race-related response shapes are mixed together:

* ``race_scenario`` (bytes / base64 string) + ``race_start_info`` - emitted
  at race start. The bytes blob is a custom binary stream documented by
  Hakuraku\u2019s ``RaceDataParser.ts`` (header + frame data + horse results
  + in-race events). We only capture the outer envelope here; decoding the
  blob is delegated to an optional helper (see ``decode_race_scenario``).
* ``race_reward_info`` - emitted when a race result screen is entered.
  Contains the trainee finishing position and post-race rewards.

Source confidence: HIGH on ``race_start_info.program_id`` and
``race_horse_data[0]`` field names (UmaLauncher reads them every race).
MEDIUM on the inner ``horse_data`` fields (we mirror UL\u2019s keys verbatim).
LOW on ``race_reward_info`` field names beyond ``result_rank``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .skills import SkillEntry


@dataclass
class RaceHorseData:
    """``race_start_info.race_horse_data[i]`` - per-horse pre-race state.

    Entry 0 is always the trainee; remaining entries are the AI field.
    Field names sourced from UmaLauncher ``training_tracker.py`` block
    that constructs a Race action.
    """

    frame_order: int = 0  # msgpack key: "frame_order"
    """1-based starting gate position (used as index into horse_result)."""

    speed: int = 0  # msgpack key: "speed"
    stamina: int = 0  # msgpack key: "stamina"
    pow: int = 0  # msgpack key: "pow"  (note: not "power" at race time)
    guts: int = 0  # msgpack key: "guts"
    wiz: int = 0  # msgpack key: "wiz"
    motivation: int = 3  # msgpack key: "motivation"
    fan_count: int = 0  # msgpack key: "fan_count"
    skill_array: list = field(default_factory=list)  # msgpack key: "skill_array"
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        if raw is None:
            return cls()
        known = {
            "frame_order", "speed", "stamina", "pow", "guts", "wiz",
            "motivation", "fan_count", "skill_array",
        }
        return cls(
            frame_order=int(raw.get("frame_order", 0)),
            speed=int(raw.get("speed", 0)),
            stamina=int(raw.get("stamina", 0)),
            pow=int(raw.get("pow", 0)),
            guts=int(raw.get("guts", 0)),
            wiz=int(raw.get("wiz", 0)),
            motivation=int(raw.get("motivation", 3)),
            fan_count=int(raw.get("fan_count", 0)),
            skill_array=[SkillEntry.from_raw(x) for x in raw.get("skill_array", []) or []],
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class RaceStartInfo:
    """``race_start_info`` block on a race response."""

    program_id: int  # msgpack key: "program_id"
    """Race program id; joins to master.mdb ``single_mode_program``."""

    random_seed: int = 0  # msgpack key: "random_seed"
    season: int = 0  # msgpack key: "season"  (1=spring, 2=summer, 3=autumn, 4=winter)
    weather: int = 0  # msgpack key: "weather"  (1=sunny, 2=cloudy, 3=rain, 4=snow)
    ground_condition: int = 0  # msgpack key: "ground_condition"  (1=firm..4=heavy)
    continue_num: int = 0  # msgpack key: "continue_num"  (retries already spent)

    race_horse_data: list = field(default_factory=list)
    # msgpack key: "race_horse_data"  (trainee at [0])

    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        if raw is None:
            return cls(program_id=0)
        known = {
            "program_id", "race_horse_data",
            "random_seed", "season", "weather", "ground_condition",
            "continue_num",
        }
        return cls(
            program_id=int(raw.get("program_id", 0)),
            random_seed=int(raw.get("random_seed", 0)),
            season=int(raw.get("season", 0)),
            weather=int(raw.get("weather", 0)),
            ground_condition=int(raw.get("ground_condition", 0)),
            continue_num=int(raw.get("continue_num", 0)),
            race_horse_data=[
                RaceHorseData.from_raw(x)
                for x in raw.get("race_horse_data", []) or []
            ],
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class RaceRewardItem:
    """Entry in ``race_reward_info.race_reward*`` arrays.

    Each reward is a typed tuple of item_type + item_id + quantity. The
    bot cares mostly about fan gain (gained_fans) and skill point rewards
    for goal races, but item rewards surface here too (grade points,
    tickets, items into the inventory).
    """

    item_type: int = 0  # msgpack key: "item_type"
    item_id: int = 0  # msgpack key: "item_id"
    item_num: int = 0  # msgpack key: "item_num"
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        if not isinstance(raw, dict):
            return cls()
        known = {"item_type", "item_id", "item_num"}
        return cls(
            item_type=int(raw.get("item_type", 0)),
            item_id=int(raw.get("item_id", 0)),
            item_num=int(raw.get("item_num", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class RaceRewardInfo:
    """``race_reward_info`` block on a post-race response."""

    result_rank: int = 0  # msgpack key: "result_rank"
    """Finishing position, 1-indexed. 1 = won."""

    result_time: int = 0  # msgpack key: "result_time"
    """Finish time in tenths of ms. Divide by 10000 for seconds."""

    gained_fans: int = 0  # msgpack key: "gained_fans"

    race_reward: list = field(default_factory=list)
    """Base rewards (one ``RaceRewardItem`` per granted item/currency)."""
    race_reward_bonus: list = field(default_factory=list)
    """Bonus rewards (e.g. scenario campaign bonuses)."""
    race_reward_plus_bonus: list = field(default_factory=list)
    """Extra 'plus' bonus layer (varies by scenario / event)."""
    race_reward_bonus_win: list = field(default_factory=list)
    """Win-only bonus rewards (granted on 1st place)."""

    campaign_id_array: list = field(default_factory=list)
    """Active campaign IDs that modified this race's rewards."""

    extras: dict = field(default_factory=dict)
    """Any reward keys not mirrored into typed fields."""

    @classmethod
    def from_raw(cls, raw):
        if raw is None:
            return cls()
        known = {
            "result_rank", "result_time", "gained_fans",
            "race_reward", "race_reward_bonus",
            "race_reward_plus_bonus", "race_reward_bonus_win",
            "campaign_id_array",
        }
        return cls(
            result_rank=int(raw.get("result_rank", 0)),
            result_time=int(raw.get("result_time", 0)),
            gained_fans=int(raw.get("gained_fans", 0)),
            race_reward=[RaceRewardItem.from_raw(x) for x in raw.get("race_reward", []) or []],
            race_reward_bonus=[RaceRewardItem.from_raw(x) for x in raw.get("race_reward_bonus", []) or []],
            race_reward_plus_bonus=[RaceRewardItem.from_raw(x) for x in raw.get("race_reward_plus_bonus", []) or []],
            race_reward_bonus_win=[RaceRewardItem.from_raw(x) for x in raw.get("race_reward_bonus_win", []) or []],
            campaign_id_array=list(raw.get("campaign_id_array", []) or []),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class RaceCondition:
    """Entry in response-root ``race_condition_array`` - upcoming race state.

    The server ships this alongside ``chara_info`` on training-home responses.
    It lists every race currently relevant (goal + optional + scenario races)
    with its current weather + ground_condition. ``program_id`` joins to
    master.mdb ``single_mode_program`` for turn / grade / distance info.

    Replaces the bot's ``data/race_calendar.json`` lookahead — the server's
    authoritative view of which races exist this career.
    """

    program_id: int = 0  # msgpack key: "program_id"
    weather: int = 0  # msgpack key: "weather"
    ground_condition: int = 0  # msgpack key: "ground_condition"
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw):
        if not isinstance(raw, dict):
            return cls()
        known = {"program_id", "weather", "ground_condition"}
        return cls(
            program_id=int(raw.get("program_id", 0)),
            weather=int(raw.get("weather", 0)),
            ground_condition=int(raw.get("ground_condition", 0)),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class HorseResult:
    """A single entry of the decoded ``race_scenario.horse_result``.

    Fields mirror Hakuraku\u2019s ``RaceSimulateHorseResultData`` (race_data.proto).
    Decoding of the binary ``race_scenario`` blob is optional; when it runs,
    finish_order is the server\u2019s authoritative verdict.
    """

    finish_order: int = 0
    """0-based finish position. Add 1 for human-readable rank."""
    finish_time: float = 0.0
    finish_diff_time: float = 0.0
    start_delay_time: float = 0.0
    guts_order: int = 0
    wiz_order: int = 0
    last_spurt_start_distance: float = 0.0
    running_style: int = 0
    defeat: int = 0
    finish_time_raw: float = 0.0


@dataclass
class RaceScenarioDecoded:
    """Partial decode of the ``race_scenario`` binary blob.

    The authoritative parser lives in Hakuraku ``RaceDataParser.ts``. For
    training-log use we only need finishing order per horse; everything else
    is left as raw bytes in ``raw`` for an optional deep decoder.
    """

    horse_result: list = field(default_factory=list)
    raw: Optional[bytes] = None


__all__ = [
    "HorseResult",
    "RaceCondition",
    "RaceHorseData",
    "RaceRewardInfo",
    "RaceRewardItem",
    "RaceScenarioDecoded",
    "RaceStartInfo",
]
