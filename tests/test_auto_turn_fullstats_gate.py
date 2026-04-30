"""Tests for the packet vs OCR gating around Full Stats reading.

When ``_session_tailer.is_fresh()`` is True and ``UMA_PACKET_STATE`` is not
disabled, the packet overlay path populates ``_active_conditions`` and
``_positive_statuses`` directly and the OCR ``read_fullstats()`` call is
skipped. When the capture is stale or env disables it, the legacy OCR path
runs.
"""
from __future__ import annotations

import os
from pathlib import Path

import msgpack
import pytest
from PIL import Image

import scripts.auto_turn as auto_turn

FIXTURE = Path(__file__).parent / "fixtures" / "home_response_turn21.msgpack"


@pytest.fixture
def fresh_response() -> dict:
    return msgpack.unpackb(FIXTURE.read_bytes(), raw=False, strict_map_key=False)


@pytest.fixture
def blank_image() -> Image.Image:
    return Image.new("RGB", (1080, 1920), (255, 255, 255))


# ---------------------------------------------------------------------------
# _should_call_fullstats() — pure unit tests
# ---------------------------------------------------------------------------

def test_should_call_fullstats_true_when_stale(monkeypatch):
    monkeypatch.delenv("UMA_PACKET_STATE", raising=False)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: False)
    assert auto_turn._should_call_fullstats() is True


def test_should_call_fullstats_true_when_env_disabled(monkeypatch):
    monkeypatch.setenv("UMA_PACKET_STATE", "0")
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    assert auto_turn._should_call_fullstats() is True


def test_should_call_fullstats_false_when_fresh(monkeypatch):
    monkeypatch.delenv("UMA_PACKET_STATE", raising=False)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    assert auto_turn._should_call_fullstats() is False


# ---------------------------------------------------------------------------
# packet overlay populates conditions globals
# ---------------------------------------------------------------------------

def _mutate(response: dict, ids: list[int]) -> dict:
    data = response.get("data") if "data" in response else response
    chara = dict(data.get("chara_info") or {})
    chara["chara_effect_id_array"] = list(ids)
    new_data = dict(data)
    new_data["chara_info"] = chara
    if "data" in response:
        return {**response, "data": new_data}
    return new_data


def test_packet_overlay_populates_conditions(fresh_response, blank_image, monkeypatch):
    """Fresh packet with chara_effect_id_array=[1, 8] → globals populated."""
    mutated = _mutate(fresh_response, [1, 8])
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: mutated,
    )
    # Reset module-level state so the test isn't polluted by earlier runs.
    monkeypatch.setattr(auto_turn, "_active_conditions", [])
    monkeypatch.setattr(auto_turn, "_positive_statuses", [])

    auto_turn.build_game_state(blank_image, "career_home")

    assert auto_turn._active_conditions == ["night owl"]
    assert auto_turn._positive_statuses == ["charming"]


def test_packet_overlay_clears_conditions_when_array_empty(
    fresh_response, blank_image, monkeypatch
):
    """Fresh packet with empty chara_effect_id_array → globals reset to []."""
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: fresh_response,
    )
    # Pre-populate stale data — overlay should overwrite to [] since the
    # fixture's chara_effect_id_array is empty.
    monkeypatch.setattr(auto_turn, "_active_conditions", ["migraine"])
    monkeypatch.setattr(auto_turn, "_positive_statuses", ["charming"])

    auto_turn.build_game_state(blank_image, "career_home")

    assert auto_turn._active_conditions == []
    assert auto_turn._positive_statuses == []


# ---------------------------------------------------------------------------
# Step 6: Pure Passion → Sirius bond unlock via packet path
# ---------------------------------------------------------------------------

def test_pure_passion_unlocks_sirius_bond(
    fresh_response, blank_image, monkeypatch, tmp_path
):
    mutated = _mutate(fresh_response, [100])
    sentinel_flag = tmp_path / "sirius_bond_unlocked.flag"
    monkeypatch.setattr(auto_turn, "_SIRIUS_BOND_FILE", sentinel_flag)
    monkeypatch.setattr(auto_turn, "_sirius_bond_unlocked", False)
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: mutated,
    )
    # Stub scorer hooks so we don't touch real scorer state.
    promote_calls: list[bool] = []
    monkeypatch.setattr(
        auto_turn._scorer, "set_bond_override", lambda *a, **k: None
    )
    monkeypatch.setattr(
        auto_turn._scorer, "mark_bond_complete", lambda *a, **k: None
    )
    monkeypatch.setattr(
        auto_turn,
        "_promote_post_sirius_priorities",
        lambda: promote_calls.append(True),
    )

    auto_turn.build_game_state(blank_image, "career_home")

    assert auto_turn._sirius_bond_unlocked is True
    assert sentinel_flag.exists()
    assert promote_calls == [True]


def test_pure_passion_no_double_unlock(
    fresh_response, blank_image, monkeypatch, tmp_path
):
    """Already-unlocked Sirius bond shouldn't re-fire the side effects."""
    mutated = _mutate(fresh_response, [100])
    sentinel_flag = tmp_path / "sirius_bond_unlocked.flag"
    monkeypatch.setattr(auto_turn, "_SIRIUS_BOND_FILE", sentinel_flag)
    monkeypatch.setattr(auto_turn, "_sirius_bond_unlocked", True)
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: mutated,
    )
    promote_calls: list[bool] = []
    monkeypatch.setattr(
        auto_turn,
        "_promote_post_sirius_priorities",
        lambda: promote_calls.append(True),
    )

    auto_turn.build_game_state(blank_image, "career_home")

    assert promote_calls == []
