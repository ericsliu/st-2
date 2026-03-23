"""Claude API client (Tier 3 decisions — high-value, cached)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, date, timezone
from typing import TYPE_CHECKING

from uma_trainer.llm.base import LLMBudgetExceededError, LLMClient, LLMResponse
from uma_trainer.llm.cache import LLMCache
from uma_trainer.llm import prompts
from uma_trainer.config import LLMConfig

if TYPE_CHECKING:
    from uma_trainer.knowledge.advice_loader import AdviceLoader
    from uma_trainer.types import EventChoice, GameState, SkillOption

logger = logging.getLogger(__name__)


class ClaudeAPIClient(LLMClient):
    """Queries the Claude API for high-value decisions (Tier 3).

    Features:
    - Daily call budget enforcement
    - SHA256-keyed response caching (1 week TTL)
    - Always requests structured JSON output
    - Expected: 2–5 calls/day, <$0.10/day
    """

    def __init__(self, config: LLMConfig, cache: LLMCache, advice: "AdviceLoader | None" = None) -> None:
        self.config = config
        self.cache = cache
        self.advice = advice
        self._daily_count: int = 0
        self._daily_reset_date: date = date.today()
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                if not self.config.claude_api_key:
                    raise ValueError(
                        "ANTHROPIC_API_KEY not set. Add it to .env or environment."
                    )
                self._client = anthropic.Anthropic(api_key=self.config.claude_api_key)
            except ImportError:
                logger.error("anthropic package not installed: pip install anthropic")
                raise
        return self._client

    def is_available(self) -> bool:
        return bool(self.config.claude_api_key)

    def _check_budget(self) -> None:
        """Enforce the daily call limit. Raises LLMBudgetExceededError if over."""
        today = date.today()
        if today != self._daily_reset_date:
            self._daily_count = 0
            self._daily_reset_date = today

        if self._daily_count >= self.config.claude_daily_limit:
            raise LLMBudgetExceededError(
                f"Daily Claude API limit reached ({self.config.claude_daily_limit} calls). "
                "Falling back to local LLM."
            )

    def query_event(
        self,
        event_text: str,
        choices: list["EventChoice"],
        state: "GameState",
    ) -> LLMResponse:
        choices_str = "\n".join(f"{i}. {c.text}" for i, c in enumerate(choices))
        advice_ctx = ""
        if self.advice:
            advice_ctx = self.advice.get_context(scenario=state.scenario)

        cache_key = self.cache.make_key(
            self.config.claude_model, "event", event_text, choices_str, advice_ctx
        )
        cached = self.cache.get(cache_key)
        if cached:
            return self._parse_event_response(cached)

        self._check_budget()

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

        try:
            client = self._get_client()
            message = client.messages.create(
                model=self.config.claude_model,
                max_tokens=256,
                system=(
                    "You are an Uma Musume expert. Respond ONLY with the requested JSON. "
                    "No markdown, no extra text."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            self._daily_count += 1
            logger.info(
                "Claude API call #%d/%d today",
                self._daily_count,
                self.config.claude_daily_limit,
            )
            self.cache.set(cache_key, raw, model=self.config.claude_model)
            return self._parse_event_response(raw)
        except LLMBudgetExceededError:
            raise
        except Exception as e:
            logger.error("Claude API call failed: %s", e)
            return LLMResponse(choice_index=None, reasoning=str(e), confidence=0.0, raw="")

    def query_skill_build(
        self,
        available_skills: list["SkillOption"],
        state: "GameState",
    ) -> list[str]:
        skills_str = "\n".join(
            f"- {s.skill_id}: {s.name} (cost={s.cost})" for s in available_skills
        )
        cache_key = self.cache.make_key(
            self.config.claude_model, "skills", skills_str
        )
        cached = self.cache.get(cache_key)
        if cached:
            return self._parse_skill_response(cached)

        self._check_budget()

        prompt = prompts.SKILL_BUILD_PROMPT.format(
            skills=skills_str,
            stats=state.stats.as_dict(),
            turn=state.current_turn,
            max_turns=state.max_turns,
            scenario=state.scenario,
        )

        try:
            client = self._get_client()
            message = client.messages.create(
                model=self.config.claude_model,
                max_tokens=256,
                system="You are an Uma Musume expert. Respond ONLY with the requested JSON.",
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            self._daily_count += 1
            self.cache.set(cache_key, raw, model=self.config.claude_model)
            return self._parse_skill_response(raw)
        except LLMBudgetExceededError:
            raise
        except Exception as e:
            logger.error("Claude API skill query failed: %s", e)
            return []

    def analyze_run(self, run_result) -> str:
        """Post-run analysis. Returns a text summary."""
        import dataclasses
        prompt = prompts.RUN_ANALYSIS_PROMPT.format(
            trainee_id=run_result.trainee_id,
            scenario=run_result.scenario,
            final_stats=dataclasses.asdict(run_result.final_stats),
            goals_completed=run_result.goals_completed,
            total_goals=run_result.total_goals,
            turns_taken=run_result.turns_taken,
        )
        try:
            self._check_budget()
            client = self._get_client()
            message = client.messages.create(
                model=self.config.claude_model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            self._daily_count += 1
            return message.content[0].text
        except Exception as e:
            logger.error("Claude run analysis failed: %s", e)
            return ""

    def _parse_event_response(self, raw: str) -> LLMResponse:
        try:
            # Strip markdown code fences if present
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())
            return LLMResponse(
                choice_index=int(data.get("choice_index", 0)),
                reasoning=str(data.get("reasoning", "")),
                confidence=float(data.get("confidence", 0.8)),
                raw=raw,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug("Failed to parse Claude event response: %s | raw=%s", e, raw[:100])
            return LLMResponse(choice_index=0, reasoning="parse error", confidence=0.3, raw=raw)

    def _parse_skill_response(self, raw: str) -> list[str]:
        try:
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())
            return [str(s) for s in data.get("buy_ids", [])]
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug("Failed to parse Claude skill response: %s", e)
            return []
