"""Tests for uma_trainer.knowledge.skill_catalog.SkillCatalog.

These tests load the real `data/master.mdb` and assert against known
trainee preset skills (card_id=101701, Sirius Riko). Skipped if master.mdb
is missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MDB = ROOT / "data" / "master.mdb"

if not MDB.exists():
    pytest.skip("master.mdb not present", allow_module_level=True)

from uma_trainer.knowledge.skill_catalog import (  # noqa: E402
    BuyableSkill,
    SkillCatalog,
)


@pytest.fixture(scope="module")
def catalog() -> SkillCatalog:
    return SkillCatalog(MDB)


def test_preset_for_card_returns_known_riko_skills(catalog: SkillCatalog):
    preset = catalog.preset_for_card(101701)
    assert len(preset) == 7  # confirmed via direct master.mdb query
    by_name = {s.name: s for s in preset}
    assert "Corner Adept ○" in by_name
    assert by_name["Corner Adept ○"].base_cost == 180
    assert by_name["Corner Adept ○"].rarity == 1
    assert by_name["Corner Adept ○"].group_id == 20033

    # Need-rank covers the full unlock progression.
    ranks = sorted(s.need_rank for s in preset)
    assert ranks == [0, 0, 0, 2, 3, 4, 5]


def test_preset_for_unknown_card_returns_empty(catalog: SkillCatalog):
    assert catalog.preset_for_card(0) == ()
    assert catalog.preset_for_card(999999999) == ()


def test_resolve_hint_finds_skill_by_group_and_rarity(catalog: SkillCatalog):
    # group_id=20033, rarity=1 → Corner Adept ○ (skill_id=200332)
    bs = catalog.resolve_hint(group_id=20033, rarity=1, hint_level=1)
    assert bs is not None
    assert bs.skill_id == 200332
    assert bs.name == "Corner Adept ○"
    assert bs.is_hint_only is True
    assert bs.hint_level == 1


def test_resolve_hint_unknown_group_returns_none(catalog: SkillCatalog):
    assert catalog.resolve_hint(group_id=0, rarity=1, hint_level=1) is None
    assert (
        catalog.resolve_hint(group_id=99999999, rarity=1, hint_level=1) is None
    )


def test_effective_cost_applies_discount():
    bs = BuyableSkill(
        skill_id=1, name="x", base_cost=100, hint_level=0,
    )
    assert bs.effective_cost == 100

    bs.hint_level = 1
    assert bs.effective_cost == 90  # 10% off

    bs.hint_level = 5
    assert bs.effective_cost == 70  # 30% off
