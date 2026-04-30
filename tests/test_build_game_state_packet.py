"""Tests for the packet-overlay path in ``scripts.auto_turn.build_game_state``.

When the live capture is fresh, ``build_game_state`` should bypass OCR and
produce a ``GameState`` directly from the latest decoded response. When the
session is stale (probe down) or capture is disabled, the OCR path runs
unchanged.
"""
from __future__ import annotations

from pathlib import Path

import msgpack
import pytest
from PIL import Image

import scripts.auto_turn as auto_turn
from uma_trainer.types import ScreenState

FIXTURE = Path(__file__).parent / "fixtures" / "home_response_turn21.msgpack"


@pytest.fixture
def fresh_response() -> dict:
    return msgpack.unpackb(FIXTURE.read_bytes(), raw=False, strict_map_key=False)


@pytest.fixture
def blank_image() -> Image.Image:
    # 1080x1920 white image — standin for an emulator screenshot. The
    # overlay path doesn't run any OCR over it.
    return Image.new("RGB", (1080, 1920), (255, 255, 255))


def test_overlay_returns_packet_state_when_fresh(fresh_response, blank_image, monkeypatch):
    """Fresh packet → GameState built from the response, no OCR."""
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: fresh_response,
    )

    state = auto_turn.build_game_state(blank_image, "career_home")

    # Stats from the fixture (matches test_packet_state_adapter expectations)
    assert state.stats.speed == 223
    assert state.current_turn == 21
    assert state.skill_pts >= 0
    assert state.screen == ScreenState.TRAINING
    # Module globals were synced for downstream readers.
    assert auto_turn._current_turn == 21
    assert auto_turn._current_stats.speed == 223
    assert state.training_tiles, "adapter should populate training_tiles"


def test_overlay_skipped_for_non_career_screen(fresh_response, blank_image, monkeypatch):
    """Race-list / event / skill_shop screens still go through OCR."""
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: fresh_response,
    )

    state = auto_turn.build_game_state(blank_image, "skill_shop")
    # No TRAINING screen and no training tiles populated by the overlay
    # path (skill_shop isn't in _PACKET_OVERLAY_SCREENS).
    assert state.screen == ScreenState.SKILL_SHOP


def test_overlay_disabled_when_stale(blank_image, monkeypatch):
    """No packet overlay when session is stale (probe down)."""
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: False)
    overlay = auto_turn._packet_overlay_state("career_home")
    assert overlay is None


def test_overlay_disabled_via_env(blank_image, monkeypatch):
    """UMA_PACKET_STATE=0 shuts the overlay off entirely."""
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", False)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    overlay = auto_turn._packet_overlay_state("career_home")
    assert overlay is None


def test_overlay_handles_summer_screen(fresh_response, blank_image, monkeypatch):
    """career_home_summer should boost current_turn floor to 25."""
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: fresh_response,
    )
    state = auto_turn.build_game_state(blank_image, "career_home_summer")
    # Fixture turn is 21; summer floor lifts to 25.
    assert state.current_turn >= 25


def test_overlay_passes_explicit_energy(fresh_response, blank_image, monkeypatch):
    """Explicit energy arg overrides the packet's energy field."""
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: fresh_response,
    )
    state = auto_turn.build_game_state(blank_image, "career_home", energy=42)
    assert state.energy == 42


# --- Step 3: packet-driven training preview --------------------------------

def test_build_packet_training_tiles_returns_none_when_disabled(monkeypatch):
    """UMA_PACKET_TRAINING=0 short-circuits — preview loop falls through to OCR."""
    monkeypatch.setattr(auto_turn, "_PACKET_TRAINING_ENABLED", False)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    assert auto_turn._build_packet_training_tiles() is None


def test_build_packet_training_tiles_returns_none_when_stale(monkeypatch):
    """Stale session (probe down) → fall through to OCR even with flag on."""
    monkeypatch.setattr(auto_turn, "_PACKET_TRAINING_ENABLED", True)
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: False)
    assert auto_turn._build_packet_training_tiles() is None


