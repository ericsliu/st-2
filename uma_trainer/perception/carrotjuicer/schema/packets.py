"""Top-level packet routing.

Every decrypted msgpack envelope belongs to one of a small number of
categories. We call these ``PacketKind``.  The server never stamps a
``kind`` field on the wire, so we identify the kind by inspecting which
top-level keys are present (and optionally which request keys the bot
sent alongside).

Routing logic is reverse-engineered from UmaLauncher\u2019s
``carrotjuicer.handle_response`` / ``handle_request`` and
``training_tracker.determine_action_type``.

Confidence: HIGH for training / race / event / skill-purchase / start /
continue classifications. MEDIUM for the scenario-specific branches (we
can only guess until live capture).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

from .enums import PacketDirection


class PacketKind(Enum):
    """High-level packet classification.

    ``UNKNOWN`` is emitted when no rule matches - downstream code should log
    and pass the raw dict through rather than fail.
    """

    UNKNOWN = auto()

    # -- response (server -> client) ---------------------------------------
    TRAINING_HOME = auto()
    """Normal training turn: ``chara_info`` + ``home_info``."""

    TRAINING_SCENARIO_HOME = auto()
    """Training turn for a scenario that uses a sidecar data set."""

    EVENT_TRIGGER = auto()
    """``unchecked_event_array`` present; an event is pending resolution."""

    RACE_START = auto()
    """``race_scenario`` + ``race_start_info`` - the race is about to run."""

    RACE_RESULT = auto()
    """``race_reward_info`` - the race is complete."""

    SKILL_PURCHASE_ACK = auto()
    """Response to a client ``gain_skill_info_array`` purchase."""

    START_CAREER = auto()
    """Response to a ``start_chara`` request; training run initialisation."""

    RUN_ENDED = auto()
    """``single_mode_factor_select_common`` present - the career is done."""

    AOHARU_TEAM_RACE = auto()
    """Aoharu Cup scenario team race (``team_data_set``)."""

    LARC_SS_MATCH = auto()
    """L\u2019Arc scenario SS match (``selection_result_info``)."""

    CONCERT = auto()
    """Grand Live concert response."""

    CHOICE_REWARD_PREVIEW = auto()
    """``choice_reward_array`` present - server's preview of what each
    event choice will grant (select_index + gain_param_array)."""

    ITEM_USE_RESP = auto()
    """Response to a ``REQUEST_USE_ITEM`` (root key ``user_item``)."""

    # -- response, non-career (bootstrap / navigation) ---------------------
    BOOT_AUTH_RESP = auto()
    """Login / auth handshake response (``attest`` + ``nonce``)."""
    HOME_TOP_LOAD = auto()
    """Main-menu bootstrap (``user_info`` + ``item_list`` + ``card_list`` etc.)."""
    SEASON_PACK_INFO = auto()
    """Season-pack info sync (``season_pack_info`` + ``last_checked_time``)."""
    RESERVED_RACES_VIEW = auto()
    """Response listing scheduled-races state (``reserved_race_array`` only)."""
    NAV_ACK = auto()
    """Empty-ish server ack (only ``response_code`` / ``data_headers``)."""

    # -- request (client -> server) ----------------------------------------
    REQUEST_START_CHARA = auto()
    REQUEST_COMMAND = auto()
    """Training / rest / outing / infirmary (``command_type`` + ``command_id``)."""
    REQUEST_EVENT_CHOICE = auto()
    REQUEST_SKILL_PURCHASE = auto()
    REQUEST_BUY_ITEM = auto()
    REQUEST_USE_ITEM = auto()
    REQUEST_CONTINUE = auto()
    REQUEST_AOHARU_TEAM_RACE = auto()
    REQUEST_GRAND_LIVE_LESSON = auto()

    # -- request, non-career / navigation ---------------------------------
    REQUEST_BOOT_AUTH = auto()
    """Login / auth handshake (``attestation_type`` or ``dmm_onetime_token``)."""
    REQUEST_RACE_SCHEDULE_EDIT = auto()
    """Add/cancel races in schedule (``add_race_array`` / ``cancel_race_array``)."""
    REQUEST_RACE_ENTRY = auto()
    """Entering a race view (``program_id`` + ``current_turn`` without command_type)."""
    REQUEST_GRAND_LIVE_CONCERT = auto()
    """Start a Grand Live concert (``music_id`` + ``member_info_array``)."""
    REQUEST_NAV_POLL = auto()
    """Bare navigation poll (no payload beyond device boilerplate + current_turn)."""
    REQUEST_GENERIC = auto()


@dataclass
class GamePacket:
    """Typed wrapper around one decrypted msgpack dict.

    After parsing, exactly one of ``chara_info`` / ``race`` / ``event`` /
    ``skill_purchase`` / ``event_choice`` / ``command_request`` fields will
    be populated depending on ``kind``.

    All unmapped keys are preserved in ``raw`` so Phase 2 live-packet
    capture can audit fidelity.
    """

    kind: PacketKind = PacketKind.UNKNOWN
    direction: PacketDirection = PacketDirection.RESPONSE
    raw: dict = field(default_factory=dict)

    # populated lazily by parse_packet / parser.py; types are intentionally
    # Optional + forward-referenced so that this module stays importable on
    # its own.
    chara_info: Optional[Any] = None  # CharaInfo
    home_info: Optional[Any] = None  # HomeInfo
    venus_data_set: Optional[Any] = None  # VenusDataSet
    live_data_set: Optional[Any] = None  # LiveDataSet
    arc_data_set: Optional[Any] = None  # ArcDataSet
    sport_data_set: Optional[Any] = None  # SportDataSet
    cook_data_set: Optional[Any] = None  # CookDataSet
    mecha_data_set: Optional[Any] = None  # MechaDataSet
    team_data_set: Optional[Any] = None  # TeamDataSet
    free_data_set: Optional[Any] = None  # FreeDataSet (Trackblazer)

    race_start_info: Optional[Any] = None  # RaceStartInfo
    race_reward_info: Optional[Any] = None  # RaceRewardInfo
    race_scenario_bytes: Optional[bytes] = None  # raw blob; decode with race.RaceScenarioDecoded
    race_condition_array: list = field(default_factory=list)  # RaceCondition[]
    """Upcoming/active race states (weather + ground). Server-authoritative
    version of the bot's data/race_calendar.json lookahead."""

    unchecked_event_array: list = field(default_factory=list)  # UncheckedEvent[]

    command_result: Optional[Any] = None  # CommandResult
    """Emitted on the response to a REQUEST_COMMAND submission."""

    not_up_parameter_info: Optional[Any] = None  # ParameterBoundInfo
    """Stats/skills/conditions that can NOT go up this turn."""
    not_down_parameter_info: Optional[Any] = None  # ParameterBoundInfo
    """Stats/skills/conditions that can NOT go down this turn."""

    event_effected_factor_array: list = field(default_factory=list)
    """Post-event stat/skill deltas (``event_effected_factor_array``)."""

    choice_reward_array: list = field(default_factory=list)  # ChoiceReward[]
    """Server-emitted preview of each event choice's gains, populated on
    CHOICE_REWARD_PREVIEW responses."""

    skill_purchase: Optional[Any] = None  # SkillPurchaseRequest
    event_choice: Optional[Any] = None  # EventChoiceRequest

    # Free-form command request fields (command_type, command_id, select_id,
    # start_chara, continue_type, team_race_set_id, square_id, event_id,
    # choice_number, gain_skill_info_array, exchange_item_info_array,
    # use_item_info_array, current_turn, ...). We pass them through as-is.
    request_fields: dict = field(default_factory=dict)

    @property
    def is_response(self) -> bool:
        return self.direction == PacketDirection.RESPONSE

    @property
    def is_request(self) -> bool:
        return self.direction == PacketDirection.REQUEST


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def detect_packet_kind(raw: dict, direction=PacketDirection.RESPONSE) -> PacketKind:
    """Classify a raw decrypted msgpack dict into a ``PacketKind``.

    We intentionally keep this a pure key-name inspection so that it is
    cheap and easy to unit-test against synthetic dicts.

    Priority order matters: event + chara_info can both be present on the
    same packet (e.g. event triggered on a turn-response); the event check
    wins because the bot needs to resolve the event first.
    """
    if raw is None:
        return PacketKind.UNKNOWN

    # Some response envelopes nest everything under a top-level "data"
    # wrapper. UmaLauncher\u2019s carrotjuicer.handle_response pops that first.
    if "data" in raw and isinstance(raw["data"], dict):
        raw = raw["data"]

    if direction == PacketDirection.REQUEST:
        return _detect_request_kind(raw)

    # Career-ending packet wins over everything else.
    if raw.get("single_mode_factor_select_common"):
        return PacketKind.RUN_ENDED

    # Event-choice reward preview: server tells us what each option grants.
    if raw.get("choice_reward_array"):
        return PacketKind.CHOICE_REWARD_PREVIEW

    # Item-use response: a ``user_item`` root key (may be None when no
    # remaining items / ack-style).
    if "user_item" in raw and "chara_info" not in raw:
        return PacketKind.ITEM_USE_RESP

    # start_chara may be {} (empty dict / falsy) - use `in` not `get`
    if "start_chara" in raw:
        return PacketKind.START_CAREER

    # Events piggyback on normal turn responses; check them before TRAINING.
    if raw.get("unchecked_event_array"):
        return PacketKind.EVENT_TRIGGER

    # Race start: binary blob + start info.
    if raw.get("race_scenario") and raw.get("race_start_info"):
        return PacketKind.RACE_START

    # Race result.
    if raw.get("race_reward_info"):
        return PacketKind.RACE_RESULT

    # L\u2019Arc SS-match response.
    if raw.get("selection_result_info"):
        return PacketKind.LARC_SS_MATCH

    # Grand Live concert (live_theater_save_info_array + music_id etc.)
    if raw.get("live_theater_save_info_array") or raw.get("music_id"):
        return PacketKind.CONCERT

    # Nested race inside venus (Grand Masters goddess race).
    venus = raw.get("venus_data_set") or {}
    if isinstance(venus, dict):
        if venus.get("race_scenario"):
            return PacketKind.RACE_START
        if venus.get("race_reward_info"):
            return PacketKind.RACE_RESULT

    # Aoharu team race result.
    team = raw.get("team_data_set") or {}
    if isinstance(team, dict) and team.get("race_result_array"):
        return PacketKind.AOHARU_TEAM_RACE

    # Standard training home.
    if raw.get("chara_info"):
        # Did a scenario sidecar come along?
        # NOTE: ``free_data_set`` (Trackblazer) intentionally NOT in this list -
        # those packets carry the standard chara_info+home_info shape and the
        # bot consumes them as TRAINING_HOME; the typed FreeDataSet sidecar
        # is populated regardless.
        scenario_keys = (
            "venus_data_set", "live_data_set", "arc_data_set",
            "sport_data_set", "cook_data_set", "mecha_data_set",
        )
        if any(k in raw and raw[k] for k in scenario_keys):
            return PacketKind.TRAINING_SCENARIO_HOME
        return PacketKind.TRAINING_HOME

    # --- non-career response shapes (menu/bootstrap/nav) ----------------
    if "attest" in raw and "nonce" in raw:
        return PacketKind.BOOT_AUTH_RESP
    if raw.get("user_info") is not None and raw.get("item_list") is not None:
        return PacketKind.HOME_TOP_LOAD
    if raw.get("season_pack_info") is not None and "last_checked_time" in raw:
        return PacketKind.SEASON_PACK_INFO
    if "reserved_race_array" in raw and len(raw.keys()) <= 3:
        return PacketKind.RESERVED_RACES_VIEW
    if set(raw.keys()) <= {"data_headers", "response_code", "data"}:
        return PacketKind.NAV_ACK

    return PacketKind.UNKNOWN


