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
        assert pb.max_turns == 72

        # Check explicit schedule — should have most turns mapped
        assert 18 in pb.schedule
        assert pb.schedule[18].action == "recreation"
        assert pb.schedule[16].action == "race"
        assert pb.schedule[37].action == "train"
        assert pb.schedule[72].action == "race"
        # Count: should have ~60 explicit entries (turns 13-72)
        assert len(pb.schedule) >= 55

        # Check recreation policy
        assert pb.recreation.enabled is True
        assert pb.recreation.sources["team_sirius"].total == 7
        assert pb.recreation.sources["riko"].total == 13

        # Check race policy
        assert pb.race.g1_policy == "always"
        assert pb.race.g2_policy == "skip_for_training"

        # Check skill policy
        assert pb.skills.defer_until_sp == 1200

        # Check friendship policy
        assert pb.friendship.priority_order == ["team_sirius", "riko"]
        assert pb.friendship.deadlines["team_sirius"].on_miss == "restart"

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


# ---------------------------------------------------------------------------
# Pair commitment
# ---------------------------------------------------------------------------

def _pair_config(commitments_path):
    """Build a playbook config with one flex pair (turns 42/43)."""
    return PlaybookConfig(
        schedule={
            42: TurnAction(
                action="flex",
                pair="cl_all_comers",
                role="lead",
                partner_turn=43,
                race="All Comers",
                note="Cl Late Sep flex pair lead",
            ),
            43: TurnAction(
                action="flex",
                pair="cl_all_comers",
                role="follow",
                partner_turn=42,
                note="Cl Early Oct flex pair follow Riko if raced",
            ),
        },
        recreation=RecreationPolicy(
            enabled=True,
            sources={
                "team_sirius": RecreationSource(total=7),
                "riko": RecreationSource(total=13),
            },
        ),
    )


