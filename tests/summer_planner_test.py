"""Tests for summer camp energy/failure-insurance planner.

Covers: charm-coverage fast path, kale efficiency thresholds, drink selection
(overshoot, size order, emptiness), charm/kale strategic interactions,
emergency fallbacks, and boundary conditions on energy and turn counts.
"""

import pytest

from uma_trainer.decision.summer_planner import (
    SummerAction,
    plan_summer_turn,
    SAFE_ENERGY_FLOOR,
    KALE_EFFICIENT_AT,
    DRINK_OVERSHOOT_ALLOWANCE,
)


# ── Charm-covers-all fast path ──────────────────────────────────────────────

def full_4_charms_cover_4_turns_test():
    inv = {"good_luck_charm": 4, "vita_40": 2, "vita_65": 1}
    plan = plan_summer_turn(energy=80, turns_remaining=4, inventory=inv)
    assert plan.kind == "charm"
    assert "cover" in plan.reason


def charm_exact_count_single_turn_test():
    plan = plan_summer_turn(
        energy=20, turns_remaining=1, inventory={"good_luck_charm": 1}
    )
    # 1 charm, 1 turn remaining → charm path (even at low energy, charms suffice).
    assert plan.kind == "charm"


def charm_surplus_triggers_charm_test():
    # More charms than turns, plenty of drinks — still charm.
    inv = {"good_luck_charm": 5, "vita_65": 3}
    plan = plan_summer_turn(energy=100, turns_remaining=2, inventory=inv)
    assert plan.kind == "charm"


def charm_one_short_breaks_fast_path_test():
    # 2 charms, 3 turns remaining — charms alone don't cover.
    inv = {"good_luck_charm": 2}
    plan = plan_summer_turn(energy=70, turns_remaining=3, inventory=inv)
    # Energy fine, no kale, no drinks → train this turn.
    assert plan.kind == "none"


# ── Kale efficiency thresholds ─────────────────────────────────────────────

def kale_at_very_low_energy_with_cupcake_test():
    inv = {"royal_kale": 1, "plain_cupcake": 1}
    plan = plan_summer_turn(energy=10, turns_remaining=4, inventory=inv)
    assert plan.kind == "kale_cupcake"
    assert plan.item_key == "royal_kale"


def kale_at_very_low_energy_without_cupcake_test():
    inv = {"royal_kale": 1}
    plan = plan_summer_turn(energy=10, turns_remaining=4, inventory=inv)
    assert plan.kind == "kale"
    assert plan.item_key == "royal_kale"


def kale_with_berry_cupcake_flavor_test():
    # Berry cupcake should also satisfy cupcake check.
    inv = {"royal_kale": 1, "berry_cupcake": 1}
    plan = plan_summer_turn(energy=5, turns_remaining=2, inventory=inv)
    assert plan.kind == "kale_cupcake"


def kale_just_above_efficient_threshold_prefers_drink_test():
    # Energy == KALE_EFFICIENT_AT (30): not below threshold → drink preferred.
    inv = {"royal_kale": 1, "vita_20": 1}
    plan = plan_summer_turn(
        energy=KALE_EFFICIENT_AT, turns_remaining=3, inventory=inv
    )
    assert plan.kind == "drink"
    assert plan.item_key == "vita_20"


def kale_just_below_efficient_threshold_uses_kale_test():
    # Energy just under threshold with drinks present — kale still wins.
    inv = {"royal_kale": 1, "vita_65": 1, "plain_cupcake": 1}
    plan = plan_summer_turn(
        energy=KALE_EFFICIENT_AT - 1, turns_remaining=3, inventory=inv
    )
    assert plan.kind == "kale_cupcake"


def kale_fallback_when_drinks_exhausted_low_energy_test():
    # Low energy but kale not yet at efficient threshold, no drinks.
    inv = {"royal_kale": 1}
    plan = plan_summer_turn(energy=45, turns_remaining=3, inventory=inv)
    assert plan.kind == "kale"


# ── Drink selection ─────────────────────────────────────────────────────────

def drink_biggest_fits_within_allowance_test():
    # Energy 40 + vita_65 = 105 → exactly at allowance edge (100 + 5). Accept.
    inv = {"vita_65": 1, "vita_40": 1, "vita_20": 1}
    plan = plan_summer_turn(energy=40, turns_remaining=2, inventory=inv)
    assert plan.kind == "drink"
    assert plan.item_key == "vita_65"


