"""SHA256-keyed LLM response cache backed by SQLite."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class LLMCache:
    """Persistent cache for LLM responses to avoid redundant API calls.

    Keys are SHA256(model_name + serialized_input).
    Entries expire after ttl_hours (default 168 = 1 week).
    """

    def __init__(self, db_path: str, ttl_hours: int = 168) -> None:
        self.db_path = Path(db_path)
        self.ttl_hours = ttl_hours
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_cache (
                cache_key TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_cache_expires ON llm_cache (expires_at)"
        )
        self._conn.commit()

    def make_key(self, model: str, *args) -> str:
        """Build a cache key from model name and variable arguments."""
        payload = model + json.dumps(args, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, key: str) -> str | None:
        """Return cached response string, or None if missing/expired."""
        self._evict_expired()
        row = self._conn.execute(
            "SELECT response FROM llm_cache WHERE cache_key = ? AND expires_at > ?",
            (key, datetime.now(tz=timezone.utc).isoformat()),
        ).fetchone()
        if row:
            logger.debug("LLM cache hit: %s...", key[:16])
            return row[0]
        return None

    def set(self, key: str, response: str, model: str = "unknown") -> None:
        """Store a response in the cache."""
        expires = (datetime.now(tz=timezone.utc) + timedelta(hours=self.ttl_hours)).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO llm_cache (cache_key, response, model, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, response, model, expires),
        )
        self._conn.commit()
        logger.debug("LLM cache set: %s...", key[:16])

    def _evict_expired(self) -> None:
        """Delete expired entries (runs occasionally)."""
        import random
        if random.random() < 0.02:  # 2% chance per call
            now = datetime.now(tz=timezone.utc).isoformat()
            self._conn.execute(
                "DELETE FROM llm_cache WHERE expires_at < ?", (now,)
            )
            self._conn.commit()

    def clear(self) -> None:
        """Remove all cached entries."""
        self._conn.execute("DELETE FROM llm_cache")
        self._conn.commit()