def test_build_packet_training_tiles_populates_from_response(fresh_response, monkeypatch):
    """Fresh session + flag on → tiles built from packet, tap_coords stamped in."""
    monkeypatch.setattr(auto_turn, "_PACKET_TRAINING_ENABLED", True)
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: fresh_response,
    )

    tiles = auto_turn._build_packet_training_tiles()
    assert tiles is not None and len(tiles) == 5

    # Each tile gets coords from auto_turn.TRAINING_TILES (drop-in for the
    # rest of handle_training, which taps tap_coords to confirm).
    expected = {k.lower(): v for k, v in auto_turn.TRAINING_TILES.items()}
    for tile in tiles:
        assert tile.tap_coords == expected[tile.stat_type.value]
        # Adapter populates failure_rate (0.0-1.0) and gains; verify shape.
        assert 0.0 <= tile.failure_rate <= 1.0
        assert isinstance(tile.stat_gains, dict)


def test_inventory_packet_skip(monkeypatch):
    """read_inventory_from_training_items should pull from packet and skip
    BTN_TRAINING_ITEMS navigation when scenario_state inventory is present.
    Validates the user-facing fix: bot stops opening Training Items every turn."""
    import msgpack
    free_data_fixture = (
        Path(__file__).parent / "fixtures" / "free_data_set_response.msgpack"
    )
    if not free_data_fixture.exists():
        pytest.skip("free_data_set fixture not present")
    response = msgpack.unpackb(
        free_data_fixture.read_bytes(), raw=False, strict_map_key=False
    )

    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: response,
    )

    # Trip an exception if we ever tap or screenshot — the packet path must
    # not navigate to Training Items at all.
    def _tripwire(*args, **kwargs):
        raise AssertionError(f"unexpected UI navigation: args={args}")
    monkeypatch.setattr(auto_turn, "tap", _tripwire)
    monkeypatch.setattr(auto_turn, "screenshot", _tripwire)

    auto_turn._inventory_checked = False
    auto_turn.read_inventory_from_training_items()

    assert auto_turn._inventory_checked is True
    # Step 7's free_data_set fixture has 3 inventory entries; verify the sync
    # ran (length will match whatever the fixture carries).
    assert len(auto_turn._shop_manager.inventory) > 0


def test_packet_overlay_populates_conditions(fresh_response, blank_image, monkeypatch):
    """Step 4: chara_effect_id_array → _active_conditions / _positive_statuses."""
    data = fresh_response.get("data") if "data" in fresh_response else fresh_response
    chara = dict(data.get("chara_info") or {})
    chara["chara_effect_id_array"] = [1, 8]
    new_data = dict(data)
    new_data["chara_info"] = chara
    mutated = (
        {**fresh_response, "data": new_data}
        if "data" in fresh_response
        else new_data
    )

    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: mutated,
    )
    monkeypatch.setattr(auto_turn, "_active_conditions", [])
    monkeypatch.setattr(auto_turn, "_positive_statuses", [])

    auto_turn.build_game_state(blank_image, "career_home")

    assert auto_turn._active_conditions == ["night owl"]
    assert auto_turn._positive_statuses == ["charming"]


def test_overlay_syncs_cached_aptitudes(fresh_response, blank_image, monkeypatch):
    """Step 6: overlay path populates _cached_aptitudes from packet data so
    downstream callers (run-style picker, etc.) don't need the Full Stats OCR pass."""
    monkeypatch.setattr(auto_turn, "_PACKET_STATE_ENABLED", True)
    monkeypatch.setattr(auto_turn._session_tailer, "is_fresh", lambda: True)
    monkeypatch.setattr(
        auto_turn._session_tailer,
        "latest_response",
        lambda *, endpoint_keys=None: fresh_response,
    )
    # Start with no cached aptitudes — packet should fill them.
    monkeypatch.setattr(auto_turn, "_cached_aptitudes", None)

    state = auto_turn.build_game_state(blank_image, "career_home")

    # Adapter resolves chara_info.proper_* into named grades on
    # state.trainee_aptitudes; module-level cache mirrors it after overlay.
    assert state.trainee_aptitudes
    assert auto_turn._cached_aptitudes == state.trainee_aptitudes
