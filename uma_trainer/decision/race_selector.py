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
        # Playbook race policy override: skip non-G1 races for strong training
        self._race_policy = None  # Set via set_race_policy()
        self._target_race_name: str | None = None  # Set per-turn by playbook
        logger.info("Race calendar loaded: %d races", len(self._calendar))

    def set_race_policy(self, policy) -> None:
        """Set a playbook race policy override.

        When set, non-G1/non-goal races may be skipped on flex turns if
        strong training conditions are met.
        """
        self._race_policy = policy
        logger.info("Race policy override set: g1=%s, g2=%s, g3=%s",
                     policy.g1_policy, policy.g2_policy, policy.g3_policy)

    # ------------------------------------------------------------------
    # Calendar-driven race pre-selection
    # ------------------------------------------------------------------

    @staticmethod
    def turn_to_month_half(turn: int, max_turns: int = 72) -> tuple[int, int, str]:
        """Convert absolute turn to (year, month, half).

        Each year has 24 turns (2 per month).
        Turn numbering is 1-based (turn 1 = Junior Early Jan).
        Returns (year_1based, month_1to12, "early"|"late").
        """
        t = turn - 1  # convert to 0-based
        turns_per_year = max_turns // 3
        year = t // turns_per_year + 1
        year_turn = t % turns_per_year
        month = (year_turn // 2) + 1
        half = "early" if year_turn % 2 == 0 else "late"
        return year, month, half

    def get_races_for_turn(self, turn: int, max_turns: int = 72) -> list[dict]:
        """Return all calendar races available at the given turn.

        Filters by both month/half AND year (Junior/Classic/Senior).
        """
        year, month, half = self.turn_to_month_half(turn, max_turns)
        results = []
        for entry in self._calendar:
            # Year filter: skip races not available in this year
            allowed_years = entry.get("years")
            if allowed_years and year not in allowed_years:
                continue
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

        # Filter out scenario-specific races (URA Finale etc.) — 0m distance
        calendar_races = [r for r in calendar_races if r.get("distance", 0) > 0]
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
        If _target_race_name is set (by playbook), force-select that race
        via fuzzy name matching. Otherwise fall back to scoring.
        """
        if not state.available_races:
            logger.info("Race list empty — going back")
            return BotAction(
                action_type=ActionType.WAIT,
                reason="No races available",
            )

        # Playbook target race: fuzzy-match by name and force-select
        if self._target_race_name:
            target = self._target_race_name.lower()
            self._target_race_name = None  # Clear after use
            best_match = None
            best_ratio = 0.0
            for race in state.available_races:
                # Simple substring + token matching
                race_lower = race.name.lower()
                if target in race_lower or race_lower in target:
                    best_match = race
                    best_ratio = 1.0
                    break
                # Token overlap ratio
                target_tokens = set(target.split())
                race_tokens = set(race_lower.split())
                overlap = len(target_tokens & race_tokens)
                ratio = overlap / max(len(target_tokens), 1)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = race
            if best_match and best_ratio >= 0.4:
                logger.info(
                    "Race: playbook target '%s' matched '%s' (ratio=%.2f)",
                    target, best_match.name, best_ratio,
                )
                if self.scenario:
                    self.scenario.on_race_completed()
                return BotAction(
                    action_type=ActionType.RACE,
                    target=best_match.name,
                    tap_coords=best_match.tap_coords,
                    reason=f"Playbook: {best_match.name} ({best_match.grade})",
                    tier_used=1,
                )
            logger.warning(
                "Race: playbook target '%s' not found in available races: %s",
                target, [r.name for r in state.available_races],
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

    # Absolute hard cap — auto_turn.py handles the soft cap (3+) with item checks
    MAX_CONSECUTIVE_RACES = 5

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

        # --- G1 race available this turn? Always race G1s. ---
        g1 = self._find_best_g1(state)
        if g1:
            logger.info(
                "G1 available: '%s' (%dm, %s, fans=%d)",
                g1["name"], g1.get("distance", 0),
                g1.get("surface", "?"), g1.get("fan_reward", 0),
            )
            return BotAction(
                action_type=ActionType.RACE,
                tap_coords=races_btn,
                reason=f"G1 available: {g1['name']} (fans={g1.get('fan_reward', 0)})",
                tier_used=1,
            )

        # Playbook race policy: skip non-G1 races when strong training is available
        if self._race_policy:
            from uma_trainer.decision.playbook import CONDITION_EVALUATORS
            best_grade = self._best_available_grade(state)
            grade_policy = getattr(self._race_policy, f"{best_grade.lower()}_policy", "default")
            if grade_policy == "skip_for_training":
                for cond_name in (self._race_policy.skip_for or []):
                    evaluator = CONDITION_EVALUATORS.get(cond_name)
                    if evaluator and evaluator(state):
                        logger.info(
                            "Race policy: skipping %s race (condition: %s)",
                            best_grade, cond_name,
                        )
                        return None

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

        # 0a. Never enter dirt races — not worth it even with B aptitude
        if race.surface == "dirt":
            if not race.is_goal_race:
                logger.info("Race '%s' blocked: dirt races banned", race.name)
                return 0.0

        # 0b. After Junior Year, only enter Graded races (G1/G2/G3) or goal races
        if state.current_turn > 24 and race.grade in ("OP", "Pre-OP", "Debut", ""):
            if not race.is_goal_race:
                logger.info(
                    "Race '%s' blocked: grade %s not allowed after Junior Year",
                    race.name, race.grade or "unknown",
                )
                return 0.0

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
        """Map race distance in metres to aptitude category.

        Game categories: Sprint 1000-1400, Mile 1600-1800,
        Medium 2000-2400, Long 2500+.
        """
        if distance_m <= 1400:
            return "short"
        elif distance_m <= 1800:
            return "mile"
        elif distance_m <= 2400:
            return "medium"
        else:
            return "long"

    def _find_best_g1(self, state: GameState) -> dict | None:
        """Return the best G1 race available this turn, or None.

        Filters by aptitude (distance + surface must be B or better).
        Among eligible G1s, picks the one with the highest fan reward.
        """
        calendar_races = self.get_races_for_turn(
            state.current_turn, state.max_turns,
        )
        g1s = [r for r in calendar_races if r.get("grade") == "G1" and r.get("distance", 0) > 0]
        if not g1s:
            return None

        blocked = {"C", "D", "E", "F", "G"}
        apt = state.trainee_aptitudes or {}

        eligible = []
        for race in g1s:
            dist = race.get("distance", 0)
            surface = race.get("surface", "turf")
            if dist > 0 and apt:  # 0m = scenario race (URA Finale etc), skip aptitude check
                dist_cat = self._distance_category(dist)
                if apt.get(dist_cat, "") in blocked:
                    logger.info(
                        "G1 '%s' blocked: %s aptitude %s",
                        race["name"], dist_cat, apt.get(dist_cat, "?"),
                    )
                    continue
                if apt.get(surface, "") in blocked:
                    logger.info(
                        "G1 '%s' blocked: %s surface aptitude %s",
                        race["name"], surface, apt.get(surface, "?"),
                    )
                    continue
            eligible.append(race)

        if not eligible:
            return None

        eligible.sort(key=lambda r: r.get("fan_reward", 0), reverse=True)
        return eligible[0]

    def _find_required_race(self, state: GameState) -> str | None:
        """Return a race name if a career goal race must be entered."""
        for goal in state.career_goals:
            if not goal.completed and goal.race_name:
                return goal.race_name
        return None

    def _best_available_grade(self, state: GameState) -> str:
        """Return the best race grade available this turn (e.g., 'G1', 'G2')."""
        grade_rank = {"G1": 0, "G2": 1, "G3": 2, "OP": 3, "Pre-OP": 4, "Debut": 5}
        races = self.get_races_for_turn(state.current_turn, state.max_turns)
        best = "Pre-OP"
        for r in races:
            grade = r.get("grade", "Pre-OP")
            if grade_rank.get(grade, 99) < grade_rank.get(best, 99):
                best = grade
        return best

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
            if best_match.get("fan_reward") and race.fan_reward == 0:
                race.fan_reward = best_match["fan_reward"]

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
