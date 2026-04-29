"""Tests for ``carrotjuicer.state_adapter.game_state_from_response``.

Uses a real msgpack response captured from a Trackblazer training-home turn
(see ``tests/fixtures/home_response_turn21.msgpack``). Verifies the adapter
produces a GameState whose stats, mood, turn, and training tiles match what
the server sent.
"""
from __future__ import annotations

from pathlib import Path

import msgpack
import pytest

from uma_trainer.perception.carrotjuicer.state_adapter import (
    CardRegistry,
    game_state_from_response,
)
from uma_trainer.types import Mood, ScreenState, StatType

FIXTURE = Path(__file__).parent / "fixtures" / "home_response_turn21.msgpack"
RACE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "race_condition_response.msgpack"
)
FREE_DATA_FIXTURE = (
    Path(__file__).parent / "fixtures" / "free_data_set_response.msgpack"
)
ACTIVE_EFFECTS_FIXTURE = (
    Path(__file__).parent / "fixtures" / "active_effects_response.msgpack"
)
MDB_PATH = Path("data/master.mdb")


@pytest.fixture
def response() -> dict:
    return msgpack.unpackb(FIXTURE.read_bytes(), raw=False, strict_map_key=False)


def test_trainee_core_fields(response):
    gs = game_state_from_response(response)
    assert gs.screen == ScreenState.TRAINING
    assert gs.stats.speed == 223
    assert gs.energy == 69
    assert gs.mood == Mood.GREAT              # motivation=5
    assert gs.current_turn == 21
    assert gs.scenario.startswith("scenario_")
    # Aptitudes come through as letter grades
    assert gs.trainee_aptitudes["turf"] in {"A", "S", "B"}


def test_training_tiles_populated(response):
    gs = game_state_from_response(response)
    # Exactly 5 stat training tiles (101 Speed, 102 Power, 103 Guts, 105 Stamina, 106 Wit)
    assert len(gs.training_tiles) == 5
    stats = {t.stat_type for t in gs.training_tiles}
    assert stats == {StatType.SPEED, StatType.POWER, StatType.GUTS,
                     StatType.STAMINA, StatType.WIT}

    # Every tile has gains: at least one positive stat delta
    for tile in gs.training_tiles:
        assert tile.stat_gains, f"{tile.stat_type} missing stat_gains"
        pos_gains = [v for v in tile.stat_gains.values() if v > 0]
        assert pos_gains, f"{tile.stat_type} has no positive gains: {tile.stat_gains}"

    # Failure rate is a float in [0, 1]
    for tile in gs.training_tiles:
        assert 0.0 <= tile.failure_rate <= 1.0


def test_support_cards_present(response):
    gs = game_state_from_response(response)
    # 6 slots in the deck
    assert len(gs.support_cards) == 6
    # Cards are ordered by position 1..6
    # Bond levels arrive as integers in [0, 100]
    for sc in gs.support_cards:
        assert 0 <= sc.bond_level <= 100


def test_partner_names_without_registry(response):
    gs = game_state_from_response(response, registry=None)
    # Without a registry we still get tile partners via fallback slot names
    for tile in gs.training_tiles:
        for card_name in tile.support_cards:
            assert card_name, "partner name should never be empty"


@pytest.mark.skipif(not MDB_PATH.exists(), reason="master.mdb not available")
def test_partner_names_resolved_with_registry(response):
    reg = CardRegistry(MDB_PATH)
    gs = game_state_from_response(response, registry=reg)
    # Support cards should carry real names (text_data lookup)
    for sc in gs.support_cards:
        if int(sc.card_id) == 0:
            continue
        assert sc.name and not sc.name.startswith("card_"), (
            f"card {sc.card_id} did not resolve: {sc.name!r}"
        )
    # At least one tile should have at least one named partner
    any_partner = any(tile.support_cards for tile in gs.training_tiles)
    assert any_partner


@pytest.fixture
def race_response() -> dict:
    return msgpack.unpackb(
        RACE_FIXTURE.read_bytes(), raw=False, strict_map_key=False,
    )


def test_upcoming_races_empty_when_array_missing(response):
    """home_response_turn21.msgpack has no race_condition_array — gs.upcoming_races
    must be an empty list (not raise, not stale)."""
    gs = game_state_from_response(response)
    assert gs.upcoming_races == []


