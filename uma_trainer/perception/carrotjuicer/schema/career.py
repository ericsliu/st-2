"""Per-turn trainee state: the ``chara_info`` object.

On every turn-response packet the server emits a ``chara_info`` dict that
describes the trainee\u2019s full public state: stats, energy, mood, fans,
and turn number.  UmaLauncher\u2019s ``TrainingAnalyzer`` and
``helper_table.py`` document every key we mirror here; keys not (yet)
mirrored get dropped into ``extras`` for future schema extension.

Confidence: HIGH. All of these fields are read on every turn and the
name/shape has been stable since UmaLauncher v1 (2022+).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .support_cards import EvaluationInfo, SupportCardRef
from .skills import SkillEntry, SkillHintEntry


@dataclass
class ReservedRace:
    """Entry in ``chara_info.reserved_race_array[].race_array`` (upcoming races).

    ``reserved_race_array[0].race_array`` is a list of scheduled races; each
    entry has the program_id and year the race is scheduled for.
    """

    program_id: int  # msgpack key: "program_id"
    """Race program ID; joins to ``single_mode_program`` in master.mdb."""

    year: Optional[int] = None  # msgpack key: "year"
    """Race year (Junior=1, Classic=2, Senior=3). Sometimes omitted."""

    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "ReservedRace":
        known = {"program_id", "year"}
        return cls(
            program_id=int(raw.get("program_id", 0)),
            year=raw.get("year"),
            extras={k: v for k, v in raw.items() if k not in known},
        )


@dataclass
class TrainingLevelInfo:
    """Entry in ``chara_info.training_level_info_array``.

    Tracks the level of each training command (or facility) the trainee has
    leveled up. ``command_id`` 101..105 = Speed/Stamina/Power/Guts/Wit
    training, 106 = social/recreation, 601..605 = facility-level markers
    (the 5 at startup reflects fully-upgraded base levels).
    """

    command_id: int = 0  # msgpack key: "command_id"
    level: int = 0  # msgpack key: "level"

    @classmethod
    def from_raw(cls, raw):
        if not isinstance(raw, dict):
            return cls()
        return cls(
            command_id=int(raw.get("command_id", 0)),
            level=int(raw.get("level", 0)),
        )


@dataclass
class CharaInfo:
    """The ``chara_info`` block attached to almost every training response.

    Confidence legend:
      H=high  (observed in every packet we know of)
      M=med   (observed, field meaning not 100% nailed down)
      L=low   (existence assumed from UL code, needs live verification)
    """

    # --- identifiers -----------------------------------------------------
    card_id: int  # msgpack key: "card_id"  (H)
    """Trainee character + outfit id. First 4 digits = character id.
    Joins to master.mdb ``card_data``."""

    scenario_id: int  # msgpack key: "scenario_id"  (H)
    """Scenario identifier. Use ``enums.ScenarioId`` to decode."""

    turn: int  # msgpack key: "turn"  (H)
    """Current turn number. 0 = pre-start, 1 = first training turn."""

    start_time: Optional[int] = None  # msgpack key: "start_time"  (M)
    """Epoch second of training start; UmaLauncher uses as a run id."""

    # --- stats -----------------------------------------------------------
    speed: int = 0  # msgpack key: "speed"  (H)
    stamina: int = 0  # msgpack key: "stamina"  (H)
    power: int = 0  # msgpack key: "power"  (H)
    guts: int = 0  # msgpack key: "guts"  (H)
    wiz: int = 0  # msgpack key: "wiz"  (H)
    """Wisdom stat. Note the msgpack key is ``wiz`` (not ``wisdom`` or ``int``).
    This is a consistent gotcha across all UmaLauncher-based tooling.
    """

    # --- resources -------------------------------------------------------
    vital: int = 0  # msgpack key: "vital"  (H)
    """Current energy (0 up to ``max_vital``)."""
    max_vital: int = 100  # msgpack key: "max_vital"  (H)
    """Maximum energy, raised by summer camp and certain items."""

    motivation: int = 3  # msgpack key: "motivation"  (H)
    """Mood 1-5. Use ``enums.Motivation`` to decode."""

    fans: int = 0  # msgpack key: "fans"  (H)
    """Total fan count; used to gate certain races."""

    skill_point: int = 0  # msgpack key: "skill_point"  (H)
    """Spendable SP for the skill shop."""

    talent_level: Optional[int] = None  # msgpack key: "talent_level"  (M)
    """Trainee\u2019s unique-skill level (0-3) if applicable."""

    # --- stat caps -------------------------------------------------------
    max_speed: int = 0  # msgpack key: "max_speed"  (H)
    max_stamina: int = 0  # msgpack key: "max_stamina"  (H)
    max_power: int = 0  # msgpack key: "max_power"  (H)
    max_guts: int = 0  # msgpack key: "max_guts"  (H)
    max_wiz: int = 0  # msgpack key: "max_wiz"  (H)

    # --- aptitudes (1..8, higher is better: G..S) ------------------------
    proper_distance_short: int = 0  # msgpack key: "proper_distance_short"
    proper_distance_mile: int = 0
    proper_distance_middle: int = 0
    proper_distance_long: int = 0
    proper_ground_turf: int = 0
    proper_ground_dirt: int = 0
    proper_running_style_nige: int = 0
    proper_running_style_senko: int = 0
    proper_running_style_sashi: int = 0
    proper_running_style_oikomi: int = 0

    # --- base / default caps (pre-summer / pre-growth-bonus) -------------
    default_max_speed: int = 0  # msgpack key: "default_max_speed"
    default_max_stamina: int = 0  # msgpack key: "default_max_stamina"
    default_max_power: int = 0  # msgpack key: "default_max_power"
    default_max_guts: int = 0  # msgpack key: "default_max_guts"
    default_max_wiz: int = 0  # msgpack key: "default_max_wiz"
    """Stat caps ignoring growth-rate bonuses. ``max_*`` is what the UI
    shows; ``default_max_*`` is the baseline before scenario/card modifiers."""

    # --- trainee / scenario metadata -------------------------------------
    chara_grade: int = 0  # msgpack key: "chara_grade"
    """Overall grade (A/B/C letter bucketed as an int)."""
    rarity: int = 0  # msgpack key: "rarity"  (trainee star rarity)
    state: int = 0  # msgpack key: "state"  (career state machine position)
    playing_state: int = 0  # msgpack key: "playing_state"  (turn sub-state)
    short_cut_state: int = 0  # msgpack key: "short_cut_state"  (race-skip toggle state)

    # --- scenario / route identifiers ------------------------------------
    single_mode_chara_id: int = 0  # msgpack key: "single_mode_chara_id"
    """Trainee-chara id within the scenario. Joins to
    ``single_mode_unique_chara.chara_id`` for NPC lookups."""
    route_id: int = 0  # msgpack key: "route_id"
    route_race_id_array: list[int] = field(default_factory=list)  # msgpack key: "route_race_id_array"
    """Program ids for this career's fixed goal races."""

    # --- current race state (set when entering/resolving a race) ---------
    race_program_id: int = 0  # msgpack key: "race_program_id"
    reserve_race_program_id: int = 0  # msgpack key: "reserve_race_program_id"
    race_running_style: int = 0  # msgpack key: "race_running_style"
    is_short_race: int = 0  # msgpack key: "is_short_race"

    # --- training level per command / facility ---------------------------
    training_level_info_array: list[TrainingLevelInfo] = field(default_factory=list)
    # msgpack key: "training_level_info_array"

    # --- succession / inheritance ----------------------------------------
    succession_trained_chara_id_1: int = 0  # msgpack key: "succession_trained_chara_id_1"
    succession_trained_chara_id_2: int = 0  # msgpack key: "succession_trained_chara_id_2"

    # --- locked skills ---------------------------------------------------
    disable_skill_id_array: list[int] = field(default_factory=list)
    # msgpack key: "disable_skill_id_array"

    # --- misc ------------------------------------------------------------
    nickname_id_array: list[int] = field(default_factory=list)  # msgpack key: "nickname_id_array"
    guest_outing_info_array: list = field(default_factory=list)  # msgpack key: "guest_outing_info_array"

    # --- rosters ---------------------------------------------------------
    skill_array: list[SkillEntry] = field(default_factory=list)  # msgpack key: "skill_array"  (H)
    skill_tips_array: list[SkillHintEntry] = field(default_factory=list)  # msgpack key: "skill_tips_array"  (H)
    support_card_array: list[SupportCardRef] = field(default_factory=list)  # msgpack key: "support_card_array"  (H)
    evaluation_info_array: list[EvaluationInfo] = field(default_factory=list)  # msgpack key: "evaluation_info_array"  (H)

    # --- status ----------------------------------------------------------
    chara_effect_id_array: list[int] = field(default_factory=list)  # msgpack key: "chara_effect_id_array"  (H)
    """Active status/condition ids (e.g. headache, cold, practice-perfect).
    Joins to master.mdb ``chara_effect`` / ``text_data`` (category 142)."""

    # --- scheduled races -------------------------------------------------
    reserved_race_array: list[ReservedRace] = field(default_factory=list)  # msgpack key: "reserved_race_array"  (H)
    """Upcoming races the trainee has entered. In UmaLauncher this is the
    outer array; we flatten ``race_array`` entries into a single list here.
    """

    # --- anything else we have not mapped yet ----------------------------
    extras: dict[str, Any] = field(default_factory=dict)
    """Any chara_info keys not mirrored into typed fields (e.g. scenario
    specific side-data). Preserved for future schema expansion."""

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "CharaInfo":
        known = {
            "card_id", "scenario_id", "turn", "start_time",
            "speed", "stamina", "power", "guts", "wiz",
            "vital", "max_vital", "motivation", "fans", "skill_point",
            "talent_level",
            "max_speed", "max_stamina", "max_power", "max_guts", "max_wiz",
            "default_max_speed", "default_max_stamina", "default_max_power",
            "default_max_guts", "default_max_wiz",
            "chara_grade", "rarity", "state", "playing_state", "short_cut_state",
            "proper_distance_short", "proper_distance_mile",
            "proper_distance_middle", "proper_distance_long",
            "proper_ground_turf", "proper_ground_dirt",
            "proper_running_style_nige", "proper_running_style_senko",
            "proper_running_style_sashi", "proper_running_style_oikomi",
            "single_mode_chara_id", "route_id", "route_race_id_array",
            "race_program_id", "reserve_race_program_id",
            "race_running_style", "is_short_race",
            "training_level_info_array",
            "succession_trained_chara_id_1", "succession_trained_chara_id_2",
            "disable_skill_id_array",
            "nickname_id_array", "guest_outing_info_array",
            "skill_array", "skill_tips_array", "support_card_array",
            "evaluation_info_array", "chara_effect_id_array",
            "reserved_race_array",
        }
        # Flatten reserved_race_array[*].race_array into a single list.
        reserved = []
        for outer in raw.get("reserved_race_array", []) or []:
            if isinstance(outer, dict) and "race_array" in outer:
                for inner in outer["race_array"]:
                    reserved.append(ReservedRace.from_raw(inner))
            elif isinstance(outer, dict):
                reserved.append(ReservedRace.from_raw(outer))
        return cls(
            card_id=int(raw.get("card_id", 0)),
            scenario_id=int(raw.get("scenario_id", 0)),
            turn=int(raw.get("turn", 0)),
            start_time=raw.get("start_time"),
            speed=int(raw.get("speed", 0)),
            stamina=int(raw.get("stamina", 0)),
            power=int(raw.get("power", 0)),
            guts=int(raw.get("guts", 0)),
            wiz=int(raw.get("wiz", 0)),
            vital=int(raw.get("vital", 0)),
            max_vital=int(raw.get("max_vital", 100)),
            motivation=int(raw.get("motivation", 3)),
            fans=int(raw.get("fans", 0)),
            skill_point=int(raw.get("skill_point", 0)),
            talent_level=raw.get("talent_level"),
            max_speed=int(raw.get("max_speed", 0)),
            max_stamina=int(raw.get("max_stamina", 0)),
            max_power=int(raw.get("max_power", 0)),
            max_guts=int(raw.get("max_guts", 0)),
            max_wiz=int(raw.get("max_wiz", 0)),
            proper_distance_short=int(raw.get("proper_distance_short", 0)),
            proper_distance_mile=int(raw.get("proper_distance_mile", 0)),
            proper_distance_middle=int(raw.get("proper_distance_middle", 0)),
            proper_distance_long=int(raw.get("proper_distance_long", 0)),
            proper_ground_turf=int(raw.get("proper_ground_turf", 0)),
            proper_ground_dirt=int(raw.get("proper_ground_dirt", 0)),
            proper_running_style_nige=int(raw.get("proper_running_style_nige", 0)),
            proper_running_style_senko=int(raw.get("proper_running_style_senko", 0)),
            proper_running_style_sashi=int(raw.get("proper_running_style_sashi", 0)),
            proper_running_style_oikomi=int(raw.get("proper_running_style_oikomi", 0)),
            default_max_speed=int(raw.get("default_max_speed", 0)),
            default_max_stamina=int(raw.get("default_max_stamina", 0)),
            default_max_power=int(raw.get("default_max_power", 0)),
            default_max_guts=int(raw.get("default_max_guts", 0)),
            default_max_wiz=int(raw.get("default_max_wiz", 0)),
            chara_grade=int(raw.get("chara_grade", 0)),
            rarity=int(raw.get("rarity", 0)),
            state=int(raw.get("state", 0)),
            playing_state=int(raw.get("playing_state", 0)),
            short_cut_state=int(raw.get("short_cut_state", 0)),
            single_mode_chara_id=int(raw.get("single_mode_chara_id", 0)),
            route_id=int(raw.get("route_id", 0)),
            route_race_id_array=list(raw.get("route_race_id_array", []) or []),
            race_program_id=int(raw.get("race_program_id", 0)),
            reserve_race_program_id=int(raw.get("reserve_race_program_id", 0)),
            race_running_style=int(raw.get("race_running_style", 0)),
            is_short_race=int(raw.get("is_short_race", 0)),
            training_level_info_array=[
                TrainingLevelInfo.from_raw(x)
                for x in raw.get("training_level_info_array", []) or []
            ],
            succession_trained_chara_id_1=int(raw.get("succession_trained_chara_id_1", 0)),
            succession_trained_chara_id_2=int(raw.get("succession_trained_chara_id_2", 0)),
            disable_skill_id_array=list(raw.get("disable_skill_id_array", []) or []),
            nickname_id_array=list(raw.get("nickname_id_array", []) or []),
            guest_outing_info_array=list(raw.get("guest_outing_info_array", []) or []),
            skill_array=[SkillEntry.from_raw(x) for x in raw.get("skill_array", []) or []],
            skill_tips_array=[SkillHintEntry.from_raw(x) for x in raw.get("skill_tips_array", []) or []],
            support_card_array=[SupportCardRef.from_raw(x) for x in raw.get("support_card_array", []) or []],
            evaluation_info_array=[EvaluationInfo.from_raw(x) for x in raw.get("evaluation_info_array", []) or []],
            chara_effect_id_array=list(raw.get("chara_effect_id_array", []) or []),
            reserved_race_array=reserved,
            extras={k: v for k, v in raw.items() if k not in known},
        )

    @property
    def chara_id(self) -> int:
        """First 4 digits of ``card_id`` identifies the character proper."""
        return int(str(self.card_id)[:4]) if self.card_id else 0


__all__ = ["CharaInfo", "ReservedRace", "TrainingLevelInfo"]
