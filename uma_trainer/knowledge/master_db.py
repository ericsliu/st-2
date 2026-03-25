"""Read-only client for the game's master.mdb SQLite database.

master.mdb ships with the Uma Musume client and contains all static game
data: events, skills, support cards, characters, race calendar, and
localised text.  Despite the .mdb extension it is a standard SQLite file.

The global/English client stores English text directly in the ``text_data``
table, so no translation patching is needed.

Usage::

    mdb = MasterDB("data/master.mdb")
    skills = mdb.get_skills()
    events = mdb.get_event_choices(story_id=401001)
    mdb.close()

Obtaining master.mdb
--------------------
The file lives at ``/data/data/com.cygames.umamusume/files/master/master.mdb``
on the Android device.  Pulling it requires root access::

    adb root
    adb pull /data/data/com.cygames.umamusume/files/master/master.mdb data/master.mdb

Or use ``scripts/pull_master_mdb.py`` which handles root toggling.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# text_data category numbers (from umamusu-translate index.json)
_CAT_CHARACTER_NAME = 6
_CAT_SKILL_NAME = 47
_CAT_SKILL_DESC = 48
_CAT_SUPPORT_CARD_NAME = 75
_CAT_SUPPORT_CARD_TITLE = 76
_CAT_RACE_NAME = 28
_CAT_EVENT_TITLE = 189


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class MasterSkill:
    id: int
    name: str
    description: str
    rarity: int
    group_id: int
    icon_id: int


@dataclass
class MasterSupportCard:
    id: int
    chara_id: int
    name: str
    title: str
    rarity: int


@dataclass
class MasterCharacter:
    id: int
    name: str


@dataclass
class MasterRace:
    program_id: int
    race_instance_id: int
    name: str
    month: int
    half: int
    distance: int
    ground: int       # 1=turf, 2=dirt
    need_fan_count: int


@dataclass
class MasterEventChoice:
    story_id: int
    event_title: str
    choice_index: int
    choice_text: str
    effect_type: int
    effect_value: int


@dataclass
class MasterEvent:
    story_id: int
    title: str
    choices: list[MasterEventChoice] = field(default_factory=list)


# ── Main class ────────────────────────────────────────────────────────────────

class MasterDB:
    """Read-only interface to master.mdb."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

        if not self.path.exists():
            logger.warning("master.mdb not found at %s — MasterDB disabled", self.path)
            return

        try:
            self._conn = sqlite3.connect(
                f"file:{self.path}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            logger.info("MasterDB opened: %s", self.path)
        except sqlite3.Error as e:
            logger.error("Failed to open master.mdb: %s", e)
            self._conn = None

    @property
    def available(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Text helpers ──────────────────────────────────────────────────────

    def get_text(self, category: int, index: int) -> str:
        """Look up a localised string from the text_data table."""
        if not self.available:
            return ""
        row = self._query_one(
            "SELECT text FROM text_data WHERE category = ? AND \"index\" = ?",
            (category, index),
        )
        return row["text"] if row else ""

    def search_text(self, category: int, query: str) -> list[tuple[int, str]]:
        """Search text_data by substring (case-insensitive).

        Returns list of (index, text) tuples.
        """
        if not self.available:
            return []
        rows = self._query_all(
            "SELECT \"index\", text FROM text_data "
            "WHERE category = ? AND text LIKE ?",
            (category, f"%{query}%"),
        )
        return [(r["index"], r["text"]) for r in rows]

    # ── Skills ────────────────────────────────────────────────────────────

    def get_skills(self) -> list[MasterSkill]:
        """Return all skills with names and descriptions."""
        if not self.available:
            return []

        rows = self._query_all(
            """
            SELECT s.id, s.rarity, s.group_id, s.icon_id,
                   n.text AS name, COALESCE(d.text, '') AS description
            FROM skill_data s
            LEFT JOIN text_data n
                ON n.category = ? AND n."index" = s.id
            LEFT JOIN text_data d
                ON d.category = ? AND d."index" = s.id
            WHERE n.text IS NOT NULL
            ORDER BY s.id
            """,
            (_CAT_SKILL_NAME, _CAT_SKILL_DESC),
        )
        return [
            MasterSkill(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                rarity=r["rarity"],
                group_id=r["group_id"],
                icon_id=r["icon_id"],
            )
            for r in rows
        ]

    def get_skill_by_name(self, name: str) -> MasterSkill | None:
        """Find a skill by exact name (case-insensitive)."""
        if not self.available:
            return None
        row = self._query_one(
            """
            SELECT s.id, s.rarity, s.group_id, s.icon_id,
                   n.text AS name, COALESCE(d.text, '') AS description
            FROM skill_data s
            JOIN text_data n
                ON n.category = ? AND n."index" = s.id
            LEFT JOIN text_data d
                ON d.category = ? AND d."index" = s.id
            WHERE LOWER(n.text) = LOWER(?)
            """,
            (_CAT_SKILL_NAME, _CAT_SKILL_DESC, name),
        )
        if not row:
            return None
        return MasterSkill(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            rarity=row["rarity"],
            group_id=row["group_id"],
            icon_id=row["icon_id"],
        )

    def get_skill_cost(self, skill_id: int) -> int | None:
        """Get the skill point cost for a skill."""
        if not self.available:
            return None
        row = self._query_one(
            "SELECT need_skill_point FROM single_mode_skill_need_point WHERE id = ?",
            (skill_id,),
        )
        return row["need_skill_point"] if row else None

    # ── Support cards ─────────────────────────────────────────────────────

    def get_support_cards(self) -> list[MasterSupportCard]:
        """Return all support cards with names."""
        if not self.available:
            return []
        rows = self._query_all(
            """
            SELECT sc.id, sc.chara_id, sc.rarity,
                   COALESCE(n.text, '') AS name,
                   COALESCE(t.text, '') AS title
            FROM support_card_data sc
            LEFT JOIN text_data n
                ON n.category = ? AND n."index" = sc.id
            LEFT JOIN text_data t
                ON t.category = ? AND t."index" = sc.id
            WHERE n.text IS NOT NULL
            ORDER BY sc.id
            """,
            (_CAT_SUPPORT_CARD_NAME, _CAT_SUPPORT_CARD_TITLE),
        )
        return [
            MasterSupportCard(
                id=r["id"],
                chara_id=r["chara_id"],
                name=r["name"],
                title=r["title"],
                rarity=r["rarity"],
            )
            for r in rows
        ]

    # ── Characters ────────────────────────────────────────────────────────

    def get_characters(self) -> list[MasterCharacter]:
        """Return all characters with names."""
        if not self.available:
            return []
        rows = self._query_all(
            """
            SELECT c.id, n.text AS name
            FROM chara_data c
            JOIN text_data n
                ON n.category = ? AND n."index" = c.id
            ORDER BY c.id
            """,
            (_CAT_CHARACTER_NAME,),
        )
        return [MasterCharacter(id=r["id"], name=r["name"]) for r in rows]

    def get_character_aptitudes(self, card_id: int) -> dict | None:
        """Get aptitudes (ground/distance/style) for a trainable card.

        Returns dict with keys like proper_ground_turf, proper_distance_mile, etc.
        Card IDs differ from character IDs — each trainable variant has its own card.
        """
        if not self.available:
            return None
        row = self._query_one(
            """
            SELECT cr.*
            FROM card_rarity_data cr
            WHERE cr.card_id = ? AND cr.rarity = (
                SELECT MAX(rarity) FROM card_rarity_data WHERE card_id = ?
            )
            """,
            (card_id, card_id),
        )
        if not row:
            return None
        return dict(row)

    # ── Race calendar ─────────────────────────────────────────────────────

    def get_race_program(self, scenario_id: int | None = None) -> list[MasterRace]:
        """Return the race calendar (single_mode_program entries).

        If scenario_id is given, filters to that scenario.
        """
        if not self.available:
            return []

        sql = """
            SELECT p.id AS program_id, p.race_instance_id,
                   p.month, p.half, p.need_fan_count,
                   COALESCE(rn.text, '') AS name,
                   COALESCE(rcs.distance, 0) AS distance,
                   COALESCE(rcs.ground, 0) AS ground
            FROM single_mode_program p
            LEFT JOIN race_instance ri ON ri.id = p.race_instance_id
            LEFT JOIN race r ON r.id = ri.race_id
            LEFT JOIN race_course_set rcs ON rcs.id = r.course_set
            LEFT JOIN text_data rn
                ON rn.category = ? AND rn."index" = ri.race_id
        """
        params: list = [_CAT_RACE_NAME]

        if scenario_id is not None:
            sql += " WHERE p.base_program_id = ?"
            params.append(scenario_id)

        sql += " ORDER BY p.month, p.half"

        rows = self._query_all(sql, tuple(params))
        return [
            MasterRace(
                program_id=r["program_id"],
                race_instance_id=r["race_instance_id"],
                name=r["name"],
                month=r["month"],
                half=r["half"],
                distance=r["distance"],
                ground=r["ground"],
                need_fan_count=r["need_fan_count"],
            )
            for r in rows
        ]

    # ── Events ────────────────────────────────────────────────────────────

    def get_event_titles(self) -> list[tuple[int, str]]:
        """Return all (story_id, title) pairs from text_data."""
        if not self.available:
            return []
        rows = self._query_all(
            "SELECT \"index\", text FROM text_data WHERE category = ?",
            (_CAT_EVENT_TITLE,),
        )
        return [(r["index"], r["text"]) for r in rows]

    def search_events(self, query: str) -> list[tuple[int, str]]:
        """Search event titles by substring."""
        return self.search_text(_CAT_EVENT_TITLE, query)

    def get_event_choices_by_story(self, story_id: int) -> list[dict]:
        """Get event choice outcomes for a story_id.

        Queries single_mode_story_data and related conclusion tables.
        Returns a list of dicts with choice details.
        """
        if not self.available:
            return []

        # The event data model varies by schema version; try common patterns
        rows = self._query_all(
            """
            SELECT *
            FROM single_mode_story_data
            WHERE id = ?
            """,
            (story_id,),
        )
        return [dict(r) for r in rows]

    # ── Bulk export (for populating the bot's own DB) ─────────────────────

    def export_skills_for_bot_db(self) -> list[dict]:
        """Export skills in a format suitable for inserting into the bot's skills table."""
        skills = self.get_skills()
        result = []
        for s in skills:
            cost = self.get_skill_cost(s.id)
            result.append({
                "skill_id": str(s.id),
                "name": s.name,
                "description": s.description,
                "rarity": s.rarity,
                "group_id": s.group_id,
                "cost": cost or 0,
            })
        return result

    def export_support_cards_for_bot_db(self) -> list[dict]:
        """Export support cards for the bot's support_cards table."""
        cards = self.get_support_cards()
        return [
            {
                "card_id": str(c.id),
                "name": c.name,
                "title": c.title,
                "chara_id": c.chara_id,
                "rarity": c.rarity,
            }
            for c in cards
        ]

    def export_characters_for_bot_db(self) -> list[dict]:
        """Export characters for the bot's characters table."""
        chars = self.get_characters()
        return [
            {"character_id": str(c.id), "name": c.name}
            for c in chars
        ]

    # ── Table introspection ───────────────────────────────────────────────

    def list_tables(self) -> list[str]:
        """List all tables in master.mdb (useful for exploration)."""
        if not self.available:
            return []
        rows = self._query_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [r["name"] for r in rows]

    def table_info(self, table: str) -> list[dict]:
        """Get column info for a table."""
        if not self.available:
            return []
        rows = self._query_all(f"PRAGMA table_info(\"{table}\")")
        return [dict(r) for r in rows]

    def sample_rows(self, table: str, limit: int = 5) -> list[dict]:
        """Get sample rows from a table (for exploration)."""
        if not self.available:
            return []
        rows = self._query_all(
            f"SELECT * FROM \"{table}\" LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    # ── Internal helpers ──────────────────────────────────────────────────

    def _query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        assert self._conn is not None
        try:
            cur = self._conn.execute(sql, params)
            return cur.fetchone()
        except sqlite3.Error as e:
            logger.debug("MasterDB query failed: %s — %s", e, sql[:80])
            return None

    def _query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        assert self._conn is not None
        try:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()
        except sqlite3.Error as e:
            logger.debug("MasterDB query failed: %s — %s", e, sql[:80])
            return []
