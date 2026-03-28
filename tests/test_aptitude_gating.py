"""Tests for aptitude-based race gating in RaceSelector."""

from unittest.mock import MagicMock

from uma_trainer.decision.race_selector import RaceSelector
from uma_trainer.types import GameState, RaceOption


def _make_selector():
    """Create a RaceSelector with mocked dependencies."""
    kb = MagicMock()
    overrides = MagicMock()
    strategy = {
        "preferred_distances": [1200, 1600],
        "preferred_surface": "turf",
    }
    overrides.get_strategy.return_value = MagicMock(
        raw={"race_strategy": strategy}
    )
    return RaceSelector(kb=kb, overrides=overrides)


CURREN_APTITUDES = {
    "short": "A",
    "mile": "B",
    "medium": "F",
    "long": "F",
    "turf": "A",
    "dirt": "G",
}


def _state(aptitudes=None):
    return GameState(trainee_aptitudes=aptitudes or {})


def test_distance_category_mapping():
    assert RaceSelector._distance_category(1000) == "short"
    assert RaceSelector._distance_category(1200) == "short"
    assert RaceSelector._distance_category(1400) == "mile"
    assert RaceSelector._distance_category(1600) == "mile"
    assert RaceSelector._distance_category(1800) == "mile"
    assert RaceSelector._distance_category(2000) == "medium"
    assert RaceSelector._distance_category(2400) == "medium"
    assert RaceSelector._distance_category(2500) == "long"
    assert RaceSelector._distance_category(3000) == "long"


# --- is_aptitude_ok (yellow highlight) gating ---

def test_white_text_race_blocked():
    """Race with white text (is_aptitude_ok=False) should be hard-blocked."""
    sel = _make_selector()
    race = RaceOption(
        name="Bad Race", grade="G1", distance=2000,
        surface="turf", is_aptitude_ok=False,
    )
    score = sel._score_race(race, _state())
    assert score == 0.0, f"White-text race should be blocked, got {score}"


def test_white_text_goal_race_still_scored():
    """Goal race with white text gets heavy penalty but is not blocked."""
    sel = _make_selector()
    race = RaceOption(
        name="Goal Race", grade="G1", distance=2000,
        surface="turf", is_aptitude_ok=False, is_goal_race=True,
    )
    score = sel._score_race(race, _state())
    assert score > 0, f"Goal race should not be hard-blocked, got {score}"


def test_yellow_text_race_allowed():
    """Race with yellow text (is_aptitude_ok=True) should be allowed."""
    sel = _make_selector()
    race = RaceOption(
        name="Good Race", grade="G2", distance=1200,
        surface="turf", is_aptitude_ok=True,
    )
    score = sel._score_race(race, _state())
    assert score > 0, f"Yellow-text race should be allowed, got {score}"


# --- trainee_aptitudes detailed gating ---

def test_sprint_turf_allowed():
    """Sprint + turf = A/A aptitude, should score highly."""
    sel = _make_selector()
    race = RaceOption(name="Sprint G3", grade="G3", distance=1200, surface="turf")
    score = sel._score_race(race, _state(CURREN_APTITUDES))
    assert score > 0, f"Sprint turf race should be allowed, got {score}"


def test_mile_turf_allowed():
    """Mile + turf = B/A aptitude, should be allowed but penalized."""
    sel = _make_selector()
    race = RaceOption(name="Mile G2", grade="G2", distance=1600, surface="turf")
    score = sel._score_race(race, _state(CURREN_APTITUDES))
    assert score > 0, f"Mile turf race should be allowed, got {score}"


def test_medium_blocked_by_aptitude():
    """Medium = F aptitude, should return 0 (hard block)."""
    sel = _make_selector()
    race = RaceOption(name="Medium G1", grade="G1", distance=2000, surface="turf")
    score = sel._score_race(race, _state(CURREN_APTITUDES))
    assert score == 0.0, f"Medium race should be blocked, got {score}"


def test_long_blocked_by_aptitude():
    """Long = F aptitude, should return 0."""
    sel = _make_selector()
    race = RaceOption(name="Long G1", grade="G1", distance=3000, surface="turf")
    score = sel._score_race(race, _state(CURREN_APTITUDES))
    assert score == 0.0, f"Long race should be blocked, got {score}"


def test_dirt_blocked_by_aptitude():
    """Dirt = G aptitude, should return 0."""
    sel = _make_selector()
    race = RaceOption(name="Dirt Sprint", grade="G3", distance=1200, surface="dirt")
    score = sel._score_race(race, _state(CURREN_APTITUDES))
    assert score == 0.0, f"Dirt race should be blocked, got {score}"


def test_sprint_beats_mile():
    """Sprint (A) should score higher than Mile (B) for same grade."""
    sel = _make_selector()
    sprint = RaceOption(name="Sprint G2", grade="G2", distance=1200, surface="turf")
    mile = RaceOption(name="Mile G2", grade="G2", distance=1600, surface="turf")
    state = _state(CURREN_APTITUDES)
    sprint_score = sel._score_race(sprint, state)
    mile_score = sel._score_race(mile, state)
    assert sprint_score > mile_score, (
        f"Sprint ({sprint_score}) should beat Mile ({mile_score})"
    )


def test_no_aptitudes_allows_all():
    """Without aptitudes or highlight data, all races should be allowed."""
    sel = _make_selector()
    race = RaceOption(name="Long Dirt", grade="G1", distance=3000, surface="dirt")
    score = sel._score_race(race, _state())
    assert score > 0, f"Without aptitude data, races should not be blocked"


# --- Both sources agree ---

def test_double_block():
    """White text + bad aptitude grade = definitely blocked."""
    sel = _make_selector()
    race = RaceOption(
        name="Bad All Around", grade="G1", distance=3000,
        surface="dirt", is_aptitude_ok=False,
    )
    score = sel._score_race(race, _state(CURREN_APTITUDES))
    assert score == 0.0