class TestPairCommitment:
    """Pair commitment guarantees flex pairs resolve as race+Riko or train+train."""

    def test_lead_commits_to_race_when_riko_available(self, tmp_path):
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        # Provide a weak tile (gain < 40) so the evaluator picks race
        weak_tile = _make_tile(stat_type="speed", total_gain=20)
        result = engine.decide_turn(_make_state(turn=42, tiles=[weak_tile]))
        assert result.action_type == ActionType.RACE
        assert "cl_all_comers" in result.reason
        # Commitment persisted
        commitment = engine._get_commitment("cl_all_comers")
        assert commitment is not None
        assert commitment["choice"] == "race"
        assert commitment["lead_turn"] == 42

    def test_follow_returns_riko_when_lead_raced(self, tmp_path):
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        weak_tile = _make_tile(stat_type="speed", total_gain=20)
        engine.decide_turn(_make_state(turn=42, tiles=[weak_tile]))  # commits race
        result = engine.decide_turn(_make_state(turn=43))
        assert result.action_type == ActionType.GO_OUT
        assert "follow" in result.reason

    def test_lead_commits_to_train_when_riko_exhausted(self, tmp_path):
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        # Burn all 13 Riko uses
        for _ in range(13):
            engine.rec_tracker.on_recreation_used("riko")
        assert engine.rec_tracker.remaining_for("riko") == 0

        result = engine.decide_turn(_make_state(turn=42))
        assert result.action_type == ActionType.TRAIN
        assert engine._get_commitment("cl_all_comers")["choice"] == "train"

    def test_follow_returns_train_when_lead_trained(self, tmp_path):
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        for _ in range(13):
            engine.rec_tracker.on_recreation_used("riko")
        engine.decide_turn(_make_state(turn=42))  # commits train
        result = engine.decide_turn(_make_state(turn=43))
        assert result.action_type == ActionType.TRAIN

    def test_follow_without_commitment_raises(self, tmp_path):
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        # Run follow turn directly with no lead commitment
        with pytest.raises(RuntimeError, match="Pair commitment missing"):
            engine.decide_turn(_make_state(turn=43))

    def test_follow_with_mismatched_lead_turn_raises(self, tmp_path):
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        # Manually inject a commitment with wrong lead_turn (in the past, so
        # the stale-clear heuristic doesn't fire — current_turn must be > min)
        engine._record_commitment("cl_all_comers", "race", lead_turn=10)
        with pytest.raises(RuntimeError, match="lead_turn"):
            engine.decide_turn(_make_state(turn=43))

    def test_lead_reuses_existing_commitment_on_replay(self, tmp_path):
        """If the lead turn re-runs (e.g. after a crash), reuse the prior commitment."""
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        engine._record_commitment("cl_all_comers", "train", lead_turn=42)
        result = engine.decide_turn(_make_state(turn=42))
        # Even though Riko has uses remaining, the existing commitment wins
        assert result.action_type == ActionType.TRAIN

    def test_wants_recreation_on_pair_follow_with_race_choice(self, tmp_path):
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        engine._record_commitment("cl_all_comers", "race", lead_turn=42)
        assert engine.wants_recreation(43) is True

    def test_wants_recreation_false_on_pair_follow_with_train_choice(self, tmp_path):
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        engine._record_commitment("cl_all_comers", "train", lead_turn=42)
        assert engine.wants_recreation(43) is False

    def test_stale_commitments_cleared_on_early_turn(self, tmp_path):
        """Commitments referencing future turns are cleared when current turn precedes them."""
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        # Inject a commitment from a "prior career"
        engine._record_commitment("cl_all_comers", "race", lead_turn=42)
        assert engine._get_commitment("cl_all_comers") is not None
        # Now run decide_turn at turn 1 (start of fresh career)
        engine.decide_turn(_make_state(turn=1))
        assert engine._get_commitment("cl_all_comers") is None

    def test_lead_peeks_when_no_tiles_and_riko_available(self, tmp_path):
        """Without tile data and Riko available, lead returns TRAIN/peek mode."""
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        result = engine.decide_turn(_make_state(turn=42))
        assert result.action_type == ActionType.TRAIN
        assert "peeking" in result.reason
        # No commitment recorded yet — caller must peek tiles and call commit_pair_after_tiles
        assert engine._get_commitment("cl_all_comers") is None

    def test_commit_pair_after_tiles_strong_tile_picks_train(self, tmp_path):
        """A strong tile (≥40 total gain) commits the pair to train+train."""
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        strong_tile = _make_tile(stat_type="speed", total_gain=45)
        state = _make_state(turn=42, tiles=[strong_tile])
        choice = engine.commit_pair_after_tiles(state)
        assert choice == "train"
        assert engine._get_commitment("cl_all_comers")["choice"] == "train"

    def test_commit_pair_after_tiles_weak_tile_picks_race(self, tmp_path):
        """A weak tile (<40 total gain) commits the pair to race+Riko."""
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        weak_tile = _make_tile(stat_type="speed", total_gain=25)
        state = _make_state(turn=42, tiles=[weak_tile])
        choice = engine.commit_pair_after_tiles(state)
        assert choice == "race"
        assert engine._get_commitment("cl_all_comers")["choice"] == "race"

    def test_commit_pair_after_tiles_returns_none_off_pair_turn(self, tmp_path):
        """Non-pair turns return None so handle_training falls through normally."""
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        state = _make_state(turn=44, tiles=[_make_tile(total_gain=50)])
        assert engine.commit_pair_after_tiles(state) is None

    def test_clear_pair_commitments(self, tmp_path):
        path = tmp_path / "commitments.json"
        engine = PlaybookEngine(_pair_config(path), commitments_path=path)
        engine._record_commitment("cl_all_comers", "race", lead_turn=42)
        assert path.exists()
        engine.clear_pair_commitments()
        assert not path.exists()

    def test_load_sirius_riko_pair_fields(self):
        """The shipped strategy YAML should have pair fields tagged on flex pairs."""
        engine = load_playbook("sirius_riko_v1")
        sched = engine.playbook.schedule

        # Classic All Comers pair
        assert sched[42].pair == "cl_all_comers"
        assert sched[42].role == "lead"
        assert sched[42].partner_turn == 43
        assert sched[42].race == "All Comers"
        assert sched[43].pair == "cl_all_comers"
        assert sched[43].role == "follow"
        assert sched[43].partner_turn == 42

        # Senior Kyoto Kinen pair
        assert sched[51].pair == "sr_kyoto_kinen"
        assert sched[51].role == "lead"
        assert sched[51].race == "Kyoto Kinen"
        assert sched[52].role == "follow"

        # Senior All Comers pair
        assert sched[66].pair == "sr_all_comers"
        assert sched[66].role == "lead"
        assert sched[67].role == "follow"
