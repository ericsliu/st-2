"""Tests for the Playbook system — schedule resolution, conditions, recreation tracking."""

import pytest

from uma_trainer.decision.playbook import (
    FriendshipDeadline,
    FriendshipPolicy,
    PlaybookConfig,
    PlaybookEngine,
    RacePolicy,
    RecreationPolicy,
    RecreationSource,
    RecreationTracker,
    ScheduleBlock,
    SkillPolicy,
    TurnAction,
    load_playbook,
)
from uma_trainer.types import (
    ActionType,
    BotAction,
    GameState,
    SupportCard,
    TraineeStats,
    TrainingTile,
)


def _make_state(turn: int = 1, tiles: list[TrainingTile] | None = None) -> GameState:
    """Helper to build a minimal GameState for testing."""
    state = GameState()
    state.current_turn = turn
    state.training_tiles = tiles or []
    state.stats = TraineeStats()
    return state


def _make_tile(
    stat_type: str = "speed",
    cards: list[tuple[int, bool]] | None = None,
    total_gain: int = 10,
) -> TrainingTile:
    """Helper to make a TrainingTile. cards = list of (bond_level, is_friend)."""
    tile = TrainingTile()
    tile.stat_type = stat_type
    tile.support_cards = [
        SupportCard(card_id=str(i), name=f"card_{i}", bond_level=bond, is_friend=friend)
        for i, (bond, friend) in enumerate(cards or [])
    ]
    tile.stat_gains = {stat_type: total_gain}
    return tile


# ---------------------------------------------------------------------------
# Schedule resolution
# ---------------------------------------------------------------------------

class TestScheduleResolution:
    """Explicit schedule entries and block patterns."""

    def test_explicit_schedule_entry(self):
        config = PlaybookConfig(
            schedule={18: TurnAction(action="recreation", note="Sirius rec 1")},
        )
        engine = PlaybookEngine(config)
        action = engine._get_scheduled_action(18)
        assert action is not None
        assert action.action == "recreation"
        assert action.note == "Sirius rec 1"

    def test_unscheduled_turn_returns_none(self):
        config = PlaybookConfig(
            schedule={18: TurnAction(action="recreation")},
        )
        engine = PlaybookEngine(config)
        assert engine._get_scheduled_action(5) is None

    def test_schedule_block_non_repeating(self):
        config = PlaybookConfig(
            schedule_blocks=[
                ScheduleBlock(
                    start_turn=28, end_turn=35,
                    pattern=["recreation", "race", "race", "race"],
                    repeat=False,
                ),
            ],
        )
        engine = PlaybookEngine(config)
        assert engine._get_scheduled_action(28).action == "recreation"
        assert engine._get_scheduled_action(29).action == "race"
        assert engine._get_scheduled_action(30).action == "race"
        assert engine._get_scheduled_action(31).action == "race"
        # Beyond pattern length, no action
        assert engine._get_scheduled_action(32) is None

    def test_schedule_block_repeating(self):
        config = PlaybookConfig(
            schedule_blocks=[
                ScheduleBlock(
                    start_turn=40, end_turn=60,
                    pattern=["race", "race", "recreation"],
                    repeat=True,
                ),
            ],
        )
        engine = PlaybookEngine(config)
        # Pattern repeats: race, race, rec, race, race, rec, ...
        assert engine._get_scheduled_action(40).action == "race"
        assert engine._get_scheduled_action(41).action == "race"
        assert engine._get_scheduled_action(42).action == "recreation"
        assert engine._get_scheduled_action(43).action == "race"
        assert engine._get_scheduled_action(44).action == "race"
        assert engine._get_scheduled_action(45).action == "recreation"

    def test_explicit_schedule_overrides_block(self):
        """Explicit schedule entries take priority over blocks."""
        config = PlaybookConfig(
            schedule={30: TurnAction(action="rest", note="forced rest")},
            schedule_blocks=[
                ScheduleBlock(
                    start_turn=28, end_turn=35,
                    pattern=["race", "race", "race", "race", "race", "race", "race", "race"],
                    repeat=False,
                ),
            ],
        )
        engine = PlaybookEngine(config)
        assert engine._get_scheduled_action(30).action == "rest"
        assert engine._get_scheduled_action(29).action == "race"

    def test_block_outside_range_returns_none(self):
        config = PlaybookConfig(
            schedule_blocks=[
                ScheduleBlock(start_turn=40, end_turn=50, pattern=["race"], repeat=True),
            ],
        )
        engine = PlaybookEngine(config)
        assert engine._get_scheduled_action(39) is None
        assert engine._get_scheduled_action(51) is None
        assert engine._get_scheduled_action(40).action == "race"

    def test_flex_overrides_add_conditions(self):
        config = PlaybookConfig(
            schedule_blocks=[
                ScheduleBlock(
                    start_turn=40, end_turn=50,
                    pattern=["race"],
                    repeat=True,
                    flex_overrides={"race": ["double_friendship_training"]},
                ),
            ],
        )
        engine = PlaybookEngine(config)
        action = engine._get_scheduled_action(40)
        assert action.action == "race"
        assert "unless_double_friendship_training" in action.conditions


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