def test_upcoming_races_populated_without_registry(race_response):
    """Without a registry, every race_condition entry still produces an
    UpcomingRace stub carrying program_id + weather + ground_condition."""
    gs = game_state_from_response(race_response)
    assert gs.upcoming_races, "expected packet race_condition_array to populate"
    # Same count as the raw array
    inner = race_response.get("data", race_response)
    assert len(gs.upcoming_races) == len(inner["race_condition_array"])
    for r in gs.upcoming_races:
        assert r.program_id > 0
        assert 0 <= r.weather <= 4
        assert 0 <= r.ground_condition <= 4


@pytest.mark.skipif(not MDB_PATH.exists(), reason="master.mdb not available")
def test_upcoming_races_resolved_with_registry(race_response):
    """With a registry, every entry is hydrated from master.mdb:
    name, grade, distance, surface, month, half all populated."""
    reg = CardRegistry(MDB_PATH)
    gs = game_state_from_response(race_response, registry=reg)
    assert gs.upcoming_races
    for r in gs.upcoming_races:
        assert r.name and not r.name.startswith("race_"), (
            f"race {r.race_id} did not resolve: {r.name!r}"
        )
        assert r.distance_m > 0
        assert r.surface in {"turf", "dirt"}
        assert 1 <= r.month <= 12
        assert r.half in {"early", "late"}


@pytest.fixture
def free_data_response() -> dict:
    return msgpack.unpackb(
        FREE_DATA_FIXTURE.read_bytes(), raw=False, strict_map_key=False,
    )


def test_scenario_state_none_when_free_data_set_missing(response):
    """home_response_turn21.msgpack has no free_data_set — scenario_state
    must remain None, not raise."""
    gs = game_state_from_response(response)
    assert gs.scenario_state is None


def test_scenario_state_populated_from_free_data_set(free_data_response):
    """The fixture has free_data_set with coin=5, win_points=10, 5 pickups,
    3 inventory entries. The adapter must surface all of those into
    ``state.scenario_state``."""
    gs = game_state_from_response(free_data_response)
    ss = gs.scenario_state
    assert ss is not None
    assert ss.scenario_key == "trackblazer"
    assert ss.coin == 5
    assert ss.score == 10
    assert len(ss.pick_up_items) == 5
    assert len(ss.inventory) == 3


def test_scenario_state_pickups_have_item_keys(free_data_response):
    """Pickups must round-trip ``item_id`` and resolve ``item_key`` for
    items in ITEM_CATALOGUE (8002 -> motivating_mega for this fixture)."""
    gs = game_state_from_response(free_data_response)
    ss = gs.scenario_state
    assert ss is not None
    # First pickup is item_id 8002 = Motivating Megaphone
    p0 = ss.pick_up_items[0]
    assert p0.item_id == 8002
    assert p0.item_key == "motivating_mega"
    assert p0.coin_num == 55
    assert p0.original_coin_num == 55
    assert p0.limit_buy_count == 1
    # Stock helper works
    assert p0.stock_remaining == 1
    assert not p0.is_on_sale


def test_scenario_state_inventory_resolves_item_keys(free_data_response):
    """Inventory must hydrate item_key for known item_ids (3101 = Grilled
    Carrots, 11002 = Master Cleat Hammer)."""
    gs = game_state_from_response(free_data_response)
    ss = gs.scenario_state
    assert ss is not None
    by_key = {e.item_key: e.num for e in ss.inventory if e.item_key}
    assert by_key.get("grilled_carrots") == 1
    assert by_key.get("master_hammer") == 1


def test_shop_manager_consumes_scenario_state(free_data_response):
    """ShopManager.apply_packet_state pulls inventory from packet, and
    get_packet_shop_offerings returns a tier-ranked list."""
    from uma_trainer.decision.shop_manager import ShopManager

    gs = game_state_from_response(free_data_response)
    sm = ShopManager()
    applied = sm.apply_packet_state(gs)
    assert applied is True
    inv = sm.inventory
    assert inv.get("grilled_carrots") == 1
    assert inv.get("master_hammer") == 1

    offerings = sm.get_packet_shop_offerings(gs)
    assert offerings is not None
    # All offerings are mapped + buyable; first one must be SS-tier
    # (motivating_mega = S, master_hammer = SS, etc.)
    names = [item.name for item, _ in offerings]
    assert names, "expected at least one buyable packet offering"


