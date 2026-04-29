"""Tests for the typed schema over real captured msgpack packets.

Runs against every .bin in ``data/packet_captures/`` so that the coverage
holds as we add more sessions. Each test asserts a specific schema
invariant (classification, typed-field presence, container cleanliness).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import msgpack
import pytest

from uma_trainer.perception.carrotjuicer.schema import (
    PacketDirection,
    PacketKind,
    parse_packet,
)

CAPTURES_ROOT = Path(__file__).resolve().parents[1] / "data" / "packet_captures"


def _iter_response_packets() -> Iterator[tuple[Path, dict]]:
    for session in sorted(p for p in CAPTURES_ROOT.iterdir() if p.is_dir()):
        for bin_path in sorted(session.glob("*_decompress_*_out.bin")):
            try:
                raw = msgpack.unpackb(bin_path.read_bytes(), raw=False, strict_map_key=False)
            except Exception:
                continue
            if isinstance(raw, dict):
                yield bin_path, raw


def _iter_request_packets() -> Iterator[tuple[Path, dict]]:
    for session in sorted(p for p in CAPTURES_ROOT.iterdir() if p.is_dir()):
        for bin_path in sorted(session.glob("*_compress_*_in.bin")):
            try:
                raw = msgpack.unpackb(bin_path.read_bytes(), raw=False, strict_map_key=False)
            except Exception:
                continue
            if isinstance(raw, dict):
                yield bin_path, raw


@pytest.fixture(scope="module")
def training_home_packet() -> dict:
    for _, raw in _iter_response_packets():
        pkt = parse_packet(raw, direction=PacketDirection.RESPONSE)
        if pkt.kind == PacketKind.TRAINING_HOME:
            return raw
    pytest.skip("no TRAINING_HOME packet in captures")


@pytest.fixture(scope="module")
def race_start_packet() -> dict:
    for _, raw in _iter_response_packets():
        pkt = parse_packet(raw, direction=PacketDirection.RESPONSE)
        if pkt.kind == PacketKind.RACE_START:
            return raw
    pytest.skip("no RACE_START packet in captures")


@pytest.fixture(scope="module")
def race_result_packet() -> dict:
    for _, raw in _iter_response_packets():
        pkt = parse_packet(raw, direction=PacketDirection.RESPONSE)
        if pkt.kind == PacketKind.RACE_RESULT:
            return raw
    pytest.skip("no RACE_RESULT packet in captures")


def test_no_unknown_responses_in_captures():
    """After schema hardening, no captured response should be UNKNOWN."""
    offenders = []
    for path, raw in _iter_response_packets():
        pkt = parse_packet(raw, direction=PacketDirection.RESPONSE)
        if pkt.kind == PacketKind.UNKNOWN:
            offenders.append(path.name)
    assert offenders == [], f"UNKNOWN responses: {offenders}"


def test_no_generic_requests_in_captures():
    """Every captured request should route to a specific PacketKind."""
    offenders = []
    for path, raw in _iter_request_packets():
        pkt = parse_packet(raw, direction=PacketDirection.REQUEST)
        if pkt.kind == PacketKind.REQUEST_GENERIC:
            offenders.append(path.name)
    assert offenders == [], f"REQUEST_GENERIC requests: {offenders}"


def test_chara_info_new_fields_populated(training_home_packet):
    pkt = parse_packet(training_home_packet, direction=PacketDirection.RESPONSE)
    ci = pkt.chara_info
    assert ci is not None
    # Default caps observed in captures are 1200 at career start.
    assert ci.default_max_speed > 0
    assert ci.default_max_stamina > 0
    # training_level_info_array always carries at least the 5 training commands.
    assert len(ci.training_level_info_array) >= 5
    # Every entry has a command_id + level.
    for t in ci.training_level_info_array:
        assert t.command_id > 0
        assert t.level > 0
    # Previously-extras fields now typed.
    assert isinstance(ci.disable_skill_id_array, list)
    assert isinstance(ci.guest_outing_info_array, list)


def test_chara_info_extras_bag_empty(training_home_packet):
    """After adding typed fields, extras should be empty for chara_info."""
    pkt = parse_packet(training_home_packet, direction=PacketDirection.RESPONSE)
    ci = pkt.chara_info
    assert ci is not None
    assert ci.extras == {}, f"unmapped chara_info keys: {list(ci.extras.keys())}"


def test_race_start_info_has_weather_and_ground(race_start_packet):
    pkt = parse_packet(race_start_packet, direction=PacketDirection.RESPONSE)
    rsi = pkt.race_start_info
    assert rsi is not None
    assert rsi.program_id > 0
    assert rsi.weather in {1, 2, 3, 4}
    assert rsi.ground_condition in {1, 2, 3, 4}
    assert rsi.season in {1, 2, 3, 4}
    assert rsi.extras == {}


def test_race_reward_info_has_typed_rewards(race_result_packet):
    pkt = parse_packet(race_result_packet, direction=PacketDirection.RESPONSE)
    rri = pkt.race_reward_info
    assert rri is not None
    assert rri.result_rank >= 1
    assert rri.result_time > 0
    assert rri.gained_fans >= 0
    # race_reward is a list of RaceRewardItem with typed fields.
    for item in rri.race_reward:
        assert item.item_type > 0
        assert item.item_num > 0
    assert rri.extras == {}


def test_race_condition_array_typed(training_home_packet):
    """Training-home responses include upcoming-race state."""
    pkt = parse_packet(training_home_packet, direction=PacketDirection.RESPONSE)
    # Not every turn has upcoming-race state; tolerate empty but when present
    # it must be typed.
    for rc in pkt.race_condition_array:
        assert rc.program_id > 0
        assert rc.weather in {1, 2, 3, 4}
        assert rc.ground_condition in {1, 2, 3, 4}


def test_not_up_parameter_info_shape(training_home_packet):
    pkt = parse_packet(training_home_packet, direction=PacketDirection.RESPONSE)
    nup = pkt.not_up_parameter_info
    # Not every turn includes this, but when it does every sub-array must be
    # a list (even when empty).
    if nup is not None:
        assert isinstance(nup.skill_id_array, list)
        assert isinstance(nup.chara_effect_id_array, list)
        assert isinstance(nup.evaluation_chara_id_array, list)


def test_boot_and_nav_classified():
    """The bootstrap / auth / poll packets should get specific kinds, not UNKNOWN."""
    seen = set()
    for _, raw in _iter_response_packets():
        pkt = parse_packet(raw, direction=PacketDirection.RESPONSE)
        seen.add(pkt.kind)
    expected_bootstrap = {
        PacketKind.BOOT_AUTH_RESP,
        PacketKind.HOME_TOP_LOAD,
        PacketKind.SEASON_PACK_INFO,
        PacketKind.RESERVED_RACES_VIEW,
        PacketKind.NAV_ACK,
    }
    assert expected_bootstrap & seen, (
        "expected at least one bootstrap/nav kind in captures; "
        f"got {sorted(k.name for k in seen)}"
    )


def test_request_nav_poll_and_boot_auth_routed():
    seen = set()
    for _, raw in _iter_request_packets():
        pkt = parse_packet(raw, direction=PacketDirection.REQUEST)
        seen.add(pkt.kind)
    assert PacketKind.REQUEST_NAV_POLL in seen
    assert PacketKind.REQUEST_BOOT_AUTH in seen
    assert PacketKind.REQUEST_RACE_ENTRY in seen
    assert PacketKind.REQUEST_RACE_SCHEDULE_EDIT in seen
    assert PacketKind.REQUEST_GRAND_LIVE_CONCERT in seen


@pytest.fixture(scope="module")
def trackblazer_free_data_set():
    """First TRAINING_HOME-shape response carrying a free_data_set sidecar."""
    for _, raw in _iter_response_packets():
        pkt = parse_packet(raw, direction=PacketDirection.RESPONSE)
        if pkt.free_data_set is not None and pkt.free_data_set.shop_id > 0:
            return pkt
    pytest.skip("no Trackblazer free_data_set in captures")


def test_free_data_set_typed(trackblazer_free_data_set):
    fds = trackblazer_free_data_set.free_data_set
    assert fds is not None
    # coin_num is the live wallet balance
    assert fds.coin_num >= 0
    # shop_id identifies the rotation; at least 1 in observed captures
    assert fds.shop_id >= 1
    # pick_up_item_info_array is the per-rotation shop offer list
    for item in fds.pick_up_item_info_array:
        assert item.shop_item_id > 0
        assert item.item_id > 0
        assert item.coin_num >= 0
        assert item.limit_buy_count >= 0
        assert 0 <= item.item_buy_num <= item.limit_buy_count
        assert item.stock_remaining == item.limit_buy_count - item.item_buy_num
    # user_item_info_array is the per-career inventory
    for owned in fds.user_item_info_array:
        assert owned.item_id > 0
        assert owned.num >= 0
    # Container clean - all observed keys typed
    assert fds.extras == {}, f"unmapped free_data_set keys: {list(fds.extras.keys())}"


def test_buy_item_request_routes():
    """At least one captured request should be REQUEST_BUY_ITEM."""
    seen = set()
    for _, raw in _iter_request_packets():
        pkt = parse_packet(raw, direction=PacketDirection.REQUEST)
        seen.add(pkt.kind)
    assert PacketKind.REQUEST_BUY_ITEM in seen


def test_choice_reward_preview_typed():
    """choice_reward_array responses get classified + typed."""
    seen = False
    for _, raw in _iter_response_packets():
        pkt = parse_packet(raw, direction=PacketDirection.RESPONSE)
        if pkt.kind != PacketKind.CHOICE_REWARD_PREVIEW:
            continue
        seen = True
        assert pkt.choice_reward_array, "CHOICE_REWARD_PREVIEW with empty array"
        for cr in pkt.choice_reward_array:
            assert cr.select_index >= 1
            for gp in cr.gain_param_array:
                assert gp.display_id > 0
            assert cr.extras == {}
    if not seen:
        pytest.skip("no CHOICE_REWARD_PREVIEW packet in captures")
