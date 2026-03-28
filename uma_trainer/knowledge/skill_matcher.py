"""Fuzzy-match OCR'd skill names against the master skill database."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

# Minimum fuzzy match score to accept (0-100)
MIN_MATCH_SCORE = 60


class SkillMatcher:
    """Loads skill names from data/skills.json and fuzzy-matches OCR text."""

    def __init__(self, skills_path: str | Path | None = None) -> None:
        if skills_path is None:
            skills_path = Path(__file__).resolve().parent.parent.parent / "data" / "skills.json"
        self._skills_path = Path(skills_path)
        self._names: list[str] = []
        self._name_to_entry: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._skills_path.exists():
            logger.warning("skills.json not found at %s", self._skills_path)
            return
        with open(self._skills_path) as f:
            entries = json.load(f)
        for entry in entries:
            name = entry["name"]
            self._names.append(name)
            self._name_to_entry[name] = entry
        logger.info("SkillMatcher loaded %d skill names", len(self._names))

    def match(self, ocr_text: str) -> tuple[str, int] | None:
        """Match OCR text to the closest known skill name.

        Returns (matched_name, score) or None if no good match.
        """
        if not self._names or not ocr_text or len(ocr_text) < 3:
            return None

        result = process.extractOne(
            ocr_text,
            self._names,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=MIN_MATCH_SCORE,
        )
        if result is None:
            return None

        matched_name, score, _idx = result
        return (matched_name, score)

    def get_entry(self, name: str) -> dict | None:
        """Get the full skill entry by exact name."""
        return self._name_to_entry.get(name)

    @property
    def names(self) -> list[str]:
        return self._names
