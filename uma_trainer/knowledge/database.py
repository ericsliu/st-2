"""SQLite database connection and schema initialization."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from uma_trainer.knowledge.event_lookup import EventLookup
from uma_trainer.knowledge.master_db import MasterDB
from uma_trainer.knowledge.skill_lookup import SkillLookup
from uma_trainer.knowledge.card_lookup import CardLookup

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class KnowledgeBase:
    """Top-level knowledge base: owns the SQLite connection and sub-lookups.

    Optionally integrates with master.mdb (the game's own database) for
    authoritative static data on events, skills, support cards, etc.
    """

    def __init__(
        self,
        db_path: str = "data/uma_trainer.db",
        master_mdb_path: str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = self._open_connection()
        self._apply_schema()

        # Optional master.mdb for authoritative game data
        self.master_db: MasterDB | None = None
        if master_mdb_path:
            self.master_db = MasterDB(master_mdb_path)
            if not self.master_db.available:
                self.master_db = None

        self.event_lookup = EventLookup(self, master_db=self.master_db)
        self.skill_lookup = SkillLookup(self)
        self.card_lookup = CardLookup(self)

        logger.info(
            "KnowledgeBase ready at %s (master.mdb: %s)",
            self.db_path,
            "available" if self.master_db else "not available",
        )

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=10,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _apply_schema(self) -> None:
        """Create tables if they don't exist."""
        if SCHEMA_PATH.exists():
            schema_sql = SCHEMA_PATH.read_text()
            self._conn.executescript(schema_sql)
            self._conn.commit()
        else:
            logger.warning("Schema file not found at %s", SCHEMA_PATH)

    @contextmanager
    def cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager for executing queries."""
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list) -> None:
        self._conn.executemany(sql, params_list)
        self._conn.commit()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        cur = self._conn.execute(sql, params)
        return cur.fetchone()

    def query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        cur = self._conn.execute(sql, params)
        return cur.fetchall()

    def insert_run(self, run_result) -> None:
        """Log a completed run result."""
        import dataclasses
        self._conn.execute(
            """
            INSERT OR REPLACE INTO run_log
                (run_id, trainee_id, scenario, final_stats, goals_completed,
                 total_goals, turns_taken, success, notes, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                run_result.run_id,
                run_result.trainee_id,
                run_result.scenario,
                json.dumps(dataclasses.asdict(run_result.final_stats)),
                run_result.goals_completed,
                run_result.total_goals,
                run_result.turns_taken,
                int(run_result.success),
                run_result.notes,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        if self.master_db:
            self.master_db.close()
        if self._conn:
            self._conn.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
