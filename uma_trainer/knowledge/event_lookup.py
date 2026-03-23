"""Event lookup: exact hash and fuzzy matching against the knowledge base."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uma_trainer.knowledge.database import KnowledgeBase

logger = logging.getLogger(__name__)


@dataclass
class EventRecord:
    id: int
    text_hash: str
    event_text: str
    character_id: str | None
    best_choice_index: int
    choice_effects: list
    source: str
    confidence: float
    score: float = 100.0  # Populated by fuzzy match


class EventLookup:
    """Provides exact and fuzzy event text matching."""

    def __init__(self, db: "KnowledgeBase") -> None:
        self.db = db
        self._corpus: list[tuple[str, int]] | None = None  # (text, id) cache

    def _normalize(self, text: str) -> str:
        return text.strip().lower()

    def _hash(self, text: str) -> str:
        return hashlib.sha256(self._normalize(text).encode()).hexdigest()

    def find_exact(self, event_text: str) -> EventRecord | None:
        """Look up an event by exact (hashed) text match."""
        h = self._hash(event_text)
        row = self.db.query_one(
            "SELECT * FROM events WHERE text_hash = ?", (h,)
        )
        if row:
            return self._row_to_record(row)
        return None

    def find_fuzzy(
        self, event_text: str, threshold: int = 85
    ) -> EventRecord | None:
        """Find the closest matching event using rapidfuzz.

        Returns the best match if its similarity score >= threshold, else None.
        """
        try:
            from rapidfuzz import process, fuzz
        except ImportError:
            logger.warning("rapidfuzz not installed — fuzzy matching disabled")
            return None

        corpus = self._get_corpus()
        if not corpus:
            return None

        texts = [t for t, _ in corpus]
        normalized = self._normalize(event_text)

        match = process.extractOne(
            normalized,
            texts,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold,
        )
        if match is None:
            return None

        matched_text, score, idx = match
        event_id = corpus[idx][1]

        row = self.db.query_one("SELECT * FROM events WHERE id = ?", (event_id,))
        if row:
            record = self._row_to_record(row)
            record.score = float(score)
            logger.debug("Fuzzy match: score=%.1f for '%s'", score, matched_text[:40])
            return record

        return None

    def insert(
        self,
        event_text: str,
        choice_index: int,
        effects: list,
        character_id: str | None = None,
        source: str = "llm",
        confidence: float = 1.0,
    ) -> None:
        """Insert or update an event record."""
        h = self._hash(event_text)
        self.db.execute(
            """
            INSERT INTO events
                (text_hash, event_text, character_id, best_choice_index,
                 choice_effects, source, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(text_hash) DO UPDATE SET
                best_choice_index = excluded.best_choice_index,
                choice_effects = excluded.choice_effects,
                confidence = excluded.confidence,
                updated_at = CURRENT_TIMESTAMP
            """,
            (h, event_text.strip(), character_id, choice_index,
             json.dumps(effects), source, confidence),
        )
        self._corpus = None  # Invalidate cache
        logger.debug("Upserted event: hash=%s...", h[:16])

    def _get_corpus(self) -> list[tuple[str, int]]:
        """Return cached (normalized_text, id) pairs for fuzzy matching."""
        if self._corpus is None:
            rows = self.db.query_all("SELECT id, event_text FROM events")
            self._corpus = [(self._normalize(r["event_text"]), r["id"]) for r in rows]
        return self._corpus

    def _row_to_record(self, row) -> EventRecord:
        return EventRecord(
            id=row["id"],
            text_hash=row["text_hash"],
            event_text=row["event_text"],
            character_id=row["character_id"],
            best_choice_index=row["best_choice_index"],
            choice_effects=json.loads(row["choice_effects"] or "[]"),
            source=row["source"],
            confidence=row["confidence"],
        )
