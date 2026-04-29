"""Audit the carrotjuicer schema against real captured sessions.

Decodes every compress_in (request) / decompress_out (response) buffer in a
capture session, classifies each packet, and reports where our typed schema
falls short:

  - `PacketKind` distribution (including UNKNOWN — schemas we still need)
  - Unmapped keys landing in every container's `.extras` bag
  - Top-level response-root keys not claimed by any typed container
  - Top-level request-root keys not exercised by `_fill_request`

Designed to be re-run after every new capture to keep the schema honest.

Usage:
    .venv/bin/python scripts/validate_packet_schema.py               # latest session
    .venv/bin/python scripts/validate_packet_schema.py <session_dir> # specific
    .venv/bin/python scripts/validate_packet_schema.py --all         # every session
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import sys
from pathlib import Path

import msgpack

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.perception.carrotjuicer.schema import (
    GamePacket,
    PacketDirection,
    PacketKind,
    parse_packet,
)

# Keys we consider "known" on a response root — anything else surfaces as gap.
KNOWN_RESPONSE_ROOT_KEYS = {
    "chara_info", "home_info",
    "unchecked_event_array", "event_effected_factor_array",
    "race_start_info", "race_reward_info", "race_scenario", "race_horse_data_array",
    "race_condition_array",
    "command_result",
    "not_up_parameter_info", "not_down_parameter_info",
    "start_chara", "continue_info", "single_mode_factor_select_common",
    # scenario sidecars
    "venus_data_set", "live_data_set", "arc_data_set",
    "sport_data_set", "cook_data_set", "mecha_data_set",
    "team_data_set", "free_data_set",
    # L'Arc / Grand Live extras
    "selection_result_info", "live_theater_save_info_array", "music_id",
    # post-race reward extras (bot only needs gained_fans + result_rank from
    # race_reward_info itself, these are sibling metadata)
    "race_history", "win_saddle_id_array",
    "add_trophy_info", "trophy_reward_info",
    "race_add_reward_info", "reward_summary_info",
    "add_music", "story_event_mission_list",
    # schedule edit response
    "reserved_race_array",
    # event-choice reward preview + item-use ack
    "choice_reward_array",
    "user_item",
    # first-turn-after-start_chara extras (carry missions + succession info)
    "effected_factor_array", "prev_chara_grade",
    "mission_list", "story_event_chara_bonus_list", "start_dress_info",
    # meta
    "data_headers", "data", "_direction",
}

# Keys we consider "known" on a request root (client->server).
KNOWN_REQUEST_ROOT_KEYS = {
    "command_type", "command_id", "select_id", "current_turn",
    "start_chara", "continue_type",
    "event_id", "choice_number",
    "gain_skill_info_array", "exchange_item_info_array", "use_item_info_array",
    "team_race_set_id", "square_id",
    # race schedule edit
    "add_race_array", "cancel_race_array", "deck_num", "deck_name",
    # race entry / grand live
    "program_id", "is_short", "music_id", "member_info_array",
    "live_theater_setting_info", "live_theater_vocal_chara_id_array",
    "chara_id", "command_group_id", "current_vital",
    # boot/auth
    "attestation_type", "device_token", "adid",
    # nav boilerplate (every request carries these)
    "viewer_id", "device", "device_id", "device_name", "graphics_device_name",
    "ip_address", "platform_os_version", "carrier", "keychain", "locale",
    "button_info", "dmm_viewer_id", "dmm_onetime_token",
    # request wrapper
    "_direction", "data", "data_headers",
}


def _container_extras(obj) -> collections.Counter:
    """Pull ``.extras`` off a dataclass instance if it carries one."""
    c: collections.Counter = collections.Counter()
    if obj is None:
        return c
    if not dataclasses.is_dataclass(obj):
        return c
    extras = getattr(obj, "extras", None)
    if isinstance(extras, dict):
        c.update(extras.keys())
    return c


def _inner(raw: dict) -> dict:
    if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], dict):
        return raw["data"]
    return raw


def audit_session(session: Path, report: dict) -> None:
    """Accumulate stats for one session directory into ``report``."""
    resp_kinds = report["resp_kinds"]
    req_kinds = report["req_kinds"]
    root_resp_gaps = report["root_resp_gaps"]
    root_req_gaps = report["root_req_gaps"]
    extras_by_container = report["extras_by_container"]
    parsed = report["parsed"]

    for bin_path in sorted(session.glob("*.bin")):
        name = bin_path.name
        if "_decompress_" in name and name.endswith("_out.bin"):
            direction = PacketDirection.RESPONSE
        elif "_compress_" in name and name.endswith("_in.bin"):
            direction = PacketDirection.REQUEST
        else:
            continue

        try:
            raw = msgpack.unpackb(bin_path.read_bytes(), raw=False, strict_map_key=False)
        except Exception as e:
            report["decode_errors"].append(f"{bin_path.name}: {e}")
            continue
        if not isinstance(raw, dict):
            continue

        parsed["total"] += 1
        if direction == PacketDirection.RESPONSE:
            parsed["resp"] += 1
        else:
            parsed["req"] += 1

        pkt: GamePacket = parse_packet(raw, direction=direction)

        if direction == PacketDirection.RESPONSE:
            resp_kinds[pkt.kind] += 1
        else:
            req_kinds[pkt.kind] += 1

        # Gap detection skips non-career shapes (bootstrap/auth/nav) — we
        # classify them via PacketKind but don't need typed field coverage.
        nonbot_kinds = {
            PacketKind.BOOT_AUTH_RESP,
            PacketKind.HOME_TOP_LOAD,
            PacketKind.SEASON_PACK_INFO,
            PacketKind.RESERVED_RACES_VIEW,
            PacketKind.NAV_ACK,
            PacketKind.REQUEST_BOOT_AUTH,
            PacketKind.REQUEST_NAV_POLL,
        }
        if pkt.kind in nonbot_kinds:
            continue

        inner = _inner(raw)
        if isinstance(inner, dict):
            known = KNOWN_RESPONSE_ROOT_KEYS if direction == PacketDirection.RESPONSE else KNOWN_REQUEST_ROOT_KEYS
            gap_dict = root_resp_gaps if direction == PacketDirection.RESPONSE else root_req_gaps
            for k in inner.keys():
                if k not in known:
                    gap_dict[k] += 1

        # Container extras — typed objects.
        extras_by_container["chara_info"] += _container_extras(pkt.chara_info)
        extras_by_container["home_info"] += _container_extras(pkt.home_info)
        extras_by_container["race_start_info"] += _container_extras(pkt.race_start_info)
        extras_by_container["race_reward_info"] += _container_extras(pkt.race_reward_info)
        for sc in getattr(pkt.chara_info, "support_card_array", []) or []:
            extras_by_container["chara_info.support_card_array[]"] += _container_extras(sc)
        for ev in getattr(pkt.chara_info, "evaluation_info_array", []) or []:
            extras_by_container["chara_info.evaluation_info_array[]"] += _container_extras(ev)
        for sk in getattr(pkt.chara_info, "skill_array", []) or []:
            extras_by_container["chara_info.skill_array[]"] += _container_extras(sk)
        for st in getattr(pkt.chara_info, "skill_tips_array", []) or []:
            extras_by_container["chara_info.skill_tips_array[]"] += _container_extras(st)
        for rr in getattr(pkt.chara_info, "reserved_race_array", []) or []:
            extras_by_container["chara_info.reserved_race_array[]"] += _container_extras(rr)
        for ci in getattr(pkt.home_info, "command_info_array", []) or []:
            extras_by_container["home_info.command_info_array[]"] += _container_extras(ci)
        for ev in pkt.unchecked_event_array or []:
            extras_by_container["unchecked_event_array[]"] += _container_extras(ev)
        extras_by_container["free_data_set"] += _container_extras(pkt.free_data_set)
        for it in getattr(pkt.free_data_set, "pick_up_item_info_array", []) or []:
            extras_by_container["free_data_set.pick_up_item_info_array[]"] += _container_extras(it)
        for it in getattr(pkt.free_data_set, "user_item_info_array", []) or []:
            extras_by_container["free_data_set.user_item_info_array[]"] += _container_extras(it)
        for cr in pkt.choice_reward_array or []:
            extras_by_container["choice_reward_array[]"] += _container_extras(cr)
            for gp in getattr(cr, "gain_param_array", []) or []:
                extras_by_container["choice_reward_array[].gain_param_array[]"] += _container_extras(gp)


def print_report(report: dict) -> None:
    parsed = report["parsed"]
    print(f"\n[*] parsed total={parsed['total']} (resp={parsed['resp']}, req={parsed['req']})")
    if report["decode_errors"]:
        print(f"\n== decode errors ({len(report['decode_errors'])}) ==")
        for e in report["decode_errors"][:5]:
            print(f"  {e}")

    print("\n== response PacketKind distribution ==")
    for k, v in sorted(report["resp_kinds"].items(), key=lambda kv: -kv[1]):
        print(f"  {k.name:30s} x{v}")

    print("\n== request PacketKind distribution ==")
    for k, v in sorted(report["req_kinds"].items(), key=lambda kv: -kv[1]):
        print(f"  {k.name:30s} x{v}")

    print("\n== response root keys NOT claimed by schema ==")
    gaps = report["root_resp_gaps"]
    if not gaps:
        print("  (none)")
    for k, v in gaps.most_common():
        print(f"  {k:40s} x{v}")

    print("\n== request root keys NOT claimed by schema ==")
    gaps = report["root_req_gaps"]
    if not gaps:
        print("  (none)")
    for k, v in gaps.most_common():
        print(f"  {k:40s} x{v}")

    print("\n== container .extras (unmapped dataclass fields) ==")
    for container, counter in report["extras_by_container"].items():
        if not counter:
            continue
        print(f"  -- {container} --")
        for k, v in counter.most_common():
            print(f"    {k:40s} x{v}")
    if not any(report["extras_by_container"].values()):
        print("  (all containers clean)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", nargs="?", type=Path, help="capture session dir (default: latest)")
    ap.add_argument("--all", action="store_true", help="audit every session under data/packet_captures/")
    args = ap.parse_args()

    captures_root = Path(__file__).resolve().parents[1] / "data" / "packet_captures"

    if args.all:
        sessions = sorted(p for p in captures_root.iterdir() if p.is_dir())
    elif args.session:
        sessions = [args.session]
    else:
        sessions = [sorted(p for p in captures_root.iterdir() if p.is_dir())[-1]]

    report = {
        "parsed": {"total": 0, "resp": 0, "req": 0},
        "resp_kinds": collections.Counter(),
        "req_kinds": collections.Counter(),
        "root_resp_gaps": collections.Counter(),
        "root_req_gaps": collections.Counter(),
        "extras_by_container": collections.defaultdict(collections.Counter),
        "decode_errors": [],
    }

    for s in sessions:
        print(f"[*] session: {s}")
        audit_session(s, report)

    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
