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
    """Loads skill names from data/skills.json and fuzzy-matches OCR text.

    Circle variants (○/◎) are deduplicated to base names for matching,
    since OCR cannot reliably distinguish the symbols. The actual cost
    read from screen is what matters for purchase decisions.
    """

    # Suffixes to strip for deduplication
    _CIRCLE_SUFFIXES = (" ◎", " ○")

    def __init__(self, skills_path: str | Path | None = None) -> None:
        if skills_path is None:
            skills_path = Path(__file__).resolve().parent.parent.parent / "data" / "skills.json"
        self._skills_path = Path(skills_path)
        self._base_names: list[str] = []  # deduplicated base names for matching
        self._name_to_entry: dict[str, dict] = {}  # base_name -> first entry
        self._load()

    @classmethod
    def _strip_circle(cls, name: str) -> str:
        for suffix in cls._CIRCLE_SUFFIXES:
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name

    def _load(self) -> None:
        if not self._skills_path.exists():
            logger.warning("skills.json not found at %s", self._skills_path)
            return
        with open(self._skills_path) as f:
            entries = json.load(f)
        seen: set[str] = set()
        for entry in entries:
            base = self._strip_circle(entry["name"])
            if base not in seen:
                seen.add(base)
                self._base_names.append(base)
                self._name_to_entry[base] = entry
        logger.info("SkillMatcher loaded %d unique skill names", len(self._base_names))

    def match(self, ocr_text: str) -> tuple[str, int] | None:
        """Match OCR text to the closest known skill base name.

        Returns (matched_base_name, score) or None if no good match.
        """
        if not self._base_names or not ocr_text or len(ocr_text) < 3:
            return None

        result = process.extractOne(
            ocr_text,
            self._base_names,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=MIN_MATCH_SCORE,
        )
        if result is None:
            return None

        matched_name, score, _idx = result
        return (matched_name, score)

    def get_entry(self, name: str) -> dict | None:
        """Get the full skill entry by base name."""
        return self._name_to_entry.get(name)

    @property
    def names(self) -> list[str]:
        return self._base_names
