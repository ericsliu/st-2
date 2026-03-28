"""Tests for the RunSpec system — piecewise utility and YAML loading."""

import pytest

from uma_trainer.decision.runspec import (
    HardConstraints,
    PolicyWeights,
    RunSpec,
    StatTarget,
    load_runspec,
    list_runspecs,
)
from uma_trainer.types import StatType, TraineeStats


class TestStatTargetUtility:
    """Piecewise utility integration across tiers."""

    def test_gain_below_minimum(self):
        t = StatTarget(minimum=300, target=600, excellent=800,
                       value_below_min=1.0, value_to_target=0.8,
                       value_to_excellent=0.25, value_above_excellent=0.05)
        # All 20 points land in below-minimum tier
        assert t.utility(current=100, gain=20) == 20 * 1.0

    def test_gain_crosses_minimum(self):
        t = StatTarget(minimum=300, target=600, excellent=800,
                       value_below_min=1.0, value_to_target=0.8)
        # 10 below min (× 1.0) + 10 in to_target (× 0.8) = 10 + 8 = 18
        assert t.utility(current=290, gain=20) == pytest.approx(18.0)

    def test_gain_entirely_in_target_zone(self):
        t = StatTarget(minimum=300, target=600, value_to_target=0.8)
        assert t.utility(current=400, gain=20) == pytest.approx(20 * 0.8)

    def test_gain_crosses_target(self):
        t = StatTarget(minimum=300, target=600, excellent=800,
                       value_to_target=0.8, value_to_excellent=0.25)
        # 5 in to_target (× 0.8) + 15 in to_excellent (× 0.25)
        assert t.utility(current=595, gain=20) == pytest.approx(5*0.8 + 15*0.25)

    def test_gain_above_excellent(self):
        t = StatTarget(minimum=300, target=600, excellent=800,
                       value_above_excellent=0.05)
        assert t.utility(current=900, gain=20) == pytest.approx(20 * 0.05)

    def test_zero_gain(self):
        t = StatTarget()
        assert t.utility(current=500, gain=0) == 0.0

    def test_gain_crosses_all_tiers(self):
        t = StatTarget(minimum=100, target=200, excellent=300,
                       value_below_min=1.0, value_to_target=0.8,
                       value_to_excellent=0.25, value_above_excellent=0.05)
        # 50→350: 50 below min (×1.0) + 100 to target (×0.8) + 100 to excellent (×0.25) + 50 above (×0.05)
        expected = 50*1.0 + 100*0.8 + 100*0.25 + 50*0.05
        assert t.utility(current=50, gain=300) == pytest.approx(expected)


class TestRunSpec:
    """RunSpec dataclass behavior."""

    def test_default_fills_all_stats(self):
        spec = RunSpec()
        for stat in StatType:
            assert stat.value in spec.stat_targets

    def test_stat_utility_delegates(self):
        spec = RunSpec()
        spec.stat_targets["speed"] = StatTarget(
            minimum=300, target=600, value_below_min=1.0
        )
        result = spec.stat_utility("speed", current=100, gain=20)
        assert result == pytest.approx(20 * 1.0)

    def test_stat_utility_unknown_stat(self):
        spec = RunSpec()
        # Non-existent stat gets half value
        result = spec.stat_utility("charisma", current=100, gain=20)
        assert result == pytest.approx(10.0)

    def test_compute_deficits(self):
        spec = RunSpec()
        spec.stat_targets["speed"] = StatTarget(minimum=300, target=600, excellent=800)
        stats = TraineeStats(speed=450, stamina=0, power=0, guts=0, wit=0)
        deficits = spec.compute_deficits(stats)
        speed_d = deficits["speed"]
        assert speed_d["deficit_to_min"] == 0  # 450 > 300
        assert speed_d["deficit_to_target"] == 150  # 600 - 450
        assert speed_d["overshoot_target"] == 0
        assert speed_d["pct_to_target"] == pytest.approx(0.75)

    def test_summary_serializable(self):
        spec = RunSpec(id="test", name="Test Spec")
        summary = spec.summary()
        assert summary["id"] == "test"
        assert "stat_targets" in summary
        assert "policy" in summary
        assert "constraints" in summary


class TestLoadRunspec:
    """YAML loading from data/runspecs/."""

    def test_load_parent_long(self):
        spec = load_runspec("parent_long_v1")
        assert spec.id == "parent_long_v1"
        assert spec.run_type == "parent_builder"
        assert spec.distance_target == "long"
        assert "speed" in spec.stat_targets
        assert spec.stat_targets["speed"].minimum == 500
        assert spec.stat_targets["speed"].target == 850

    def test_load_parent_sprint(self):
        spec = load_runspec("parent_sprint_v1")
        assert spec.run_type == "parent_builder"
        assert spec.distance_target == "sprint"
        assert spec.stat_targets["speed"].minimum == 550

    def test_load_parent_balanced(self):
        spec = load_runspec("parent_balanced_v1")
        assert spec.constraints.max_failure_rate == 0.12

    def test_load_missing_returns_defaults(self):
        spec = load_runspec("nonexistent_spec_xyz")
        assert spec.id == "nonexistent_spec_xyz"
        # All stats should have default targets
        for stat in StatType:
            assert stat.value in spec.stat_targets

    def test_list_runspecs(self):
        specs = list_runspecs()
        assert len(specs) >= 3
        ids = {s["id"] for s in specs}
        assert "parent_long_v1" in ids
        assert "parent_sprint_v1" in ids
        assert "parent_balanced_v1" in ids

    def test_policy_weights_loaded(self):
        spec = load_runspec("parent_long_v1")
        assert spec.policy.bond_future_value == 0.9
        assert spec.policy.failure_risk_penalty == 1.2

    def test_constraints_loaded(self):
        spec = load_runspec("parent_long_v1")
        assert spec.constraints.must_complete_goal_races is True
        assert spec.constraints.max_failure_rate == 0.15
        assert spec.constraints.min_energy_for_training == 45