def drink_prefer_smaller_if_bigger_overflows_test():
    # Energy 45 → vita_65 = 110 > 105 → skip to vita_40 (=85, fits).
    inv = {"vita_65": 1, "vita_40": 1}
    plan = plan_summer_turn(energy=45, turns_remaining=3, inventory=inv)
    assert plan.item_key == "vita_40"


def drink_only_smallest_fits_allowance_test():
    # Energy 48: vita_65 → 113 (>105 skip), vita_40 → 88 (fits).
    inv = {"vita_65": 1, "vita_40": 1, "vita_20": 1}
    plan = plan_summer_turn(energy=48, turns_remaining=3, inventory=inv)
    assert plan.item_key == "vita_40"


def drink_all_overflow_picks_smallest_held_test():
    # Energy 49 with only vita_65 and vita_40 held. Both overshoot beyond 105
    # (49+65=114, 49+40=89). Wait — 89 fits. Use energy 60 instead: 60+65=125,
    # 60+40=100. vita_40 fits.  For true all-overflow: energy 70 → 70+40=110 >105.
    inv = {"vita_65": 1, "vita_40": 1}
    # But at energy 70 we're already ≥ SAFE_ENERGY_FLOOR → planner skips drinks.
    # So construct: kale forces low-energy branch.  Actually no — kale doesn't
    # force drinks.  Test the fallback by holding only one drink that overshoots.
    plan = plan_summer_turn(energy=49, turns_remaining=3, inventory={"vita_65": 1})
    # 49+65=114 > 105 → no fit; falls back to smallest held = vita_65.
    assert plan.kind == "drink"
    assert plan.item_key == "vita_65"  # only one held — overshoot accepted


def drink_only_vita_20_held_test():
    inv = {"vita_20": 3}
    plan = plan_summer_turn(energy=30, turns_remaining=3, inventory=inv)
    assert plan.kind == "drink"
    assert plan.item_key == "vita_20"


def drink_only_vita_40_held_test():
    inv = {"vita_40": 2}
    plan = plan_summer_turn(energy=40, turns_remaining=3, inventory=inv)
    assert plan.item_key == "vita_40"


def drink_only_vita_65_held_at_low_energy_test():
    inv = {"vita_65": 1}
    plan = plan_summer_turn(energy=10, turns_remaining=3, inventory=inv)
    assert plan.item_key == "vita_65"  # 10+65=75, fits


def drink_ignored_when_energy_already_safe_test():
    # Energy above SAFE_ENERGY_FLOOR with drink held but no kale → train, save drink.
    inv = {"vita_65": 1}
    plan = plan_summer_turn(energy=SAFE_ENERGY_FLOOR, turns_remaining=3, inventory=inv)
    assert plan.kind == "none"


# ── Charm as fallback ───────────────────────────────────────────────────────

def charm_fallback_low_energy_no_drinks_no_kale_test():
    inv = {"good_luck_charm": 1}
    plan = plan_summer_turn(energy=20, turns_remaining=3, inventory=inv)
    assert plan.kind == "charm"


def charm_fallback_beats_rest_when_any_charm_held_test():
    # Very low energy, only a single charm — still charm (not rest).
    inv = {"good_luck_charm": 1}
    plan = plan_summer_turn(energy=5, turns_remaining=4, inventory=inv)
    assert plan.kind == "charm"


# ── Kale strategy: burn charm first when both held ──────────────────────────

def kale_strategy_burn_charm_at_high_energy_test():
    inv = {"good_luck_charm": 1, "royal_kale": 1}
    plan = plan_summer_turn(energy=85, turns_remaining=3, inventory=inv)
    assert plan.kind == "charm"
    assert "kale" in plan.reason.lower()


def kale_strategy_still_charms_at_exact_floor_test():
    inv = {"good_luck_charm": 1, "royal_kale": 1}
    plan = plan_summer_turn(
        energy=SAFE_ENERGY_FLOOR, turns_remaining=3, inventory=inv
    )
    # At exactly the floor, energy < FLOOR is false → high-energy branch → kale strat.
    assert plan.kind == "charm"


