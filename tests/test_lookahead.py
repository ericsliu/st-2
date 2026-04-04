"""Tests for the energy budget / lookahead system."""

from uma_trainer.decision.lookahead import (
    compute_energy_budget,
    get_next_milestone,
    should_conserve_energy,
    TRAIN_ENERGY_COST,
)


class TestComputeEnergyBudget:
    def test_at_milestone_no_budget(self):
        budget = compute_energy_budget(
            current_energy=50, turns_until_milestone=0,
            target_energy=90, inventory={},
        )
        assert budget.spendable == 0

    def test_high_energy_no_items_needed(self):
        budget = compute_energy_budget(
            current_energy=95, turns_until_milestone=2,
            target_energy=90, inventory={},
        )
        # 95 + 30 (rest) - 90 = 35
        assert budget.spendable > 0

    def test_vita_extends_budget(self):
        no_items = compute_energy_budget(
            current_energy=50, turns_until_milestone=3,
            target_energy=90, inventory={},
        )
        with_vita = compute_energy_budget(
            current_energy=50, turns_until_milestone=3,
            target_energy=90, inventory={"vita_40": 1},
        )
        assert with_vita.spendable > no_items.spendable
        assert with_vita.total_recoverable > no_items.total_recoverable

    def test_kale_needs_cupcake(self):
        # Kale alone — mood would drop, should still be usable if mood is GOOD+
        budget_kale_only = compute_energy_budget(
            current_energy=30, turns_until_milestone=3,
            target_energy=90, inventory={"royal_kale": 1},
            current_mood="GOOD",
        )
        # Kale with cupcake — can offset the mood penalty
        budget_kale_cupcake = compute_energy_budget(
            current_energy=30, turns_until_milestone=3,
            target_energy=90,
            inventory={"royal_kale": 1, "plain_cupcake": 1},
            current_mood="GOOD",
        )
        # Both should include kale since GOOD - 1 = NORMAL (not AWFUL)
        kale_in_plan = any(p.item_key == "royal_kale" for p in budget_kale_only.recovery_plan)
        assert kale_in_plan

    def test_kale_rejected_at_bad_mood(self):
        # At BAD mood, kale would drop to AWFUL — should be skipped
        budget = compute_energy_budget(
            current_energy=30, turns_until_milestone=3,
            target_energy=90, inventory={"royal_kale": 1},
            current_mood="BAD",
        )
        kale_in_plan = any(p.item_key == "royal_kale" for p in budget.recovery_plan)
        assert not kale_in_plan

    def test_spendable_capped_at_current_energy(self):
        # Even with tons of recovery, can't spend more than we have
        budget = compute_energy_budget(
            current_energy=20, turns_until_milestone=5,
            target_energy=10,
            inventory={"vita_65": 2, "vita_40": 2},
        )
        assert budget.spendable <= 20

    def test_multiple_vitas_stack(self):
        budget = compute_energy_budget(
            current_energy=30, turns_until_milestone=3,
            target_energy=90,
            inventory={"vita_20": 3},
        )
        # 3 x vita_20 = 60 recovery + 30 rest = 90
        # 30 + 90 - 90 = 30 spendable (capped at current=30)
        assert budget.total_recoverable >= 90
        assert budget.spendable == 30


class TestGetNextMilestone:
    def test_early_game_returns_summer1(self):
        m = get_next_milestone(10)
        assert m is not None
        assert m.name == "summer_camp_1"

    def test_between_summers(self):
        m = get_next_milestone(40)
        assert m is not None
        assert m.name == "summer_camp_2"

    def test_before_ts_climax(self):
        m = get_next_milestone(65)
        assert m is not None
        assert m.name == "ts_climax"

    def test_past_all_milestones(self):
        m = get_next_milestone(75)
        assert m is None


class TestShouldConserveEnergy:
    def test_far_from_milestone_no_conserve(self):
        conserve, _ = should_conserve_energy(
            current_turn=20, current_energy=50,
            inventory={}, current_mood="GOOD",
        )
        assert not conserve

    def test_low_energy_near_milestone_conserve(self):
        conserve, reason = should_conserve_energy(
            current_turn=35, current_energy=30,
            inventory={}, current_mood="GOOD",
        )
        assert conserve
        assert "summer_camp_1" in reason

    def test_low_energy_but_items_save_us(self):
        conserve, _ = should_conserve_energy(
            current_turn=35, current_energy=30,
            inventory={"vita_65": 1},
            current_mood="GOOD",
        )
        # 30 + 65 (vita) + 30 (rest) = 125, target 90 => spendable 35
        assert not conserve

    def test_one_turn_before_milestone_tight(self):
        conserve, _ = should_conserve_energy(
            current_turn=36, current_energy=40,
            inventory={}, current_mood="GOOD",
        )
        # 1 turn left, no items, no rest (only 1 turn), 40 < 90
        assert conserve
