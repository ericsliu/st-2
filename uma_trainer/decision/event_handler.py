"""Event screen decision logic: 5-tier lookup chain.

Tier 0: Hand-written overrides (data/overrides/events.yaml) — highest priority
Tier 1: Exact hash lookup in knowledge base
Tier 2: Fuzzy match (>85% similarity) in knowledge base
Tier 3: Local LLM (Ollama)
Tier 4: Claude API (high-value fallback)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from uma_trainer.types import ActionType, BotAction, EventChoice, GameState

if TYPE_CHECKING:
    from uma_trainer.knowledge.overrides import OverridesLoader

logger = logging.getLogger(__name__)


class EventHandler:
    """Decides which event choice to select using a 5-tier fallback chain."""

    def __init__(self, kb, local_llm, claude_client, overrides: "OverridesLoader | None" = None) -> None:
        self.kb = kb
        self.local_llm = local_llm
        self.claude_client = claude_client
        self.overrides = overrides

    def decide(self, state: GameState) -> BotAction:
        """Select the best event choice for the current event screen."""
        event_text = state.event_text.strip()
        choices = state.event_choices

        if not choices:
            logger.warning("No event choices found — defaulting to first choice")
            return self._make_choice_action(0, choices, tier=0, reason="no choices detected")

        if not event_text:
            logger.warning("No event text found — defaulting to first choice")
            return self._make_choice_action(0, choices, tier=0, reason="no event text")

        # Tier 0: Hand-written overrides (highest priority)
        result = self._try_override(event_text, choices, state)
        if result is not None:
            return result

        # Tier 1: Exact hash match
        result = self._try_exact_match(event_text, choices)
        if result is not None:
            return result

        # Tier 2: Fuzzy match
        result = self._try_fuzzy_match(event_text, choices)
        if result is not None:
            return result

        # Tier 3: Local LLM
        result = self._try_local_llm(event_text, choices, state)
        if result is not None:
            return result

        # Tier 4: Claude API
        result = self._try_claude(event_text, choices, state)
        if result is not None:
            return result

        # Final fallback
        logger.warning("All tiers failed — defaulting to first choice")
        return self._make_choice_action(0, choices, tier=1, reason="all tiers failed")

    def _try_override(
        self, event_text: str, choices: list[EventChoice], state: GameState
    ) -> BotAction | None:
        if self.overrides is None:
            return None
        try:
            match = self.overrides.match_event(event_text, energy=state.energy, turn=state.current_turn)
            if match is not None:
                idx = min(match.choice, len(choices) - 1)
                note = f" ({match.note})" if match.note else ""
                logger.info("Event: Tier 0 override → choice %d%s", idx, note)
                return self._make_choice_action(
                    idx, choices, tier=0, reason=f"override: {match.text_contains}{note}"
                )
        except Exception as e:
            logger.debug("Override lookup error: %s", e)
        return None

    def _try_exact_match(
        self, event_text: str, choices: list[EventChoice]
    ) -> BotAction | None:
        try:
            record = self.kb.event_lookup.find_exact(event_text)
            if record is not None:
                idx = min(record.best_choice_index, len(choices) - 1)
                logger.info("Event: exact match (idx=%d)", idx)
                return self._make_choice_action(
                    idx, choices, tier=1, reason="exact KB match"
                )
        except Exception as e:
            logger.debug("Exact match lookup error: %s", e)
        return None

    def _try_fuzzy_match(
        self, event_text: str, choices: list[EventChoice]
    ) -> BotAction | None:
        try:
            record = self.kb.event_lookup.find_fuzzy(event_text, threshold=85)
            if record is not None:
                idx = min(record.best_choice_index, len(choices) - 1)
                logger.info("Event: fuzzy match score=%.0f (idx=%d)", record.score, idx)
                return self._make_choice_action(
                    idx, choices, tier=1, reason=f"fuzzy KB match score={record.score:.0f}"
                )
        except Exception as e:
            logger.debug("Fuzzy match lookup error: %s", e)
        return None

    def _try_local_llm(
        self, event_text: str, choices: list[EventChoice], state: GameState
    ) -> BotAction | None:
        try:
            if self.local_llm is None:
                return None
            response = self.local_llm.query_event(event_text, choices, state)
            if response.confidence > 0.7 and response.choice_index is not None:
                idx = min(response.choice_index, len(choices) - 1)
                logger.info(
                    "Event: local LLM choice=%d conf=%.2f", idx, response.confidence
                )
                # Cache in KB for future runs
                self._save_to_kb(event_text, idx, response.reasoning, confidence=0.7)
                return self._make_choice_action(
                    idx, choices, tier=2, reason=f"local LLM: {response.reasoning[:60]}"
                )
        except Exception as e:
            logger.debug("Local LLM query failed: %s", e)
        return None

    def _try_claude(
        self, event_text: str, choices: list[EventChoice], state: GameState
    ) -> BotAction | None:
        try:
            if self.claude_client is None:
                return None
            response = self.claude_client.query_event(event_text, choices, state)
            if response.choice_index is not None:
                idx = min(response.choice_index, len(choices) - 1)
                logger.info(
                    "Event: Claude API choice=%d conf=%.2f", idx, response.confidence
                )
                # Cache high-confidence Claude responses in KB
                if response.confidence > 0.8:
                    self._save_to_kb(
                        event_text, idx, response.reasoning, confidence=response.confidence
                    )
                return self._make_choice_action(
                    idx, choices, tier=3, reason=f"Claude API: {response.reasoning[:60]}"
                )
        except Exception as e:
            logger.debug("Claude API query failed: %s", e)
        return None

    def _save_to_kb(
        self, event_text: str, choice_idx: int, reasoning: str, confidence: float
    ) -> None:
        """Save a discovered event choice to the knowledge base."""
        try:
            self.kb.event_lookup.insert(
                event_text=event_text,
                choice_index=choice_idx,
                effects=[reasoning],
                confidence=confidence,
            )
        except Exception as e:
            logger.debug("Failed to save event to KB: %s", e)

    def _make_choice_action(
        self,
        choice_index: int,
        choices: list[EventChoice],
        tier: int,
        reason: str,
    ) -> BotAction:
        tap_coords = (640, 400)  # Default center
        if choice_index < len(choices):
            tap_coords = choices[choice_index].tap_coords

        return BotAction(
            action_type=ActionType.CHOOSE_EVENT,
            target=str(choice_index),
            tap_coords=tap_coords,
            reason=reason,
            tier_used=tier,
        )
