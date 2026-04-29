"""Skill shop purchase decision logic."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from uma_trainer.types import ActionType, BotAction, GameState, SkillOption

if TYPE_CHECKING:
    from uma_trainer.knowledge.overrides import OverridesLoader
    from uma_trainer.knowledge.skill_catalog import BuyableSkill

logger = logging.getLogger(__name__)


# Rarity codes in master.mdb skill_data:
#   1 = white (common)
#   2 = gold (rare)
#   3 = unique (chara-specific) — never auto-buy in parent runs
#   4 = inherited (rare-gold variant) — also avoid
_UNIQUE_RARITIES = {3, 4}


class SkillBuyer:
    """Decides which skills to buy in the skill shop screen.

    Uses the strategy override system for:
    - skill_priority_list: skills the AI will actively try to acquire
    - skill_blacklist: skills to never buy
    - allow_double_circle: whether to upgrade skills (default False for parent runs)
    """

    # Budget: how many pts we're willing to spend this shop visit
    BUDGET_FRACTION = 0.6  # Spend up to 60% of remaining pts

    def __init__(self, kb, scorer, overrides: "OverridesLoader | None" = None) -> None:
        self.kb = kb
        self.scorer = scorer
        self.overrides = overrides

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

        # Apply strategy overrides to adjust priorities
        enriched = self._apply_strategy(enriched)

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

    def decide_from_packet(
        self,
        state: GameState,
        *,
        reserve: int = 200,
    ) -> list["BuyableSkill"]:
        """Plan a buy list from packet-driven ``state.buyable_skills``.

        Independent of :meth:`decide` (which works on OCR-derived
        ``state.available_skills``). Used by the packet-fast-path in
        ``handle_skill_shop`` once ``SessionTailer.is_fresh()``.

        Selection rules:
          * Look up each skill's priority via the same strategy overrides used
            by :meth:`_apply_strategy` (fuzzy name match). Default 0 if not on
            the priority list. 0 if blacklisted.
          * Skip uniques/inherited (rarity in :data:`_UNIQUE_RARITIES`).
          * Skip skills with priority 0 (no positive signal — neither
            blacklisted in a useful way nor whitelisted).
          * Sort by ``(priority desc, effective_cost asc)``.
          * Greedy fill until ``accumulated_cost + reserve > skill_pts``.

        Returns the chosen skills in priority order (highest priority first).
        """
        buyable = list(getattr(state, "buyable_skills", None) or [])
        if not buyable:
            return []

        budget = int(getattr(state, "skill_pts", 0) or 0)
        if budget <= reserve:
            return []

        strategy = self._get_strategy()

        scored: list[tuple[int, "BuyableSkill"]] = []
        for bs in buyable:
            if int(getattr(bs, "rarity", 0)) in _UNIQUE_RARITIES:
                continue

            name = getattr(bs, "name", "") or ""
            priority = 0
            if strategy is not None:
                if strategy.is_blacklisted(name):
                    continue
                sp = strategy.is_priority_skill(name)
                if sp is not None:
                    priority = int(sp.priority)

            if priority <= 0:
                continue

            scored.append((priority, bs))

        # Sort: priority desc, then effective_cost asc (cheapest first within tier).
        scored.sort(key=lambda pair: (-pair[0], pair[1].effective_cost))

        chosen: list["BuyableSkill"] = []
        spent = 0
        for _prio, bs in scored:
            cost = int(bs.effective_cost)
            if spent + cost + reserve > budget:
                break
            chosen.append(bs)
            spent += cost

        return chosen

    def _evaluate_skill(self, skill: SkillOption, state: GameState) -> BotAction:
        """Decide whether to buy a specific skill."""
        strategy = self._get_strategy()

        # Always skip blacklisted skills
        if strategy is not None and strategy.is_blacklisted(skill.name):
            return BotAction(
                action_type=ActionType.SKIP_SKILL,
                target=skill.skill_id,
                reason=f"Blacklisted: {skill.name}",
            )

        # Check if this is a double-circle (upgrade) purchase
        if skill.is_hint_skill and self._is_already_owned(skill):
            if strategy is not None and not strategy.should_double_circle(skill.name):
                return BotAction(
                    action_type=ActionType.SKIP_SKILL,
                    target=skill.skill_id,
                    reason=f"Skip double-circle: {skill.name} (not allowed)",
                )

        # Priority list skills get a boost and are always bought if affordable
        if strategy is not None:
            sp = strategy.is_priority_skill(skill.name)
            if sp is not None:
                return BotAction(
                    action_type=ActionType.BUY_SKILL,
                    target=skill.skill_id,
                    tap_coords=skill.tap_coords,
                    reason=f"Priority skill: {skill.name} (cost={skill.cost})",
                    tier_used=1,
                )

        # Skip very low priority skills
        if skill.priority <= 3:
            return BotAction(
                action_type=ActionType.SKIP_SKILL,
                target=skill.skill_id,
                reason=f"Low priority ({skill.priority})",
            )

        # Skip if too expensive relative to priority
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

    def _apply_strategy(self, skills: list[SkillOption]) -> list[SkillOption]:
        """Boost priority for skills on the priority list."""
        strategy = self._get_strategy()
        if strategy is None:
            return skills

        for skill in skills:
            sp = strategy.is_priority_skill(skill.name)
            if sp is not None:
                # Boost priority to ensure it's bought
                skill.priority = max(skill.priority, 9)

        return skills

    def _is_already_owned(self, skill: SkillOption) -> bool:
        """Check if a skill has already been purchased (single-circled).

        This is a heuristic — the skill shop typically shows already-owned
        skills with a visual indicator.  For now we rely on the is_hint_skill
        flag and future screen parsing to detect this.
        """
        # TODO: Track purchased skills during the run to detect upgrades
        return False

    def _get_strategy(self):
        if self.overrides is None:
            return None
        return self.overrides.get_strategy()

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
