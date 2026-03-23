"""Skill shop purchase decision logic."""

from __future__ import annotations

import logging

from uma_trainer.types import ActionType, BotAction, GameState, SkillOption

logger = logging.getLogger(__name__)

# Skills to always skip (low-value or detrimental)
SKIP_SKILL_KEYWORDS = frozenset(["Recovery", "Healing"])


class SkillBuyer:
    """Decides which skills to buy in the skill shop screen."""

    # Budget: how many pts we're willing to spend this shop visit
    BUDGET_FRACTION = 0.6  # Spend up to 60% of remaining pts

    def __init__(self, kb, scorer) -> None:
        self.kb = kb
        self.scorer = scorer

    def decide(self, state: GameState) -> list[BotAction]:
        """Return a list of buy/skip actions for the current skill shop."""
        actions: list[BotAction] = []
        available = state.available_skills

        if not available:
            return [BotAction(
                action_type=ActionType.SKIP_SKILL,
                reason="No skills detected",
            )]

        # Enrich skills with KB priority data
        enriched = self._enrich_with_kb(available)

        # Sort by priority descending
        prioritized = sorted(enriched, key=lambda s: s.priority, reverse=True)

        for skill in prioritized:
            action = self._evaluate_skill(skill, state)
            actions.append(action)

        # Always finish with a "done" action (tap the skip/done button)
        actions.append(BotAction(
            action_type=ActionType.SKIP_SKILL,
            reason="Finished shopping",
        ))
        return actions

    def _evaluate_skill(self, skill: SkillOption, state: GameState) -> BotAction:
        """Decide whether to buy a specific skill."""
        # Always skip very low priority skills
        if skill.priority <= 3:
            return BotAction(
                action_type=ActionType.SKIP_SKILL,
                target=skill.skill_id,
                reason=f"Low priority ({skill.priority})",
            )

        # Skip if any skip keywords match the name
        name_lower = skill.name.lower()
        if any(kw.lower() in name_lower for kw in SKIP_SKILL_KEYWORDS):
            return BotAction(
                action_type=ActionType.SKIP_SKILL,
                target=skill.skill_id,
                reason="Blacklisted skill type",
            )

        # Skip hint skills that would cost too much (cost > priority * 30)
        if skill.cost > skill.priority * 30:
            return BotAction(
                action_type=ActionType.SKIP_SKILL,
                target=skill.skill_id,
                reason=f"Too expensive ({skill.cost} pts, priority={skill.priority})",
            )

        return BotAction(
            action_type=ActionType.BUY_SKILL,
            target=skill.skill_id,
            tap_coords=skill.tap_coords,
            reason=f"Priority={skill.priority}, cost={skill.cost}",
            tier_used=1,
        )

    def _enrich_with_kb(self, skills: list[SkillOption]) -> list[SkillOption]:
        """Look up KB priority data for each skill."""
        result = []
        for skill in skills:
            try:
                kb_entry = self.kb.skill_lookup.find_by_name(skill.name)
                if kb_entry is not None:
                    skill.skill_id = kb_entry.skill_id
                    skill.priority = kb_entry.priority
            except Exception:
                pass
            result.append(skill)
        return result
