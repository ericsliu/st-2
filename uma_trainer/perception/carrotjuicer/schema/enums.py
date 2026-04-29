"""Enumerations referenced by CarrotJuicer packet fields.

Most values are sourced from:

- UmaLauncher ``constants.py`` (SCENARIO_DICT, MOTIVATION_DICT,
  COMMAND_ID_TO_KEY, SUPPORT_CARD_TYPE_DICT, BOND_COLOR_DICT) and
  ``training_tracker.py`` (ActionType, CommandType enums).
- Hakuraku ``race_data.proto`` (RunningStyle, TemptationMode,
  RaceEventType).

When a numeric game value does not have a documented label we keep the raw
integer and expose convenience constants. Prefer the enum where possible so
that downstream code (scoring, logging, UI) is self-documenting.
"""

from __future__ import annotations

from enum import Enum, IntEnum


class ScenarioId(IntEnum):
    """Training scenario identifier (``chara_info.scenario_id``).

    Sourced from UmaLauncher ``constants.SCENARIO_DICT``.
    """

    URA_FINALE = 1
    AOHARU_CUP = 2
    GRAND_LIVE = 3
    MAKING_OF_NEW_TRAINER = 4  # aka MANT
    GRAND_MASTERS = 5  # aka Venus
    PROJECT_LARC = 6
    UAF_READY_GO = 7  # aka Sport
    GREAT_FOOD_FESTIVAL = 8  # aka Cook
    RUN_MECHA_UMAMUSUME = 9  # aka Mecha
    TRACKBLAZER = 10  # tentative; verify with live packet


class Motivation(IntEnum):
    """Motivation/mood level (``chara_info.motivation``).

    1=Very Low, 2=Bad, 3=Normal, 4=Good, 5=Very High.
    """

    VERY_LOW = 1
    BAD = 2
    NORMAL = 3
    GOOD = 4
    GREAT = 5


class CommandType(IntEnum):
    """High-level request command_type (sent in client requests).

    Inferred from UmaLauncher ``TrainingAnalyzer.determine_action_type``.
    """

    TRAINING = 1
    OUTING = 3
    REST = 7
    INFIRMARY = 8


class CommandId(IntEnum):
    """Training command identifier (``command_info_array[].command_id``).

    Sourced from UmaLauncher ``training_tracker.CommandType``.
    """

    SPEED = 101
    POWER = 102
    GUTS = 103
    STAMINA = 105
    WISDOM = 106

    SUMMER_SPEED = 601
    SUMMER_STAMINA = 602
    SUMMER_POWER = 603
    SUMMER_GUTS = 604
    SUMMER_WISDOM = 605

    OVERSEAS_SPEED = 1101
    OVERSEAS_STAMINA = 1102
    OVERSEAS_POWER = 1103
    OVERSEAS_GUTS = 1104
    OVERSEAS_WISDOM = 1105

    MACHINE_GUN_RECEIVE = 2101
    HELL_SWIM_SHOOT = 2102
    MOUNTAIN_DUNK = 2103
    UNLIMITED_LIFTING = 2104
    SNIPE_BALL = 2105

    GODSPEED_KARATE = 2201
    PUSH_THE_ROCK = 2202
    HARITE_PILE = 2203
    GIGANTIC_THROW = 2204
    SONIC_FENCING = 2205

    HYPER_JUMP = 2301
    HANG_CLIMB = 2302
    DYNAMIC_HAMMER = 2303
    LIKE_A_SUBMARINE = 2304
    ACROBAT_ARROW = 2305


COMMAND_ID_TO_STAT_KEY = {
    101: "speed",
    102: "power",
    103: "guts",
    105: "stamina",
    106: "wiz",
    601: "speed",
    602: "stamina",
    603: "power",
    604: "guts",
    605: "wiz",
    1101: "speed",
    1102: "stamina",
    1103: "power",
    1104: "guts",
    1105: "wiz",
}
"""Maps ``CommandId`` -> the ``chara_info`` key that holds the matching stat.

Sourced from UmaLauncher ``constants.COMMAND_ID_TO_KEY``. ``wiz`` is the
msgpack key for wisdom.
"""


class ParamTargetType(IntEnum):
    """Stat category in ``params_inc_dec_info_array[].target_type``.

    1=Speed, 2=Stamina, 3=Power, 4=Guts, 5=Wisdom. Inferred from UmaLauncher
    helper_table usage. Verify with live packets - some scenarios extend this.
    """

    SPEED = 1
    STAMINA = 2
    POWER = 3
    GUTS = 4
    WISDOM = 5
    ENERGY = 10  # tentative - vital delta
    MAX_ENERGY = 11  # tentative
    MOTIVATION = 20  # tentative
    SKILL_PT = 30  # tentative


