"""Skill knowledge base queries."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uma_trainer.knowledge.database import KnowledgeBase

logger = logging.getLogger(__name__)


@dataclass
class SkillRecord:
    skill_id: str
    name: str
    description: str
    category: str
    priority: int


class SkillLookup:
    def __init__(self, db: "KnowledgeBase") -> None:
        self.db = db

    def find_by_id(self, skill_id: str) -> SkillRecord | None:
        row = self.db.query_one(
            "SELECT * FROM skills WHERE skill_id = ?", (skill_id,)
        )
        return self._to_record(row) if row else None

    def find_by_name(self, name: str) -> SkillRecord | None:
        row = self.db.query_one(
            "SELECT * FROM skills WHERE name_lower = ?", (name.strip().lower(),)
        )
        return self._to_record(row) if row else None

    def get_all(self) -> list[SkillRecord]:
        rows = self.db.query_all("SELECT * FROM skills ORDER BY priority DESC")
        return [self._to_record(r) for r in rows]

    def _to_record(self, row) -> SkillRecord:
        return SkillRecord(
            skill_id=row["skill_id"],
            name=row["name"],
            description=row["description"] or "",
            category=row["category"] or "unknown",
            priority=row["priority"],
        )
