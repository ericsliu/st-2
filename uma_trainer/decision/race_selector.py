"""Race selection logic for Career Mode.

Handles race scoring on the race list screen and delegates
scenario-specific race-vs-train decisions to the scenario handler.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from uma_trainer.types import ActionType, BotAction, GameState, RaceOption

if TYPE_CHECKING:
    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.knowledge.overrides import OverridesLoader
    from uma_trainer.scenario.base import ScenarioHandler

logger = logging.getLogger(__name__)


class RaceSelector:
    """Decides whether to enter a race and which race to choose."""

    def __init__(
        self,
        kb: "KnowledgeBase",
        overrides: "OverridesLoader | None" = None,
        scenario: "ScenarioHandler | None" = None,
    ) -> None:
        self.kb = kb
        self.overrides = overrides
        self.scenario = scenario

    # ------------------------------------------------------------------
    # Main entry point: race list screen
    # ------------------------------------------------------------------

    def decide(self, state: GameState) -> BotAction:
        """Pick the best race from the available race list.

        Called when the game is on the RACE_ENTRY screen.
        """
        if not state.available_races:
            logger.info("Race list empty — going back")
            return BotAction(
                action_type=ActionType.WAIT,
                reason="No races available",
            )

        scored = self.score_races(state)
        if not scored:
            return BotAction(
                action_type=ActionType.WAIT,
                reason="No races scored",
            )

        best_race, best_score = scored[0]

        min_score = self.scenario.get_race_min_score() if self.scenario else 5.0
        if best_score < min_score:
            logger.info("Race: skipping, best score %.1f < %.1f", best_score, min_score)
            return BotAction(
                action_type=ActionType.WAIT,
                reason=f"No worthwhile race (best={best_score:.1f})",
            )

        logger.info(
            "Race: selecting '%s' (grade=%s, dist=%dm, score=%.1f)",
            best_race.name, best_race.grade, best_race.distance, best_score,
        )
        if self.scenario:
            self.scenario.on_race_completed()

        # The race list UI requires:
        # 1. Tap a race entry to select it (green brackets highlight)
        # 2. Tap the green "Race" button at the bottom to confirm
        # For now, the first race is pre-selected by default, so we just
        # tap the "Race" button. If we need a non-first race, we'd tap
        # the entry first, then the button.
        from uma_trainer.perception.regions import RACE_LIST_REGIONS, get_tap_center
        race_btn = RACE_LIST_REGIONS.get("btn_race")
        tap = get_tap_center(race_btn) if race_btn else best_race.tap_coords

        return BotAction(
            action_type=ActionType.RACE,
            target=best_race.name,
            tap_coords=tap,
            reason=f"{best_race.name} ({best_race.grade}, score={best_score:.1f})",
            tier_used=1,
        )

    # ------------------------------------------------------------------
    # Turn action screen: should we race this turn?
    # ------------------------------------------------------------------

    def should_race_this_turn(self, state: GameState) -> BotAction | None:
        """Called from the turn action screen to decide if the bot should
        tap the Races button instead of training.

        Returns a BotAction to tap the Races button, or None if training
        is preferred.
        """
        from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
        races_btn = get_tap_center(TURN_ACTION_REGIONS["btn_races"])

        # --- Always check for mandatory goal races ---
        goal_race = self._find_required_race(state)
        if goal_race:
            logger.info("Race: goal race '%s' is due", goal_race)
            return BotAction(
                action_type=ActionType.RACE,
                tap_coords=races_btn,
                reason=f"Goal race due: {goal_race}",
                tier_used=1,
            )

        # Delegate scenario-specific logic
        if self.scenario:
            return self.scenario.should_race_this_turn(state, races_btn)

        # Fallback: only race for fan boosts
        if self._needs_fan_boost(state):
            return BotAction(
                action_type=ActionType.RACE,
                tap_coords=races_btn,
                reason="Need fans for next goal",
                tier_used=1,
            )

        return None

    def on_non_race_action(self) -> None:
        """Call when the bot takes a non-race action to reset counters."""
        if self.scenario:
            self.scenario.on_non_race_action()

    # ------------------------------------------------------------------
    # Scoring races on the race list screen
    # ------------------------------------------------------------------

    def score_races(
        self, state: GameState,
    ) -> list[tuple[RaceOption, float]]:
        """Score all available races and return sorted best-first."""
        scored = [(race, self._score_race(race, state)) for race in state.available_races]
        return sorted(scored, key=lambda x: x[1], reverse=True)

    def _score_race(self, race: RaceOption, state: GameState) -> float:
        """Score a single race option."""
        score = 0.0
        strategy = self._get_race_strategy()

        # 1. Goal race — highest priority
        if race.is_goal_race:
            score += 200.0

        # 2. Grade value — G1 >> G2 >> G3
        grade_value = (
            self.scenario.get_grade_value(race.grade)
            if self.scenario else 1.0
        )
        score += grade_value * 3.0

        # 3. Grade Points value (how many points would winning give us?)
        gp_list = (
            self.scenario.get_grade_points(race.grade)
            if self.scenario else []
        )
        gp = gp_list[0] if gp_list else 0
        if gp > 0 and self.scenario:
            year = self.scenario.current_year(state.current_turn)
            surface = strategy.get("preferred_surface", "turf")
            target = self.scenario.get_grade_point_target(year, surface)
            if target > 0:
                # TODO: track actual GP, not estimate
                deficit = max(0, target // 3)
                urgency = min(deficit / 100.0, 3.0)
                score += gp * 0.3 * urgency

        # 4. Fan reward
        if race.fan_reward > 0:
            score += min(race.fan_reward / 2000.0, 8.0)

        # 5. Aptitude gating — the most important filter.
        # Two sources of truth:
        #   a) is_aptitude_ok: from race list screen color (yellow = B+, white = C-)
        #   b) trainee_aptitudes: from stats page OCR (S/A/B/C/D/E/F/G)
        #
        # If is_aptitude_ok is False, the game itself is telling us the trainee
        # can't compete — hard block, no exceptions except goal races.

        if not race.is_aptitude_ok:
            if not race.is_goal_race:
                logger.info(
                    "Race '%s' blocked: not highlighted (C or worse aptitude)",
                    race.name,
                )
                return 0.0
            score -= 100.0  # Goal race but bad aptitude

        # If we have detailed aptitudes from the stats page, use them for
        # finer scoring (prefer A/S over B).
        aptitudes = state.trainee_aptitudes
        if aptitudes and race.distance > 0:
            dist_cat = self._distance_category(race.distance)
            dist_apt = aptitudes.get(dist_cat, "")
            surf_apt = aptitudes.get(race.surface, "")

            blocked_grades = {"C", "D", "E", "F", "G"}

            if dist_apt in blocked_grades:
                if not race.is_goal_race:
                    logger.info(
                        "Race '%s' blocked: %s aptitude %s (distance %dm)",
                        race.name, dist_cat, dist_apt, race.distance,
                    )
                    return 0.0
                score -= 100.0
            elif dist_apt == "B":
                score -= 5.0
            elif dist_apt in ("A", "S"):
                score += 10.0

            if surf_apt in blocked_grades:
                if not race.is_goal_race:
                    logger.info(
                        "Race '%s' blocked: %s surface aptitude %s",
                        race.name, race.surface, surf_apt,
                    )
                    return 0.0
                score -= 100.0
            elif surf_apt == "B":
                score -= 3.0
            elif surf_apt in ("A", "S"):
                score += 5.0

        return max(0.0, score)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _distance_category(distance_m: int) -> str:
        """Map race distance in metres to aptitude category."""
        if distance_m <= 1200:
            return "short"
        elif distance_m <= 1800:
            return "mile"
        elif distance_m <= 2400:
            return "medium"
        else:
            return "long"

    def _find_required_race(self, state: GameState) -> str | None:
        """Return a race name if a career goal race must be entered."""
        for goal in state.career_goals:
            if not goal.completed and goal.race_name:
                return goal.race_name
        return None

    def _needs_fan_boost(self, state: GameState) -> bool:
        """True if fan count is too low for the next goal."""
        for goal in state.career_goals:
            if not goal.completed and goal.required_fans > 0:
                if state.fan_count < goal.required_fans * 0.7:
                    return True
        return False

    def _get_race_strategy(self) -> dict:
        """Get race-related strategy settings from overrides."""
        if not self.overrides:
            return {}
        strategy = self.overrides.get_strategy()
        return strategy.raw.get("race_strategy", {})

    def lookup_race_info(self, race_name: str) -> dict | None:
        """Look up race metadata from the knowledge base."""
        row = self.kb.query_one(
            "SELECT * FROM race_calendar WHERE name = ?",
            (race_name,),
        )
        if row:
            return dict(row)

        # Fuzzy match
        try:
            from rapidfuzz import fuzz
            rows = self.kb.query_all("SELECT * FROM race_calendar")
            best_match = None
            best_ratio = 0
            for r in rows:
                ratio = fuzz.ratio(race_name.lower(), r["name"].lower())
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = r
            if best_match and best_ratio >= 70:
                return dict(best_match)
        except ImportError:
            pass

        return None