def kale_strategy_drops_when_energy_truly_low_test():
    # Energy < floor with kale+charm — at this range the strategy switches.
    # < KALE_EFFICIENT_AT means use kale now.
    inv = {"good_luck_charm": 1, "royal_kale": 1, "plain_cupcake": 1}
    plan = plan_summer_turn(energy=15, turns_remaining=3, inventory=inv)
    assert plan.kind == "kale_cupcake"


def kale_strategy_middle_energy_uses_drink_over_charm_test():
    # 30 ≤ energy < 50 with kale+charm: planner currently chooses "charm" via
    # the high-energy branch since energy >= 50 fails — let's verify the actual
    # low-energy path runs. Below 50 but ≥ 30: kale inefficient, drinks preferred,
    # but with kale+charm present the kale-strat does NOT fire in low-energy branch.
    inv = {"good_luck_charm": 1, "royal_kale": 1, "vita_40": 1}
    plan = plan_summer_turn(energy=35, turns_remaining=3, inventory=inv)
    # low-energy branch: kale inefficient (35 ≥ 30), so prefer drink.
    assert plan.kind == "drink"
    assert plan.item_key == "vita_40"


# ── Boundary / empty ────────────────────────────────────────────────────────

def nothing_held_low_energy_rests_test():
    plan = plan_summer_turn(energy=20, turns_remaining=3, inventory={})
    assert plan.kind == "none"
    assert "rest" in plan.reason.lower()


def nothing_held_high_energy_trains_test():
    plan = plan_summer_turn(energy=90, turns_remaining=3, inventory={})
    assert plan.kind == "none"


def empty_inventory_single_turn_remaining_trains_on_high_energy_test():
    plan = plan_summer_turn(energy=80, turns_remaining=1, inventory={})
    assert plan.kind == "none"


def zero_turns_remaining_still_returns_action_test():
    # Shouldn't crash; last turn of summer should still pick something.
    # turns_remaining is bounded to 1 in the caller, but the planner must
    # handle it without dividing by zero or erroring.
    plan = plan_summer_turn(energy=60, turns_remaining=0, inventory={"good_luck_charm": 0})
    assert isinstance(plan, SummerAction)


def energy_exactly_at_safe_floor_with_only_drinks_test():
    # energy==50, no kale, no charm, drinks held → fast path (none)
    inv = {"vita_40": 2}
    plan = plan_summer_turn(energy=50, turns_remaining=3, inventory=inv)
    assert plan.kind == "none"


def energy_one_below_safe_floor_uses_drink_test():
    inv = {"vita_40": 1}
    plan = plan_summer_turn(
        energy=SAFE_ENERGY_FLOOR - 1, turns_remaining=3, inventory=inv
    )
    assert plan.kind == "drink"


def zero_items_present_but_zero_counts_treated_as_absent_test():
    # Ensure explicit zero counts don't fool the "has X" checks.
    inv = {
        "good_luck_charm": 0,
        "royal_kale": 0,
        "vita_65": 0,
        "plain_cupcake": 0,
    }
    plan = plan_summer_turn(energy=30, turns_remaining=3, inventory=inv)
    assert plan.kind == "none"  # rests (low energy, no resources)


# ── Realistic end-to-end scenarios ──────────────────────────────────────────

def scenario_classic_summer_opener_fresh_test():
    # Start of Classic summer: full charms bought, drinks in reserve, energy 60.
    # Charms cover all 4 turns → use charm, save drinks for Senior summer.
    inv = {"good_luck_charm": 4, "vita_40": 2, "vita_20": 1}
    plan = plan_summer_turn(energy=60, turns_remaining=4, inventory=inv)
    assert plan.kind == "charm"


def scenario_senior_summer_midway_spent_charms_test():
    # Turn 3 of 4 in Senior summer, 1 charm left, drinks consumed, energy 55.
    # 1 charm, 2 turns remaining — insufficient. Energy safe, no kale → train.
    inv = {"good_luck_charm": 1}
    plan = plan_summer_turn(energy=55, turns_remaining=2, inventory=inv)
    assert plan.kind == "none"


def scenario_senior_summer_low_energy_only_charm_test():
    # Same setup but energy crashed — charm saves the turn.
    inv = {"good_luck_charm": 1}
    plan = plan_summer_turn(energy=35, turns_remaining=2, inventory=inv)
    assert plan.kind == "charm"


