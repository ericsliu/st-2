"""Tests for the packet-driven fast path inside ``handle_skill_shop``.

When ``SessionTailer.is_fresh()`` and ``GameState.buyable_skills`` is
populated, ``handle_skill_shop`` should:

  * skip ``_scan_all_skills`` entirely,
  * call ``SkillBuyer.decide_from_packet`` for the buy plan,
  * tap the ``+`` button for each target whose name appears in the OCR
    skill list, and
  * confirm the purchase via the green Confirm button.

The OCR fallback path is exercised by the rest of the suite; this file
only verifies the packet branch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Importing auto_turn at module load is heavy but necessary — the function
# under test is defined there and pulls in the whole runtime.
from scripts import auto_turn  # noqa: E402
from uma_trainer.knowledge.skill_catalog import BuyableSkill  # noqa: E402
from uma_trainer.types import (  # noqa: E402
    GameState,
    ScreenState,
    SkillOption,
)


def _make_targets() -> list[BuyableSkill]:
    return [
        BuyableSkill(
            skill_id=20011,
            name="Corner Adept",
            base_cost=180,
            hint_level=1,
            is_hint_only=False,
            group_id=20011,
            rarity=1,
        ),
        BuyableSkill(
            skill_id=20012,
            name="Straightaway Adept",
            base_cost=180,
            hint_level=0,
            is_hint_only=True,
            group_id=20012,
            rarity=1,
        ),
    ]


@pytest.fixture
def packet_state() -> GameState:
    gs = GameState(screen=ScreenState.SKILL_SHOP, skill_pts=2000)
    gs.buyable_skills = _make_targets()
    return gs


def test_packet_path_taps_targets_and_skips_scan(monkeypatch, packet_state):
    targets = list(packet_state.buyable_skills)

    # --- Module-level singletons -----------------------------------------
    monkeypatch.setattr(auto_turn, "_game_state", packet_state)
    monkeypatch.setattr(
        auto_turn._session_tailer, "is_fresh", lambda: True, raising=False
    )
    monkeypatch.setattr(
        auto_turn._skill_buyer,
        "decide_from_packet",
        lambda state, **kw: list(targets),
        raising=False,
    )

    # --- Block the OCR scan path so we can detect a leak -----------------
    scan_calls: list[bool] = []

    def _fail_scan(*a, **k):  # pragma: no cover - asserted via len()
        scan_calls.append(True)
        return [], 0

    monkeypatch.setattr(auto_turn, "_scan_all_skills", _fail_scan)

    # --- Side-effect helpers ---------------------------------------------
    taps: list[tuple] = []

    def fake_tap(x, y, *a, **kw):
        taps.append((int(x), int(y)))

    monkeypatch.setattr(auto_turn, "tap", fake_tap)

    monkeypatch.setattr(auto_turn, "screenshot", lambda *a, **kw: object())
    monkeypatch.setattr(
        auto_turn, "scroll_down", lambda *a, **kw: None
    )
    monkeypatch.setattr(auto_turn, "scroll_up", lambda *a, **kw: None)
    monkeypatch.setattr(auto_turn, "find_green_button", lambda *a, **kw: (540, 1600))
    monkeypatch.setattr(auto_turn, "detect_screen", lambda *a, **kw: "career_home")
    # Speed up the test — every time.sleep call collapses to a no-op.
    monkeypatch.setattr(auto_turn.time, "sleep", lambda *a, **kw: None)

    # OCR returns the targets on page 1 with predictable tap coords.
    expected_coords = {
        "Corner Adept": (960, 770),
        "Straightaway Adept": (960, 870),
    }
    page_results = [
        SkillOption(name=name, cost=180, tap_coords=coords)
        for name, coords in expected_coords.items()
    ]

    def fake_ocr_skill_list(img):
        return list(page_results)

    monkeypatch.setattr(auto_turn, "_ocr_skill_list", fake_ocr_skill_list)

    # --- Execute ----------------------------------------------------------
    result = auto_turn.handle_skill_shop(img=object(), force_recovery=False)

    # --- Assertions -------------------------------------------------------
    assert scan_calls == [], "_scan_all_skills must NOT run on packet path"
    assert result == "skill_shop"

    # The two + button taps must have happened with the OCR-derived coords.
    plus_taps = [t for t in taps if t in expected_coords.values()]
    assert len(plus_taps) == 2
    assert set(plus_taps) == set(expected_coords.values())


def test_packet_path_skipped_when_session_stale(monkeypatch, packet_state):
    """When SessionTailer.is_fresh() is False, the OCR fallback must run."""
    monkeypatch.setattr(auto_turn, "_game_state", packet_state)
    monkeypatch.setattr(
        auto_turn._session_tailer, "is_fresh", lambda: False, raising=False
    )

    decide_calls: list[bool] = []
    monkeypatch.setattr(
        auto_turn._skill_buyer,
        "decide_from_packet",
        lambda state, **kw: decide_calls.append(True) or [],
        raising=False,
    )

    scan_calls: list[bool] = []

    def fake_scan():
        scan_calls.append(True)
        return [], 0

    monkeypatch.setattr(auto_turn, "_scan_all_skills", fake_scan)
    monkeypatch.setattr(auto_turn, "tap", lambda *a, **kw: None)
    monkeypatch.setattr(auto_turn, "time", auto_turn.time)

    result = auto_turn.handle_skill_shop(img=object(), force_recovery=False)

    assert decide_calls == [], "decide_from_packet must NOT run on stale session"
    assert scan_calls == [True], "_scan_all_skills must run on stale session"
    # OCR scan returned no skills → handler exits via skill_back.
    assert result == "skill_back"
