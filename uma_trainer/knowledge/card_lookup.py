"""Support card knowledge base queries."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uma_trainer.knowledge.database import KnowledgeBase

logger = logging.getLogger(__name__)


@dataclass
class CardRecord:
    card_id: str
    name: str
    type: str
    rarity: str
    tier: int
    training_bonuses: dict[str, float]


class CardLookup:
    def __init__(self, db: "KnowledgeBase") -> None:
        self.db = db

    def find_by_id(self, card_id: str) -> CardRecord | None:
        row = self.db.query_one(
            "SELECT * FROM support_cards WHERE card_id = ?", (card_id,)
        )
        return self._to_record(row) if row else None

    def get_all_tier_s(self) -> list[CardRecord]:
        rows = self.db.query_all(
            "SELECT * FROM support_cards WHERE tier = 1 ORDER BY name"
        )
        return [self._to_record(r) for r in rows]

    def _to_record(self, row) -> CardRecord:
        return CardRecord(
            card_id=row["card_id"],
            name=row["name"],
            type=row["type"],
            rarity=row["rarity"],
            tier=row["tier"],
            training_bonuses=json.loads(row["training_bonuses"] or "{}"),
        )
