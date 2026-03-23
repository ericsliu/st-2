"""Local LLM client via Ollama (Tier 2 decisions)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from uma_trainer.llm.base import LLMClient, LLMResponse
from uma_trainer.llm.cache import LLMCache
from uma_trainer.llm import prompts
from uma_trainer.config import LLMConfig

if TYPE_CHECKING:
    from uma_trainer.knowledge.advice_loader import AdviceLoader
    from uma_trainer.types import EventChoice, GameState, SkillOption

logger = logging.getLogger(__name__)


class OllamaClient(LLMClient):
    """Queries a locally running Ollama server for Tier 2 decisions.

    Model: phi4-mini:q4_K_M (recommended) or llama3.2:3b
    Speed: ~15-20 tok/s on M1 — fast enough for game-speed decisions.
    """

    def __init__(self, config: LLMConfig, cache: LLMCache, advice: "AdviceLoader | None" = None) -> None:
        self.config = config
        self.cache = cache
        self.advice = advice
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import ollama
                self._client = ollama.Client(host=self.config.ollama_host)
            except ImportError:
                logger.error("ollama package not installed: pip install ollama")
                raise
        return self._client

    def is_available(self) -> bool:
        """Check if Ollama server is running."""
        try:
            client = self._get_client()
            client.list()
            return True
        except Exception:
            return False

    def query_event(
        self,
        event_text: str,
        choices: list["EventChoice"],
        state: "GameState",
    ) -> LLMResponse:
        choices_str = "\n".join(
            f"{i}. {c.text}" for i, c in enumerate(choices)
        )
        advice_ctx = ""
        if self.advice:
            advice_ctx = self.advice.get_context(scenario=state.scenario)

        prompt = prompts.EVENT_DECISION_PROMPT.format(
            event=event_text,
            choices=choices_str,
            stats=state.stats.as_dict(),
            turn=state.current_turn,
            max_turns=state.max_turns,
            scenario=state.scenario,
            energy=state.energy,
            mood=state.mood.value,
            advice_context=f"\n\n## Your personal notes\n{advice_ctx}" if advice_ctx else "",
        )

        cache_key = self.cache.make_key(self.config.local_model, "event", event_text, choices_str, advice_ctx)
        cached = self.cache.get(cache_key)
        if cached:
            return self._parse_event_response(cached)

        try:
            client = self._get_client()
            response = client.chat(
                model=self.config.local_model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )
            raw = response.message.content
            self.cache.set(cache_key, raw, model=self.config.local_model)
            return self._parse_event_response(raw)
        except Exception as e:
            logger.warning("Ollama query failed: %s", e)
            return LLMResponse(choice_index=None, reasoning=str(e), confidence=0.0, raw="")

    def query_skill_build(
        self,
        available_skills: list["SkillOption"],
        state: "GameState",
    ) -> list[str]:
        skills_str = "\n".join(
            f"- {s.skill_id}: {s.name} (cost={s.cost}, priority={s.priority})"
            for s in available_skills
        )
        prompt = prompts.SKILL_BUILD_PROMPT.format(
            skills=skills_str,
            stats=state.stats.as_dict(),
            turn=state.current_turn,
            max_turns=state.max_turns,
            scenario=state.scenario,
        )

        cache_key = self.cache.make_key(self.config.local_model, "skills", skills_str)
        cached = self.cache.get(cache_key)
        if cached:
            return self._parse_skill_response(cached)

        try:
            client = self._get_client()
            response = client.chat(
                model=self.config.local_model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )
            raw = response.message.content
            self.cache.set(cache_key, raw, model=self.config.local_model)
            return self._parse_skill_response(raw)
        except Exception as e:
            logger.warning("Ollama skill query failed: %s", e)
            return []

    def _parse_event_response(self, raw: str) -> LLMResponse:
        try:
            data = json.loads(raw)
            return LLMResponse(
                choice_index=int(data.get("choice_index", 0)),
                reasoning=str(data.get("reasoning", "")),
                confidence=float(data.get("confidence", 0.5)),
                raw=raw,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug("Failed to parse LLM event response: %s | raw=%s", e, raw[:100])
            return LLMResponse(choice_index=0, reasoning="parse error", confidence=0.3, raw=raw)

    def _parse_skill_response(self, raw: str) -> list[str]:
        try:
            data = json.loads(raw)
            return [str(s) for s in data.get("buy_ids", [])]
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug("Failed to parse LLM skill response: %s", e)
            return []
