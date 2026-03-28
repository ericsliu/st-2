"""Race selection logic for Career Mode.

Handles race scoring on the race list screen and delegates
scenario-specific race-vs-train decisions to the scenario handler.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from uma_trainer.types import ActionType, BotAction, GameState, RaceOption

if TYPE_CHECKING:
    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.knowledge.overrides import OverridesLoader
    from uma_trainer.scenario.base import ScenarioHandler

logger = logging.getLogger(__name__)


def _load_race_calendar(path: str = "data/race_calendar.json") -> list[dict]:
    """Load race calendar from JSON file."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception as e:
        logger.warning("Failed to load race calendar: %s", e)
        return []


GRADE_SORT_ORDER = {"G1": 0, "G2": 1, "G3": 2, "OP": 3, "Pre-OP": 4}


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
        self._calendar = _load_race_calendar()
        self._pre_selected: RaceOption | None = None
        logger.info("Race calendar loaded: %d races", len(self._calendar))

    # ------------------------------------------------------------------
    # Calendar-driven race pre-selection
    # ------------------------------------------------------------------

    @staticmethod
    def turn_to_month_half(turn: int, max_turns: int = 72) -> tuple[int, int, str]:
        """Convert absolute turn to (year, month, half).

        Each year has 24 turns (2 per month).
        Returns (year_1based, month_1to12, "early"|"late").
        """
        turns_per_year = max_turns // 3
        year = turn // turns_per_year + 1
        year_turn = turn % turns_per_year
        month = (year_turn // 2) + 1
        half = "early" if year_turn % 2 == 0 else "late"
        return year, month, half

    def get_races_for_turn(self, turn: int, max_turns: int = 72) -> list[dict]:
        """Return all calendar races available at the given turn."""
        _, month, half = self.turn_to_month_half(turn, max_turns)
        results = []
        for entry in self._calendar:
            grade = entry.get("grade", "")
            if grade in ("OP", "Pre-OP"):
                results.append(entry)
                continue
            if entry.get("month") == month and entry.get("half") == half:
                results.append(entry)
        return results

    def pre_select_race(self, state: GameState) -> RaceOption | None:
        """Pick the best race from the calendar for this turn.

        Called BEFORE opening the race list. Uses calendar data +
        trainee aptitudes to score all candidates without OCR.
        Stores result in self._pre_selected for later navigation.
        """
        calendar_races = self.get_races_for_turn(
            state.current_turn, state.max_turns,
        )
        if not calendar_races:
            self._pre_selected = None
            return None

        candidates = []
        for entry in calendar_races:
            race = RaceOption(
                name=entry["name"],
                grade=entry.get("grade", ""),
                distance=entry.get("distance", 0),
                surface=entry.get("surface", "turf"),
                fan_reward=entry.get("fan_reward", 0),
            )
            for goal in state.career_goals:
                if goal.race_name and not goal.completed:
                    if goal.race_name.lower() in race.name.lower():
                        race.is_goal_race = True
            candidates.append(race)

        # Aptitude gating
        if state.trainee_aptitudes:
            blocked = {"C", "D", "E", "F", "G"}
            for race in candidates:
                if race.distance > 0:
                    dist_cat = self._distance_category(race.distance)
                    if state.trainee_aptitudes.get(dist_cat, "") in blocked:
                        race.is_aptitude_ok = False
                if state.trainee_aptitudes.get(race.surface, "") in blocked:
                    race.is_aptitude_ok = False

        scored = [(r, self._score_race(r, state)) for r in candidates]
        scored = [(r, s) for r, s in scored if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)

        if not scored:
            self._pre_selected = None
            return None

        min_score = self.scenario.get_race_min_score() if self.scenario else 5.0
        best_race, best_score = scored[0]
        if best_score < min_score:
            self._pre_selected = None
            return None

        logger.info(
            "Pre-selected race: '%s' (grade=%s, dist=%dm, score=%.1f)",
            best_race.name, best_race.grade, best_race.distance, best_score,
        )
        self._pre_selected = best_race
        return best_race

    def estimate_race_position(self, target: RaceOption, turn: int, max_turns: int = 72) -> int:
        """Estimate the 0-based position of a race in the in-game sorted list.

        The game sorts G1 first, then G2, G3, OP, Pre-OP.
        """
        all_races = self.get_races_for_turn(turn, max_turns)
        all_races.sort(key=lambda r: (
            GRADE_SORT_ORDER.get(r.get("grade", ""), 99),
            r.get("name", ""),
        ))
        for i, entry in enumerate(all_races):
            if entry["name"].lower() == target.name.lower():
                return i
        return GRADE_SORT_ORDER.get(target.grade, 0) * 3

    # ------------------------------------------------------------------
    # Main entry point: race list screen (legacy OCR-based)
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

        # tap_coords = the race ROW to select it (not the Race button)
        return BotAction(
            action_type=ActionType.RACE,
            target=best_race.name,
            tap_coords=best_race.tap_coords,
            reason=f"{best_race.name} ({best_race.grade}, score={best_score:.1f})",
            tier_used=1,
        )

    # ------------------------------------------------------------------
    # Turn action screen: should we race this turn?
    # ------------------------------------------------------------------

    # Hard cap: never exceed this many consecutive races regardless of scenario
    MAX_CONSECUTIVE_RACES = 3

    def should_race_this_turn(self, state: GameState) -> BotAction | None:
        """Called from the turn action screen to decide if the bot should
        tap the Races button instead of training.

        Returns a BotAction to tap the Races button, or None if training
        is preferred.
        """
        from uma_trainer.perception.regions import TURN_ACTION_REGIONS, get_tap_center
        races_btn = get_tap_center(TURN_ACTION_REGIONS["btn_races"])

        # Hard cap on consecutive races — negative effects after 3 in a row
        if self.scenario and hasattr(self.scenario, "_consecutive_races"):
            if self.scenario._consecutive_races >= self.MAX_CONSECUTIVE_RACES:
                logger.warning(
                    "Hard cap: %d consecutive races — forcing break",
                    self.scenario._consecutive_races,
                )
                return None

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
        # Enrich races with calendar data before scoring
        for race in state.available_races:
            self.enrich_race(race)
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

        # 4. Rival race bonus (Trackblazer: rival races give extra GP)
        if race.is_rival_race:
            score += 15.0

        # 5. Fan reward
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

    def enrich_race(self, race: RaceOption) -> None:
        """Fill in grade/distance/surface from the race calendar if OCR missed them.

        Matches by searching for the race's proper name within the OCR'd text,
        or by venue+distance+surface combo. Only overrides OCR values when
        we have a high-confidence match (proper name or 3-field combo).
        """
        if not self._calendar:
            return

        ocr_lower = race.name.lower()

        # Try matching by proper race name within the OCR text
        best_match = None
        for entry in self._calendar:
            entry_name = entry["name"].lower()
            # Direct substring match (e.g., "hopeful stakes" in OCR text)
            if entry_name in ocr_lower:
                best_match = entry
                break

        # Try venue + distance + surface triple match.
        # If multiple races share the same triple, prefer one whose grade
        # matches the OCR'd grade (avoid overriding a correctly-parsed grade).
        if not best_match:
            candidates = []
            for entry in self._calendar:
                venue = entry.get("venue", "").lower()
                surface = entry.get("surface", "").lower()
                dist_str = str(entry.get("distance", 0))
                if (venue and venue in ocr_lower
                        and dist_str in ocr_lower
                        and surface in ocr_lower):
                    candidates.append(entry)
            if candidates:
                # Only use the match if grade matches OCR (or OCR has no grade)
                if race.grade:
                    grade_matches = [c for c in candidates if c["grade"] == race.grade]
                    if grade_matches:
                        best_match = grade_matches[0]
                    # If no grade match, don't enrich — OCR grade is probably right
                else:
                    best_match = candidates[0]

        if best_match:
            logger.debug(
                "Race enriched: '%s' → %s (%s, %dm, %s)",
                race.name, best_match["name"], best_match["grade"],
                best_match["distance"], best_match["surface"],
            )
            if best_match.get("grade"):
                race.grade = best_match["grade"]
            if best_match.get("distance"):
                race.distance = best_match["distance"]
            if best_match.get("surface"):
                race.surface = best_match["surface"]

    def lookup_race_info(self, race_name: str) -> dict | None:
        """Look up race metadata from the race calendar JSON."""
        name_lower = race_name.lower()
        for entry in self._calendar:
            if entry["name"].lower() in name_lower:
                return entry

        # Fuzzy match
        try:
            from rapidfuzz import fuzz
            best_match = None
            best_ratio = 0
            for entry in self._calendar:
                ratio = fuzz.partial_ratio(entry["name"].lower(), name_lower)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = entry
            if best_match and best_ratio >= 65:
                return best_match
        except ImportError:
            pass

        return None
