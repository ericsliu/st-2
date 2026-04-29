"""Resolve buyable skills for a trainee from master.mdb.

Replaces the OCR scroll-and-scan approach in `scripts/auto_turn.py` for the
Skill Shop / Learn page. The game does not send a skill list packet — the
Learn page is rendered locally from already-cached state — so we rebuild
the same view by joining `chara_info.skill_tips_array` (hints from
supports) with the trainee's preset skills from `available_skill_set`.

Mirrors the lazy-sqlite + LRU pattern used by
`uma_trainer.perception.carrotjuicer.state_adapter.CardRegistry`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Empirically calibrated against captured purchases (session_20260429_030953,
# requests 85 + 149). Request 149 was 5 skills all hint_level=1, paid 603 vs
# base 670 = ~10% off. Higher levels are extrapolated and documented as TODO
# for later recalibration; the bot only uses these for priority + budget
# estimates, so small errors don't break behaviour.
_DISCOUNT: dict[int, float] = {
    0: 1.00,
    1: 0.90,
    2: 0.85,
    3: 0.80,
    4: 0.75,
    5: 0.70,
}

# text_data category for skill names (matches knowledge.master_db).
_CAT_SKILL_NAME = 47


@dataclass
class BuyableSkill:
    """A skill the trainee can buy on the Learn page.

    `base_cost` is the SP cost from `single_mode_skill_need_point`; the actual
    SP charged is `effective_cost`, which folds in the hint discount.
    `is_hint_only=True` means the skill is offered solely because a support
    card is hinting at it (i.e. not in the trainee's own preset).
    """

    skill_id: int
    name: str
    base_cost: int
    hint_level: int = 0
    is_hint_only: bool = False
    group_id: int = 0
    rarity: int = 0
    need_rank: int = 0  # 0 if not from preset

    @property
    def effective_cost(self) -> int:
        return int(round(self.base_cost * _DISCOUNT.get(self.hint_level, 1.0)))


class SkillCatalog:
    """Read-only resolver for buyable skills, backed by master.mdb.

    Cheap to construct: the underlying sqlite connection is opened lazily on
    first query and lookups are LRU-cached for the life of the catalog.
    """

    def __init__(self, mdb_path: Path | str = "data/master.mdb") -> None:
        self.mdb_path = Path(mdb_path)
        self._conn: Optional[sqlite3.Connection] = None

    def _cursor(self) -> sqlite3.Cursor:
        if self._conn is None:
            if not self.mdb_path.exists():
                raise FileNotFoundError(
                    f"master.mdb not found at {self.mdb_path}; see "
                    f"docs/reference_master_mdb.md for extraction"
                )
            self._conn = sqlite3.connect(f"file:{self.mdb_path}?mode=ro", uri=True)
        return self._conn.cursor()

    @lru_cache(maxsize=2048)
    def _skill_meta(self, skill_id: int) -> tuple[int, int, int, str] | None:
        """Return (base_cost, group_id, rarity, name) for a skill, or None."""
        cur = self._cursor()
        meta_row = cur.execute(
            "SELECT group_id, rarity FROM skill_data WHERE id=?",
            (skill_id,),
        ).fetchone()
        if meta_row is None:
            return None
        group_id, rarity = meta_row
        cost_row = cur.execute(
            "SELECT need_skill_point FROM single_mode_skill_need_point WHERE id=?",
            (skill_id,),
        ).fetchone()
        base_cost = int(cost_row[0]) if cost_row else 0
        name_row = cur.execute(
            'SELECT text FROM text_data WHERE category=? AND "index"=?',
            (_CAT_SKILL_NAME, skill_id),
        ).fetchone()
        name = name_row[0] if name_row else f"skill_{skill_id}"
        return base_cost, int(group_id), int(rarity), name

    @lru_cache(maxsize=512)
    def preset_for_card(self, card_id: int) -> tuple[BuyableSkill, ...]:
        """Return the trainee's preset (innate + scenario) buyable skills.

        Joins `available_skill_set` (the per-card skill list) with metadata.
        Returned as a tuple so the lru_cache result is immutable; callers that
        need a list should ``list(catalog.preset_for_card(...))``.
        """
        if not card_id:
            return ()
        rows = self._cursor().execute(
            "SELECT skill_id, need_rank FROM available_skill_set "
            "WHERE available_skill_set_id=? "
            "ORDER BY need_rank, skill_id",
            (card_id,),
        ).fetchall()
        out: list[BuyableSkill] = []
        for skill_id, need_rank in rows:
            meta = self._skill_meta(int(skill_id))
            if meta is None:
                continue
            base_cost, group_id, rarity, name = meta
            out.append(
                BuyableSkill(
                    skill_id=int(skill_id),
                    name=name,
                    base_cost=base_cost,
                    group_id=group_id,
                    rarity=rarity,
                    need_rank=int(need_rank),
                    is_hint_only=False,
                )
            )
        return tuple(out)

    @lru_cache(maxsize=2048)
    def _skill_id_from_group(self, group_id: int, rarity: int) -> int | None:
        """Map (group_id, rarity) → skill_id via skill_data.

        When multiple skills share the same (group_id, rarity), prefer the
        smallest id (older / more common variant).
        """
        row = self._cursor().execute(
            "SELECT id FROM skill_data WHERE group_id=? AND rarity=? "
            "ORDER BY id LIMIT 1",
            (group_id, rarity),
        ).fetchone()
        return int(row[0]) if row else None

    def resolve_hint(
        self,
        *,
        group_id: int,
        rarity: int,
        hint_level: int,
    ) -> BuyableSkill | None:
        """Resolve a single ``skill_tips_array`` entry to a BuyableSkill.

        Returns None if the (group_id, rarity) pair isn't present in
        ``skill_data``.
        """
        if not group_id:
            return None
        skill_id = self._skill_id_from_group(int(group_id), int(rarity))
        if skill_id is None:
            return None
        meta = self._skill_meta(skill_id)
        if meta is None:
            return None
        base_cost, _gid, _rar, name = meta
        return BuyableSkill(
            skill_id=skill_id,
            name=name,
            base_cost=base_cost,
            hint_level=int(hint_level),
            is_hint_only=True,
            group_id=int(group_id),
            rarity=int(rarity),
        )


__all__ = ["BuyableSkill", "SkillCatalog"]