class SupportCardType(Enum):
    """Support card archetype derived from ``(command_id, card_type)``.

    Sourced from UmaLauncher ``SUPPORT_CARD_TYPE_DICT``.
    """

    SPEED = "speed"
    STAMINA = "stamina"
    POWER = "power"
    GUTS = "guts"
    WISDOM = "wisdom"
    FRIEND = "friend"
    GROUP = "group"


SUPPORT_CARD_TYPE_MAP = {
    (101, 1): SupportCardType.SPEED,
    (105, 1): SupportCardType.STAMINA,
    (102, 1): SupportCardType.POWER,
    (103, 1): SupportCardType.GUTS,
    (106, 1): SupportCardType.WISDOM,
    (0, 2): SupportCardType.FRIEND,
    (0, 3): SupportCardType.GROUP,
}


class BondBand(IntEnum):
    """Bond bands used by the in-game UI colour ring.

    Each band lower bound corresponds to a ``BOND_COLOR_DICT`` key in
    UmaLauncher. The actual bond value (``evaluation`` field) ranges 0-100.
    """

    GREY = 0
    GREEN = 60
    ORANGE = 80
    YELLOW = 100  # maxed / rainbow eligible


class ActionType(IntEnum):
    """High-level action the bot performed on a given turn.

    Derived after packet parsing; not a raw msgpack field. Sourced verbatim
    from UmaLauncher ``training_tracker.ActionType``. Negative values are
    ignorable (preludes to a real action).
    """

    AFTER_RACE_2 = -2
    BEFORE_RACE = -1
    UNKNOWN = 0
    START = 1
    END = 2
    TRAINING = 3
    EVENT = 4
    RACE = 5
    SKILL_HINT = 6
    BUY_SKILL = 7
    REST = 8
    OUTING = 9
    INFIRMARY = 10
    GODDESS_WISDOM = 11  # Grand Masters scenario
    BUY_ITEM = 12  # MANT scenario
    USE_ITEM = 13  # MANT scenario
    LESSON = 14  # Grand Live scenario
    AFTER_RACE = 15
    CONTINUE = 16
    AOHARU_RACES = 17
    SS_MATCH = 18  # L\u2019Arc scenario


class RunningStyle(IntEnum):
    """Running style enum (``horse_result.running_style``).

    Sourced from Hakuraku race_data.proto.
    """

    NIGE = 1  # Front runner
    SENKO = 2  # Pace chaser
    SASHI = 3  # Late surger
    OIKOMI = 4  # End closer


class TemptationMode(IntEnum):
    """Horse frame-level temptation (``horse_frame.temptation_mode``).

    Sourced from Hakuraku race_data.proto.
    """

    NONE = 0
    POSITION_SASHI = 1
    SASHI_OIKOMI = 2
    BOOST = 3  # speed boost event


class RaceGroundType(IntEnum):
    """Turf vs dirt (``RaceInstance.ground_type``).

    Sourced from Hakuraku data.proto ``GroundType``.
    """

    UNKNOWN = 0
    TURF = 1
    DIRT = 2


class RaceDistance(IntEnum):
    """Race distance bucket (derived; not a raw msgpack field).

    Lookup derived via ``single_mode_program -> race -> distance`` in
    master.mdb. The packet exposes ``program_id`` only.
    """

    SPRINT = 1  # 1000-1400m
    MILE = 2  # 1401-1800m
    MEDIUM = 3  # 1801-2400m
    LONG = 4  # 2401m+


class ContinueType(IntEnum):
    """Request ``continue_type`` after a failed race / missed goal.

    Derived from UmaLauncher ``TrainingAnalyzer``. Tentative mapping.
    """

    UNSPECIFIED = 0
    RETRY = 1
    ACCEPT = 2


class PacketDirection(IntEnum):
    """Injected into every packet dict by the UmaLauncher tracker.

    ``_direction`` is 0 for a client->server request, 1 for a server->client
    response. This is not an on-the-wire msgpack field; it is added by the
    CarrotJuicer-side wrapper before the packet is serialized to disk.
    """

    REQUEST = 0
    RESPONSE = 1


__all__ = [
    "ActionType",
    "BondBand",
    "COMMAND_ID_TO_STAT_KEY",
    "CommandId",
    "CommandType",
    "ContinueType",
    "Motivation",
    "PacketDirection",
    "ParamTargetType",
    "RaceDistance",
    "RaceGroundType",
    "RunningStyle",
    "ScenarioId",
    "SUPPORT_CARD_TYPE_MAP",
    "SupportCardType",
    "TemptationMode",
]
