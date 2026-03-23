#!/usr/bin/env python3
"""Import knowledge base JSON files into SQLite.

Usage:
    python scripts/import_knowledge_base.py --data data/ --db data/uma_trainer.db
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Import knowledge base from JSON files")
    parser.add_argument("--data", default="data", help="Data directory with JSON files")
    parser.add_argument("--db", default="data/uma_trainer.db", help="SQLite database path")
    parser.add_argument("--clear", action="store_true", help="Clear existing data before importing")
    args = parser.parse_args()

    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.knowledge.loaders import KnowledgeBaseLoader

    print(f"Importing knowledge base from {args.data} → {args.db}")
    kb = KnowledgeBase(args.db)

    if args.clear:
        print("Clearing existing data...")
        for table in ["events", "skills", "support_cards", "characters", "race_calendar"]:
            kb.execute(f"DELETE FROM {table}")

    loader = KnowledgeBaseLoader(kb, args.data)

    events = loader.load_events()
    skills = loader.load_skills()
    cards = loader.load_support_cards()
    chars = loader.load_characters()
    races = loader.load_race_calendar()

    print(f"\nImport complete:")
    print(f"  Events:        {events}")
    print(f"  Skills:        {skills}")
    print(f"  Support cards: {cards}")
    print(f"  Characters:    {chars}")
    print(f"  Races:         {races}")

    kb.close()


if __name__ == "__main__":
    main()
