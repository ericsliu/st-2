"""Typed schema for CarrotJuicer-style decrypted msgpack packets.

This package provides Python dataclasses that mirror the shape of the
msgpack dicts emitted by the game server (decrypted/decompressed by a
CarrotJuicer-style hook). Downstream consumers parse raw dicts into
these typed objects via ``parser.parse_packet(raw)``.

Top-level modules:

- ``enums``         : integer/string enums (scenario_id, command_id, mood, ...)
- ``career``        : per-turn trainee state (``CharaInfo``, turn, fans, energy)
- ``training_state``: training home screen (``HomeInfo``, ``CommandInfo``)
- ``scenario_data`` : Venus / Live / Arc / Sport / Cook / Mecha / Team sidecars
- ``support_cards`` : support card lineup and bond/evaluation state
- ``events``        : unchecked event objects and choice metadata
- ``skills``        : owned skill list, skill hints, skill purchase requests
- ``race``          : race start info, race reward info, race_scenario blob
- ``packets``       : top-level ``GamePacket`` and routing helpers
- ``parser``        : main orchestrator (``parse_packet``)

See README.md in this directory for example raw msgpack structures.
"""

from .career import CharaInfo, ReservedRace, TrainingLevelInfo
from .enums import (
    ActionType,
    BondBand,
    COMMAND_ID_TO_STAT_KEY,
    CommandId,
    CommandType,
    ContinueType,
    Motivation,
    PacketDirection,
    ParamTargetType,
    RaceDistance,
    RaceGroundType,
    RunningStyle,
    ScenarioId,
    SUPPORT_CARD_TYPE_MAP,
    SupportCardType,
    TemptationMode,
)
from .events import (
    ChoiceReward,
    EventChoice,
    EventChoiceRequest,
    EventContentsInfo,
    GainParam,
    UncheckedEvent,
)
from .packets import GamePacket, PacketKind, detect_packet_kind
from .parser import iter_packets, parse_packet, parse_request, parse_response
from .race import (
    HorseResult,
    RaceCondition,
    RaceHorseData,
    RaceRewardInfo,
    RaceRewardItem,
    RaceScenarioDecoded,
    RaceStartInfo,
)
from .scenario_data import (
    ArcDataSet,
    CookDataSet,
    FreeDataSet,
    LiveDataSet,
    MechaDataSet,
    OwnedItem,
    RivalRaceInfo,
    SportDataSet,
    TeamDataSet,
    TrackblazerShopItem,
    VenusDataSet,
)
from .skills import SkillEntry, SkillGainRequest, SkillHintEntry, SkillPurchaseRequest
from .support_cards import (
    EvaluationInfo,
    SupportCardRef,
    TipsEventPartnerRef,
    TrainingPartnerRef,
)
from .training_state import (
    CommandInfo,
    CommandResult,
    HomeInfo,
    ParameterBoundInfo,
    ParamsIncDecInfo,
)

__all__ = [
    # enums
    "ActionType", "BondBand", "COMMAND_ID_TO_STAT_KEY", "CommandId",
    "CommandType", "ContinueType", "Motivation", "PacketDirection",
    "ParamTargetType", "RaceDistance", "RaceGroundType", "RunningStyle",
    "ScenarioId", "SUPPORT_CARD_TYPE_MAP", "SupportCardType", "TemptationMode",
    # career
    "CharaInfo", "ReservedRace", "TrainingLevelInfo",
    # events
    "ChoiceReward", "EventChoice", "EventChoiceRequest", "EventContentsInfo",
    "GainParam", "UncheckedEvent",
    # packets / parser
    "GamePacket", "PacketKind", "detect_packet_kind",
    "iter_packets", "parse_packet", "parse_request", "parse_response",
    # race
    "HorseResult", "RaceCondition", "RaceHorseData", "RaceRewardInfo",
    "RaceRewardItem", "RaceScenarioDecoded", "RaceStartInfo",
    # scenario_data
    "ArcDataSet", "CookDataSet", "FreeDataSet", "LiveDataSet", "MechaDataSet",
    "OwnedItem", "RivalRaceInfo", "SportDataSet", "TeamDataSet",
    "TrackblazerShopItem", "VenusDataSet",
    # skills
    "SkillEntry", "SkillGainRequest", "SkillHintEntry", "SkillPurchaseRequest",
    # support_cards
    "EvaluationInfo", "SupportCardRef", "TipsEventPartnerRef",
    "TrainingPartnerRef",
    # training_state
    "CommandInfo", "CommandResult", "HomeInfo", "ParameterBoundInfo",
    "ParamsIncDecInfo",
]