_NAV_BOILERPLATE_KEYS = frozenset({
    "viewer_id", "device", "device_id", "device_name", "graphics_device_name",
    "ip_address", "platform_os_version", "carrier", "keychain", "locale",
    "button_info", "dmm_viewer_id", "dmm_onetime_token",
})


def _detect_request_kind(raw: dict) -> PacketKind:
    # use ``in`` - start_chara may be {} which is falsy
    if "start_chara" in raw:
        return PacketKind.REQUEST_START_CHARA
    if raw.get("continue_type"):
        return PacketKind.REQUEST_CONTINUE
    if raw.get("event_id"):
        return PacketKind.REQUEST_EVENT_CHOICE
    if raw.get("gain_skill_info_array"):
        return PacketKind.REQUEST_SKILL_PURCHASE
    if raw.get("exchange_item_info_array"):
        return PacketKind.REQUEST_BUY_ITEM
    if raw.get("use_item_info_array"):
        return PacketKind.REQUEST_USE_ITEM
    if raw.get("team_race_set_id"):
        return PacketKind.REQUEST_AOHARU_TEAM_RACE
    if raw.get("square_id"):
        return PacketKind.REQUEST_GRAND_LIVE_LESSON
    if raw.get("command_type"):
        return PacketKind.REQUEST_COMMAND
    # Grand Live concert submission.
    if raw.get("music_id") and raw.get("member_info_array") is not None:
        return PacketKind.REQUEST_GRAND_LIVE_CONCERT
    # Schedule edit: adds/removes races on the trainee's schedule.
    if "add_race_array" in raw or "cancel_race_array" in raw:
        return PacketKind.REQUEST_RACE_SCHEDULE_EDIT
    # Auth/boot handshake (attestation or DMM onetime token without command).
    if "attestation_type" in raw or "adid" in raw or "device_token" in raw:
        return PacketKind.REQUEST_BOOT_AUTH
    # Race entry view: program_id + current_turn but no command_type.
    if "program_id" in raw and "current_turn" in raw:
        return PacketKind.REQUEST_RACE_ENTRY
    # Bare navigation poll: only boilerplate + optional current_turn / is_short.
    extra_keys = set(raw.keys()) - _NAV_BOILERPLATE_KEYS - {"current_turn", "is_short"}
    if not extra_keys:
        return PacketKind.REQUEST_NAV_POLL
    return PacketKind.REQUEST_GENERIC


__all__ = [
    "GamePacket",
    "PacketKind",
    "PacketDirection",
    "detect_packet_kind",
]
