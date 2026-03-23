"""Abstract base classes for LLM clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uma_trainer.types import EventChoice, GameState, SkillOption


@dataclass
class LLMResponse:
    choice_index: int | None  # Event choice (0-based), or None if not applicable
    reasoning: str
    confidence: float  # 0.0–1.0
    raw: str  # Raw model output (for debugging)


class LLMClient(ABC):
    """Abstract interface for LLM backends."""

    @abstractmethod
    def query_event(
        self,
        event_text: str,
        choices: list["EventChoice"],
        state: "GameState",
    ) -> LLMResponse:
        """Decide which event choice to select."""

    @abstractmethod
    def query_skill_build(
        self,
        available_skills: list["SkillOption"],
        state: "GameState",
    ) -> list[str]:
        """Return list of skill IDs to buy (ordered by priority)."""

    def is_available(self) -> bool:
        """Return True if this LLM backend is reachable."""
        return True


class LLMBudgetExceededError(RuntimeError):
    """Raised when the daily Claude API call budget is exhausted."""