class TestConditions:
    """Conditional overrides on scheduled actions."""

    def test_unless_condition_triggers_fallback(self):
        """When 'unless_double_friendship_training' is set and there IS
        double-friendship training, use the fallback."""
        config = PlaybookConfig(
            schedule={
                30: TurnAction(
                    action="race",
                    conditions=["unless_double_friendship_training"],
                    fallback="train",
                ),
            },
        )
        engine = PlaybookEngine(config)
        # Two low-bond cards on one tile = double friendship training
        tile = _make_tile(cards=[(30, True), (40, True)])
        state = _make_state(turn=30, tiles=[tile])

        result = engine.decide_turn(state)
        assert result.action_type == ActionType.TRAIN

    def test_unless_condition_not_triggered(self):
        """When condition is NOT met, the scheduled action proceeds."""
        config = PlaybookConfig(
            schedule={
                30: TurnAction(
                    action="race",
                    conditions=["unless_double_friendship_training"],
                    fallback="train",
                ),
            },
        )
        engine = PlaybookEngine(config)
        # Only one low-bond card — not double friendship
        tile = _make_tile(cards=[(30, True), (90, True)])
        state = _make_state(turn=30, tiles=[tile])

        result = engine.decide_turn(state)
        assert result.action_type == ActionType.RACE

    def test_flex_turn_returns_wait(self):
        """Unscheduled turns return WAIT (fall through to dynamic logic)."""
        config = PlaybookConfig()
        engine = PlaybookEngine(config)
        state = _make_state(turn=5)
        result = engine.decide_turn(state)
        assert result.action_type == ActionType.WAIT

    def test_explicit_flex_action_returns_wait(self):
        config = PlaybookConfig(
            schedule={5: TurnAction(action="flex")},
        )
        engine = PlaybookEngine(config)
        state = _make_state(turn=5)
        result = engine.decide_turn(state)
        assert result.action_type == ActionType.WAIT


# ---------------------------------------------------------------------------
# decide_turn integration
# ---------------------------------------------------------------------------

class TestDecideTurn:
    """Full decide_turn flow."""

    def test_recreation_scheduled(self):
        config = PlaybookConfig(
            schedule={18: TurnAction(action="recreation", note="Sirius rec 1")},
            recreation=RecreationPolicy(enabled=True),
        )
        engine = PlaybookEngine(config)
        state = _make_state(turn=18)
        result = engine.decide_turn(state)
        assert result.action_type == ActionType.GO_OUT
        assert "recreation" in result.reason

    def test_race_scheduled(self):
        config = PlaybookConfig(
            schedule={12: TurnAction(action="race", note="Debut")},
        )
        engine = PlaybookEngine(config)
        state = _make_state(turn=12)
        result = engine.decide_turn(state)
        assert result.action_type == ActionType.RACE

    def test_rest_scheduled(self):
        config = PlaybookConfig(
            schedule={5: TurnAction(action="rest")},
        )
        engine = PlaybookEngine(config)
        state = _make_state(turn=5)
        result = engine.decide_turn(state)
        assert result.action_type == ActionType.REST

    def test_train_scheduled(self):
        config = PlaybookConfig(
            schedule={36: TurnAction(action="train", note="Summer camp")},
        )
        engine = PlaybookEngine(config)
        state = _make_state(turn=36)
        result = engine.decide_turn(state)
        assert result.action_type == ActionType.TRAIN


# ---------------------------------------------------------------------------
# Recreation tracker
# ---------------------------------------------------------------------------

class TestRecreationTracker:
    """Recreation use counting and source management."""

    def test_from_policy(self):
        policy = RecreationPolicy(
            enabled=True,
            sources={
                "team_sirius": RecreationSource(total=7),
                "riko": RecreationSource(total=13),
            },
        )
        tracker = RecreationTracker.from_policy(policy)
        assert tracker.uses_remaining["team_sirius"] == 7
        assert tracker.uses_remaining["riko"] == 13
        assert tracker.total_used == 0

    def test_use_specific_source(self):
        tracker = RecreationTracker(uses_remaining={"a": 3, "b": 5}, total_used=0)
        tracker.on_recreation_used("a")
        assert tracker.uses_remaining["a"] == 2
        assert tracker.uses_remaining["b"] == 5
        assert tracker.total_used == 1

    def test_use_auto_source(self):
        tracker = RecreationTracker(uses_remaining={"a": 0, "b": 5}, total_used=0)
        tracker.on_recreation_used()
        assert tracker.uses_remaining["a"] == 0
        assert tracker.uses_remaining["b"] == 4
        assert tracker.total_used == 1

    def test_any_remaining(self):
        tracker = RecreationTracker(uses_remaining={"a": 0, "b": 0})
        assert not tracker.any_remaining
        tracker.uses_remaining["b"] = 1
        assert tracker.any_remaining

    def test_no_negative(self):
        tracker = RecreationTracker(uses_remaining={"a": 0})
        tracker.on_recreation_used("a")
        assert tracker.uses_remaining["a"] == 0


