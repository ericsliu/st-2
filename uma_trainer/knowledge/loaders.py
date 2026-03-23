"""Bulk import JSON knowledge base files into SQLite."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uma_trainer.knowledge.database import KnowledgeBase

logger = logging.getLogger(__name__)


class KnowledgeBaseLoader:
    """Imports JSON data files from the data/ directory into SQLite."""

    def __init__(self, db: "KnowledgeBase", data_dir: str = "data") -> None:
        self.db = db
        self.data_dir = Path(data_dir)

    def load_all(self) -> None:
        """Import all available data files."""
        self.load_events()
        self.load_skills()
        self.load_support_cards()
        self.load_characters()
        self.load_race_calendar()
        logger.info("Knowledge base fully loaded")

    def load_events(self) -> int:
        """Load events from data/events/generic_events.json and character directories."""
        count = 0
        events_dir = self.data_dir / "events"
        if not events_dir.exists():
            return 0

        for json_file in events_dir.rglob("*.json"):
            try:
                events = json.loads(json_file.read_text())
                for event in events:
                    self.db.event_lookup.insert(
                        event_text=event["event_text"],
                        choice_index=event["best_choice_index"],
                        effects=event.get("choice_effects", []),
                        character_id=event.get("character_id"),
                        source=event.get("source", "scraper"),
                        confidence=event.get("confidence", 1.0),
                    )
                    count += 1
            except Exception as e:
                logger.error("Failed to load events from %s: %s", json_file, e)

        logger.info("Loaded %d events", count)
        return count

    def load_skills(self) -> int:
        """Load skills from data/skills.json."""
        skills_file = self.data_dir / "skills.json"
        if not skills_file.exists():
            return 0

        count = 0
        skills = json.loads(skills_file.read_text())
        rows = [
            (
                s["skill_id"],
                s["name"],
                s["name"].strip().lower(),
                s.get("description", ""),
                s.get("category", "unknown"),
                json.dumps(s.get("stat_requirements", {})),
                s.get("priority", 5),
            )
            for s in skills
        ]
        self.db.executemany(
            """
            INSERT OR REPLACE INTO skills
                (skill_id, name, name_lower, description, category,
                 stat_requirements, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        count = len(rows)
        logger.info("Loaded %d skills", count)
        return count

    def load_support_cards(self) -> int:
        """Load support cards from data/support_cards.json."""
        cards_file = self.data_dir / "support_cards.json"
        if not cards_file.exists():
            return 0

        cards = json.loads(cards_file.read_text())
        rows = [
            (
                c["card_id"],
                c["name"],
                c.get("type", "speed"),
                c.get("rarity", "R"),
                c.get("tier", 3),
                json.dumps(c.get("bond_skills", [])),
                json.dumps(c.get("training_bonuses", {})),
            )
            for c in cards
        ]
        self.db.executemany(
            """
            INSERT OR REPLACE INTO support_cards
                (card_id, name, type, rarity, tier, bond_skills, training_bonuses)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        logger.info("Loaded %d support cards", len(rows))
        return len(rows)

    def load_characters(self) -> int:
        """Load characters from data/characters.json."""
        chars_file = self.data_dir / "characters.json"
        if not chars_file.exists():
            return 0

        chars = json.loads(chars_file.read_text())
        rows = [
            (
                c["character_id"],
                c["name"],
                json.dumps(c.get("aptitudes", {})),
                c.get("scenario", "ura_finale"),
            )
            for c in chars
        ]
        self.db.executemany(
            "INSERT OR REPLACE INTO characters (character_id, name, aptitudes, scenario) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        logger.info("Loaded %d characters", len(rows))
        return len(rows)

    def load_race_calendar(self) -> int:
        """Load race calendar from data/race_calendar.json."""
        cal_file = self.data_dir / "race_calendar.json"
        if not cal_file.exists():
            return 0

        races = json.loads(cal_file.read_text())
        rows = [
            (
                r["race_id"],
                r["name"],
                r.get("grade", "G3"),
                r.get("distance", 1600),
                r.get("surface", "turf"),
                r.get("direction", "right"),
                r.get("season", "spring"),
                r.get("year", 1),
                r.get("fan_reward", 0),
            )
            for r in races
        ]
        self.db.executemany(
            """
            INSERT OR REPLACE INTO race_calendar
                (race_id, name, grade, distance, surface, direction, season,
                 year, fan_reward)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        logger.info("Loaded %d races", len(rows))
        return len(rows)