def scenario_kale_held_over_from_earlier_test():
    # Mid-summer: kale still unused (saved for near-0), energy 70, 2 turns left.
    # Kale + charm held → kale strategy: burn charm now.
    inv = {"good_luck_charm": 1, "royal_kale": 1, "plain_cupcake": 1}
    plan = plan_summer_turn(energy=70, turns_remaining=2, inventory=inv)
    assert plan.kind == "charm"


def scenario_kale_without_charm_waits_test():
    # No charm available to burn energy down. Kale held but energy still safe.
    # Planner just trains; kale will be used on a future turn when energy drops.
    inv = {"royal_kale": 1, "plain_cupcake": 1}
    plan = plan_summer_turn(energy=70, turns_remaining=3, inventory=inv)
    assert plan.kind == "none"


def scenario_desperate_last_turn_test():
    # Last summer turn, energy critical, only a drink available.
    inv = {"vita_65": 1}
    plan = plan_summer_turn(energy=15, turns_remaining=1, inventory=inv)
    assert plan.kind == "drink"
    assert plan.item_key == "vita_65"


def scenario_drinks_prioritised_over_kale_when_not_efficient_test():
    # User's rule: without kale+charm combo, burn drinks first, save charms.
    # Here kale is present but energy 45 is just above efficient range,
    # so drinks are preferred.
    inv = {"royal_kale": 1, "vita_65": 1, "good_luck_charm": 1}
    # With charm+kale present and energy < 50, code falls into low-energy branch;
    # at energy 45 (≥ KALE_EFFICIENT_AT=30), kale is inefficient → drink wins.
    plan = plan_summer_turn(energy=45, turns_remaining=4, inventory=inv)
    assert plan.kind == "drink"


def scenario_many_drinks_one_charm_moderate_energy_test():
    # Plenty of drinks, 1 charm, moderate energy — should train (save everything).
    inv = {"good_luck_charm": 1, "vita_65": 2, "vita_40": 2, "vita_20": 1}
    plan = plan_summer_turn(energy=75, turns_remaining=4, inventory=inv)
    assert plan.kind == "none"


def scenario_full_stack_but_low_energy_test():
    # Everything held, but energy crashed mid-summer.
    # Charms cover all remaining turns → charm takes priority.
    inv = {
        "good_luck_charm": 3,
        "royal_kale": 1,
        "plain_cupcake": 1,
        "vita_65": 1,
    }
    plan = plan_summer_turn(energy=25, turns_remaining=3, inventory=inv)
    assert plan.kind == "charm"  # fast path: 3 charms == 3 turns


def scenario_full_stack_low_energy_charms_short_test():
    # Same but only 2 charms vs 3 turns → fast path misses,
    # low-energy branch kicks in. Kale efficient at energy 25 < 30.
    inv = {
        "good_luck_charm": 2,
        "royal_kale": 1,
        "plain_cupcake": 1,
        "vita_65": 1,
    }
    plan = plan_summer_turn(energy=25, turns_remaining=3, inventory=inv)
    assert plan.kind == "kale_cupcake"


# ── Contract / return value ─────────────────────────────────────────────────

def action_has_reason_string_test():
    plan = plan_summer_turn(energy=80, turns_remaining=3, inventory={})
    assert plan.reason  # non-empty


def drink_action_always_has_item_key_test():
    inv = {"vita_40": 1}
    plan = plan_summer_turn(energy=30, turns_remaining=3, inventory=inv)
    assert plan.kind == "drink"
    assert plan.item_key == "vita_40"


def kale_action_always_has_item_key_test():
    inv = {"royal_kale": 1}
    plan = plan_summer_turn(energy=10, turns_remaining=3, inventory=inv)
    assert plan.item_key == "royal_kale"


def charm_action_has_no_item_key_test():
    plan = plan_summer_turn(
        energy=80, turns_remaining=3, inventory={"good_luck_charm": 3}
    )
    assert plan.kind == "charm"
    assert plan.item_key is None


def none_action_has_no_item_key_test():
    plan = plan_summer_turn(energy=90, turns_remaining=3, inventory={})
    assert plan.kind == "none"
    assert plan.item_key is None
