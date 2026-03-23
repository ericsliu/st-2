"""Hot-reloading loader for hand-written Markdown advice files.

Advice files live in data/advice/:
  general.md          — always injected into every LLM prompt
  ura_finale.md       — injected when scenario == "ura_finale"
  unity_cup.md        — scenario-specific
  special_week.md     — character-specific (character_id match)

Files are re-read whenever their mtime changes, so edits take effect on the
very next LLM call with zero restart.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)


class AdviceFile(NamedTuple):
    name: str       # stem without extension, e.g. "ura_finale"
    path: Path
    content: str
    mtime: float


class AdviceLoader:
    """Reads and caches Markdown advice files, invalidating on file change."""

    def __init__(self, advice_dir: str = "data/advice") -> None:
        self.advice_dir = Path(advice_dir)
        self.advice_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, AdviceFile] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_context(self, scenario: str = "", character_id: str = "") -> str:
        """Return combined advice text relevant to the current run context.

        Concatenates: general + scenario-specific + character-specific advice.
        """
        parts: list[str] = []

        general = self._load("general")
        if general:
            parts.append(f"## General advice\n{general}")

        if scenario:
            scenario_advice = self._load(scenario)
            if scenario_advice:
                parts.append(f"## {scenario.replace('_', ' ').title()} advice\n{scenario_advice}")

        if character_id:
            char_advice = self._load(character_id)
            if char_advice:
                parts.append(f"## {character_id.replace('_', ' ').title()} advice\n{char_advice}")

        return "\n\n".join(parts)

    def list_files(self) -> list[dict]:
        """Return metadata for all advice files (for the dashboard)."""
        result = []
        for path in sorted(self.advice_dir.glob("*.md")):
            content = self._load(path.stem) or ""
            result.append({
                "name": path.stem,
                "path": str(path),
                "size": len(content),
                "lines": content.count("\n") + 1 if content else 0,
            })
        return result

    def get_file(self, name: str) -> str:
        """Return the raw content of an advice file."""
        return self._load(name) or ""

    def save_file(self, name: str, content: str) -> None:
        """Write an advice file and invalidate its cache entry."""
        # Sanitize name — only allow alphanumeric + underscores
        safe_name = "".join(c for c in name if c.isalnum() or c == "_")
        if not safe_name:
            raise ValueError(f"Invalid advice file name: {name!r}")

        path = self.advice_dir / f"{safe_name}.md"
        path.write_text(content, encoding="utf-8")
        # Invalidate cache
        self._cache.pop(safe_name, None)
        logger.info("Saved advice file: %s", path)

    def delete_file(self, name: str) -> bool:
        """Delete an advice file. Returns True if it existed."""
        path = self.advice_dir / f"{name}.md"
        if path.exists():
            path.unlink()
            self._cache.pop(name, None)
            logger.info("Deleted advice file: %s", path)
            return True
        return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self, name: str) -> str | None:
        """Load file content, using cache if mtime unchanged."""
        path = self.advice_dir / f"{name}.md"
        if not path.exists():
            return None

        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None

        cached = self._cache.get(name)
        if cached and cached.mtime == mtime:
            return cached.content

        # Re-read — file changed or not cached yet
        try:
            content = path.read_text(encoding="utf-8").strip()
            self._cache[name] = AdviceFile(name=name, path=path, content=content, mtime=mtime)
            if cached:
                logger.info("Reloaded advice file: %s (changed)", path.name)
            return content
        except OSError as e:
            logger.warning("Could not read advice file %s: %s", path, e)
            return None
