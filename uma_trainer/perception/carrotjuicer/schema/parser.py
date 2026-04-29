"""Main parser orchestrator.

Public entry points:

* ``parse_packet(raw, direction=...)`` - accepts a decrypted msgpack dict,
  returns a ``GamePacket`` with ``kind`` set and the relevant typed
  sub-objects populated.
* ``parse_response(raw)`` / ``parse_request(raw)`` - convenience wrappers.
* ``iter_packets(stream)`` - given an iterable of raw dicts (e.g. from
  ``msgpack.Unpacker``), yields typed ``GamePacket`` objects.

Design notes:

- The parser is defensive. If a known key is absent it skips population
  rather than raising. Missing keys usually mean the scenario does not emit
  that sub-block, not that the packet is malformed.
- Unknown keys always land in ``raw`` so WS-5 can log them.
- The parser is purely functional - no I/O, no state.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Optional

from .career import CharaInfo
from .events import ChoiceReward, EventChoiceRequest, UncheckedEvent
from .enums import PacketDirection
from .packets import GamePacket, PacketKind, detect_packet_kind
from .race import RaceCondition, RaceRewardInfo, RaceStartInfo
from .scenario_data import (
    ArcDataSet,
    CookDataSet,
    FreeDataSet,
    LiveDataSet,
    MechaDataSet,
    SportDataSet,
    TeamDataSet,
    VenusDataSet,
)
from .skills import SkillPurchaseRequest
from .training_state import CommandResult, HomeInfo, ParameterBoundInfo


def _unwrap_data(raw):
    """Some responses wrap everything in ``{"data": {...}}``."""
    if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], dict):
        return raw["data"]
    return raw


def parse_packet(raw, direction=PacketDirection.RESPONSE) -> GamePacket:
    """Turn a raw msgpack dict into a typed ``GamePacket``.

    ``direction`` distinguishes request (client->server) packets, which have
    a different routing table. When the CarrotJuicer wrapper stamps
    ``_direction`` on the dict, we honour it; otherwise the caller must
    supply the direction.
    """
    if raw is None:
        return GamePacket(kind=PacketKind.UNKNOWN, direction=direction, raw={})

    # Honour the ``_direction`` key if UmaLauncher-style wrapper stamped it.
    inferred_dir = raw.get("_direction") if isinstance(raw, dict) else None
    if inferred_dir is not None:
        direction = PacketDirection(int(inferred_dir))

    raw_inner = _unwrap_data(raw)

    kind = detect_packet_kind(raw_inner, direction)
    pkt = GamePacket(kind=kind, direction=direction, raw=raw_inner)

    if direction == PacketDirection.REQUEST:
        _fill_request(pkt, raw_inner)
    else:
        _fill_response(pkt, raw_inner)

    return pkt


def parse_request(raw) -> GamePacket:
    return parse_packet(raw, direction=PacketDirection.REQUEST)


def parse_response(raw) -> GamePacket:
    return parse_packet(raw, direction=PacketDirection.RESPONSE)


def iter_packets(stream: Iterable, direction=PacketDirection.RESPONSE) -> Iterator[GamePacket]:
    for raw in stream:
        yield parse_packet(raw, direction=direction)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _fill_response(pkt: GamePacket, raw: dict) -> None:
    """Populate typed sub-objects on a response packet.

    We populate every known sub-block regardless of kind - downstream code
    uses ``kind`` to decide which fields are relevant, and having typed
    access to ``chara_info`` even on a race-start response is often useful
    (the server ships chara_info alongside the race blob).
    """
    if not isinstance(raw, dict):
        return

    ci = raw.get("chara_info")
    if isinstance(ci, dict):
        pkt.chara_info = CharaInfo.from_raw(ci)

    hi = raw.get("home_info")
    if isinstance(hi, dict):
        pkt.home_info = HomeInfo.from_raw(hi)

    if isinstance(raw.get("venus_data_set"), dict):
        pkt.venus_data_set = VenusDataSet.from_raw(raw["venus_data_set"])
    if isinstance(raw.get("live_data_set"), dict):
        pkt.live_data_set = LiveDataSet.from_raw(raw["live_data_set"])
    if isinstance(raw.get("arc_data_set"), dict):
        pkt.arc_data_set = ArcDataSet.from_raw(raw["arc_data_set"])
    if isinstance(raw.get("sport_data_set"), dict):
        pkt.sport_data_set = SportDataSet.from_raw(raw["sport_data_set"])
    if isinstance(raw.get("cook_data_set"), dict):
        pkt.cook_data_set = CookDataSet.from_raw(raw["cook_data_set"])
    if isinstance(raw.get("mecha_data_set"), dict):
        pkt.mecha_data_set = MechaDataSet.from_raw(raw["mecha_data_set"])
    if isinstance(raw.get("team_data_set"), dict):
        pkt.team_data_set = TeamDataSet.from_raw(raw["team_data_set"])
    if isinstance(raw.get("free_data_set"), dict):
        pkt.free_data_set = FreeDataSet.from_raw(raw["free_data_set"])

    # Race: may appear at top-level OR nested inside venus_data_set.
    rsi = raw.get("race_start_info")
    if rsi is None and pkt.venus_data_set is not None:
        rsi = pkt.venus_data_set.race_start_info
    if isinstance(rsi, dict):
        pkt.race_start_info = RaceStartInfo.from_raw(rsi)

    rri = raw.get("race_reward_info")
    if rri is None and pkt.venus_data_set is not None:
        rri = pkt.venus_data_set.race_reward_info
    if isinstance(rri, dict):
        pkt.race_reward_info = RaceRewardInfo.from_raw(rri)

    rs = raw.get("race_scenario")
    if rs is None and pkt.venus_data_set is not None:
        rs = pkt.venus_data_set.race_scenario
    if isinstance(rs, (bytes, bytearray)):
        pkt.race_scenario_bytes = bytes(rs)
    elif isinstance(rs, str):
        # Some captures base64 the blob. We accept it but do not decode;
        # consumers decide whether to pass through a base64 decoder plus
        # the Hakuraku race_data_parser.
        pkt.race_scenario_bytes = rs.encode("latin-1", errors="ignore")

    uea = raw.get("unchecked_event_array")
    if isinstance(uea, list):
        pkt.unchecked_event_array = [
            UncheckedEvent.from_raw(x) for x in uea if isinstance(x, dict)
        ]

    rca = raw.get("race_condition_array")
    if isinstance(rca, list):
        pkt.race_condition_array = [
            RaceCondition.from_raw(x) for x in rca if isinstance(x, dict)
        ]

    cr = raw.get("command_result")
    if isinstance(cr, dict):
        pkt.command_result = CommandResult.from_raw(cr)

    nup = raw.get("not_up_parameter_info")
    if isinstance(nup, dict):
        pkt.not_up_parameter_info = ParameterBoundInfo.from_raw(nup)
    ndn = raw.get("not_down_parameter_info")
    if isinstance(ndn, dict):
        pkt.not_down_parameter_info = ParameterBoundInfo.from_raw(ndn)

    eefa = raw.get("event_effected_factor_array")
    if isinstance(eefa, list):
        pkt.event_effected_factor_array = list(eefa)

    cra = raw.get("choice_reward_array")
    if isinstance(cra, list):
        pkt.choice_reward_array = [
            ChoiceReward.from_raw(x) for x in cra if isinstance(x, dict)
        ]


def _fill_request(pkt: GamePacket, raw: dict) -> None:
    if not isinstance(raw, dict):
        return

    # Capture the whole thing as opaque request fields; the kind field drives
    # downstream interpretation.
    pkt.request_fields = dict(raw)

    if raw.get("gain_skill_info_array"):
        pkt.skill_purchase = SkillPurchaseRequest.from_raw(raw)

    if raw.get("event_id"):
        pkt.event_choice = EventChoiceRequest.from_raw(raw)


__all__ = [
    "iter_packets",
    "parse_packet",
    "parse_request",
    "parse_response",
]