# ---------------------------------------------------------------------------
# wants_recreation
# ---------------------------------------------------------------------------

class TestWantsRecreation:

    def test_wants_recreation_when_scheduled(self):
        config = PlaybookConfig(
            schedule={18: TurnAction(action="recreation")},
            recreation=RecreationPolicy(enabled=True),
        )
        engine = PlaybookEngine(config)
        assert engine.wants_recreation(18) is True

    def test_no_recreation_when_not_scheduled(self):
        config = PlaybookConfig(
            recreation=RecreationPolicy(enabled=True),
        )
        engine = PlaybookEngine(config)
        assert engine.wants_recreation(18) is False

    def test_no_recreation_when_disabled(self):
        config = PlaybookConfig(
            schedule={18: TurnAction(action="recreation")},
            recreation=RecreationPolicy(enabled=False),
        )
        engine = PlaybookEngine(config)
        assert engine.wants_recreation(18) is False


# ---------------------------------------------------------------------------
# Skill deferral
# ---------------------------------------------------------------------------

class TestSkillDeferral:

    def test_defer_by_sp(self):
        config = PlaybookConfig(skills=SkillPolicy(defer_until_sp=2500))
        engine = PlaybookEngine(config)
        assert engine.should_defer_skills(1200, turn=50) is True
        assert engine.should_defer_skills(2500, turn=50) is False
        assert engine.should_defer_skills(3000, turn=50) is False

    def test_defer_by_turn(self):
        config = PlaybookConfig(skills=SkillPolicy(defer_until_turn=70))
        engine = PlaybookEngine(config)
        assert engine.should_defer_skills(5000, turn=50) is True
        assert engine.should_defer_skills(5000, turn=70) is False

    def test_no_deferral_by_default(self):
        config = PlaybookConfig()
        engine = PlaybookEngine(config)
        assert engine.should_defer_skills(100, turn=1) is False


# ---------------------------------------------------------------------------
# Friendship deadline check
# ---------------------------------------------------------------------------

class TestFriendshipDeadline:

    def test_deadline_missed_triggers_restart(self):
        config = PlaybookConfig(
            recreation=RecreationPolicy(
                enabled=True,
                sources={"team_sirius": RecreationSource(total=7)},
            ),
            friendship=FriendshipPolicy(
                deadlines={"team_sirius": FriendshipDeadline(by_turn=18, on_miss="restart")},
            ),
        )
        engine = PlaybookEngine(config)
        # No recreations used by turn 18 -> deadline missed
        assert engine.check_friendship_deadline(18) == "restart"

    def test_deadline_met(self):
        config = PlaybookConfig(
            recreation=RecreationPolicy(
                enabled=True,
                sources={"team_sirius": RecreationSource(total=7)},
            ),
            friendship=FriendshipPolicy(
                deadlines={"team_sirius": FriendshipDeadline(by_turn=18, on_miss="restart")},
            ),
        )
        engine = PlaybookEngine(config)
        # Use one recreation -> friendship was unlocked
        engine.rec_tracker.on_recreation_used("team_sirius")
        assert engine.check_friendship_deadline(18) is None

    def test_deadline_not_reached_yet(self):
        config = PlaybookConfig(
            recreation=RecreationPolicy(
                enabled=True,
                sources={"team_sirius": RecreationSource(total=7)},
            ),
            friendship=FriendshipPolicy(
                deadlines={"team_sirius": FriendshipDeadline(by_turn=18, on_miss="restart")},
            ),
        )
        engine = PlaybookEngine(config)
        assert engine.check_friendship_deadline(10) is None


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