def test_shop_manager_ignores_packet_when_scenario_state_none():
    """When state.scenario_state is None, packet helpers must no-op so OCR
    and yaml-driven flows take over for non-Trackblazer scenarios."""
    from uma_trainer.decision.shop_manager import ShopManager
    from uma_trainer.types import GameState

    sm = ShopManager()
    gs = GameState()
    assert sm.apply_packet_state(gs) is False
    assert sm.get_packet_shop_offerings(gs) is None


@pytest.fixture
def active_effects_response() -> dict:
    return msgpack.unpackb(
        ACTIVE_EFFECTS_FIXTURE.read_bytes(), raw=False, strict_map_key=False,
    )


def test_scenario_state_active_effects_empty_when_none(free_data_response):
    """The free_data_set fixture has no active item effects — list must be []."""
    gs = game_state_from_response(free_data_response)
    assert gs.scenario_state is not None
    assert gs.scenario_state.active_effects == []


def test_scenario_state_active_effects_populated(active_effects_response):
    """The active-effects fixture has 3 entries (Motivating Megaphone +
    Speed Ankle Weights x2 effect_types). All three round-trip with
    item_id, item_key (mapped), begin_turn/end_turn, and turns_remaining
    works against the chara_info.turn = 37."""
    gs = game_state_from_response(active_effects_response)
    ss = gs.scenario_state
    assert ss is not None
    effects = ss.active_effects
    assert len(effects) == 3

    # Map by use_id for stable assertions
    by_use = {e.use_id: e for e in effects}
    mega = by_use[4]
    assert mega.item_id == 8002
    assert mega.item_key == "motivating_mega"
    assert mega.effect_type == 11
    assert mega.begin_turn == 37
    assert mega.end_turn == 39
    assert mega.turns_remaining(37) == 3
    assert mega.turns_remaining(40) == 0  # past the window
    assert mega.turns_remaining(0) == 0   # unknown turn → 0

    # Both ankle-weight entries map to the same item but different effect_type
    ankle_a = by_use[5]
    ankle_b = by_use[6]
    assert ankle_a.item_id == ankle_b.item_id == 9001
    assert ankle_a.item_key == ankle_b.item_key == "speed_ankle_weights"
    assert {ankle_a.effect_type, ankle_b.effect_type} == {11, 12}
    assert ankle_a.end_turn == ankle_b.end_turn == 37


def test_shop_manager_active_effects_dedupe_by_item(active_effects_response):
    """ShopManager.apply_packet_state collapses the 3 raw effect entries to
    2 distinct active items (Motivating Mega + Speed Ankle Weights), with
    multiplier/zero_failure pulled from ITEM_TRAINING_EFFECTS."""
    from uma_trainer.decision.shop_manager import ShopManager

    gs = game_state_from_response(active_effects_response)
    assert gs.current_turn == 37
    sm = ShopManager()
    assert sm.apply_packet_state(gs) is True
    by_key = {e.item_key: e for e in sm._active_effects}
    assert set(by_key) == {"motivating_mega", "speed_ankle_weights"}

    mega = by_key["motivating_mega"]
    assert mega.turns_remaining == 3
    assert mega.multiplier == pytest.approx(1.4)

    ankle = by_key["speed_ankle_weights"]
    assert ankle.turns_remaining == 1
    assert ankle.multiplier == pytest.approx(1.5)


def test_shop_manager_active_effects_skipped_when_no_packet():
    """No scenario_state → _active_effects untouched (caller should run OCR)."""
    from uma_trainer.decision.shop_manager import ShopManager, ActiveEffect
    from uma_trainer.types import GameState

    sm = ShopManager()
    sentinel = ActiveEffect(item_key="empowering_mega", turns_remaining=2)
    sm._active_effects = [sentinel]
    gs = GameState()
    assert sm.apply_packet_state(gs) is False
    assert sm._active_effects == [sentinel]


@pytest.mark.skipif(not MDB_PATH.exists(), reason="master.mdb not available")
def test_scenario_npc_resolved_with_registry(response):
    """If the response has any training_partner_id in the 100s (scenario NPC),
    the registry should resolve them to a named partner."""
    reg = CardRegistry(MDB_PATH)
    gs = game_state_from_response(response, registry=reg)
    # Verify at least one tile carries an NPC name from text_data (not "npc_XX")
    npc_names = [
        name
        for tile in gs.training_tiles
        for name in tile.support_cards
        if not name.startswith("slot") and not name.startswith("npc_")
    ]
    assert npc_names, "expected at least one resolved partner name"