class TestLoadPlaybook:

    def test_load_sirius_riko(self):
        engine = load_playbook("sirius_riko_v1")
        pb = engine.playbook
        assert pb.id == "sirius_riko_v1"
        assert pb.scenario == "trackblazer"
        assert pb.runspec == "sirius_speed_v1"
        assert pb.max_turns == 78

        # Check explicit schedule
        assert 18 in pb.schedule
        assert pb.schedule[18].action == "recreation"

        # Check schedule blocks exist
        assert len(pb.schedule_blocks) >= 2

        # Check recreation policy
        assert pb.recreation.enabled is True
        assert pb.recreation.sources["team_sirius"].total == 7
        assert pb.recreation.sources["riko"].total == 13

        # Check race policy
        assert pb.race.g1_policy == "always"
        assert pb.race.g2_policy == "skip_for_training"

        # Check skill policy
        assert pb.skills.defer_until_sp == 2500

        # Check friendship policy
        assert pb.friendship.priority_order == ["team_sirius", "riko"]
        assert pb.friendship.deadlines["team_sirius"].on_miss == "restart"

        # Check item priorities
        assert pb.item_priorities[0] == "heart"

    def test_load_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            load_playbook("nonexistent_strategy")


# ---------------------------------------------------------------------------
# Recreation source tracking (Phase 4)
# ---------------------------------------------------------------------------

class TestRecreationSourceTracking:
    """Smart source selection based on priority and availability gates."""

    def test_best_source_respects_priority(self):
        policy = RecreationPolicy(
            enabled=True,
            sources={
                "team_sirius": RecreationSource(total=7),
                "riko": RecreationSource(total=13),
            },
        )
        tracker = RecreationTracker.from_policy(policy)
        # Priority order is insertion order: team_sirius first
        assert tracker.best_source(current_turn=10) == "team_sirius"

    def test_best_source_skips_exhausted(self):
        policy = RecreationPolicy(
            enabled=True,
            sources={
                "team_sirius": RecreationSource(total=7),
                "riko": RecreationSource(total=13),
            },
        )
        tracker = RecreationTracker.from_policy(policy)
        tracker.uses_remaining["team_sirius"] = 0
        assert tracker.best_source(current_turn=10) == "riko"

    def test_best_source_none_when_all_exhausted(self):
        policy = RecreationPolicy(
            enabled=True,
            sources={
                "team_sirius": RecreationSource(total=7),
                "riko": RecreationSource(total=13),
            },
        )
        tracker = RecreationTracker.from_policy(policy)
        tracker.uses_remaining["team_sirius"] = 0
        tracker.uses_remaining["riko"] = 0
        assert tracker.best_source(current_turn=50) is None

    def test_is_special_use_on_last_remaining(self):
        policy = RecreationPolicy(
            enabled=True,
            sources={"team_sirius": RecreationSource(total=7, special_index=7)},
        )
        tracker = RecreationTracker.from_policy(policy)
        # Use 6 recreations, leaving 1
        for _ in range(6):
            tracker.on_recreation_used("team_sirius")
        assert tracker.remaining_for("team_sirius") == 1
        assert tracker.is_special_use("team_sirius") is True

    def test_is_special_use_not_yet(self):
        policy = RecreationPolicy(
            enabled=True,
            sources={"team_sirius": RecreationSource(total=7, special_index=7)},
        )
        tracker = RecreationTracker.from_policy(policy)
        # Still have 5 remaining — not special yet
        tracker.on_recreation_used("team_sirius")
        tracker.on_recreation_used("team_sirius")
        assert tracker.remaining_for("team_sirius") == 5
        assert tracker.is_special_use("team_sirius") is False

    def test_auto_source_uses_priority_order(self):
        policy = RecreationPolicy(
            enabled=True,
            sources={
                "team_sirius": RecreationSource(total=3),
                "riko": RecreationSource(total=5),
            },
        )
        tracker = RecreationTracker.from_policy(policy)
        # Auto-select should prefer team_sirius
        tracker.on_recreation_used()
        assert tracker.remaining_for("team_sirius") == 2
        assert tracker.remaining_for("riko") == 5


class TestPlaybookEngineRecreation:
    """Engine-level recreation completion tracking."""

    def test_on_recreation_completed_decrements(self):
        config = PlaybookConfig(
            recreation=RecreationPolicy(
                enabled=True,
                sources={"team_sirius": RecreationSource(total=7)},
            ),
        )
        engine = PlaybookEngine(config)
        engine.on_recreation_completed(turn=18)
        assert engine.rec_tracker.remaining_for("team_sirius") == 6
        assert engine.rec_tracker.total_used == 1

    def test_on_recreation_completed_explicit_source(self):
        config = PlaybookConfig(
            recreation=RecreationPolicy(
                enabled=True,
                sources={
                    "team_sirius": RecreationSource(total=7),
                    "riko": RecreationSource(total=13),
                },
            ),
        )
        engine = PlaybookEngine(config)
        engine.on_recreation_completed(source="riko", turn=40)
        assert engine.rec_tracker.remaining_for("team_sirius") == 7
        assert engine.rec_tracker.remaining_for("riko") == 12
