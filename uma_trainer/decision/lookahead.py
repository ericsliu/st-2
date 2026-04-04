"""Energy budget and milestone lookahead for multi-turn planning.

Instead of hard-coding "rest if energy < X before summer camp", this module
computes how much energy the bot can freely spend before the next milestone,
given the items in inventory that can recover energy and mood.

Usage:
    budget = compute_energy_budget(
        current_energy=45,
        turns_until_milestone=3,
        target_energy=90,
        inventory={"vita_20": 2, "royal_kale": 1, "berry_cupcake": 1},
    )
    # budget.spendable = how much energy we can burn before the milestone
    # budget.plan = ordered list of items to use and when
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Energy recovery items in priority order (best value first).
# Each entry: (key, energy_gain, mood_delta)
ENERGY_ITEMS = [
    ("vita_65",    65,  0),
    ("vita_40",    40,  0),
    ("vita_20",    20,  0),
    ("royal_kale", 100, -1),  # Kale drops mood by 1 tier
]

# Mood recovery items (used to offset Kale penalty or fix bad mood).
# (key, mood_gain_tiers)
MOOD_ITEMS = [
    ("berry_cupcake", 2),
    ("plain_cupcake", 1),
]

# Rest recovers ~30-50% energy depending on mood.  Use conservative estimate.
REST_ENERGY_GAIN = 30

# Energy cost per training turn (approximate).
TRAIN_ENERGY_COST = 20

# Mood tiers ordered low to high.
MOOD_TIERS = ["AWFUL", "BAD", "NORMAL", "GOOD", "GREAT"]


def _mood_index(mood: str) -> int:
    try:
        return MOOD_TIERS.index(mood.upper())
    except ValueError:
        return 2  # default NORMAL


@dataclass
class ItemUse:
    """A planned item use at a specific point."""
    item_key: str
    reason: str  # e.g. "recover energy before summer camp"


@dataclass
class EnergyBudget:
    """Result of the lookahead computation."""
    spendable: int          # How much energy we can freely spend before milestone
    recovery_plan: list[ItemUse] = field(default_factory=list)  # Items to use on milestone turn
    total_recoverable: int = 0   # Total energy we can recover from items + rest
    needs_rest_turn: bool = False  # Whether we need to dedicate a turn to resting


def compute_energy_budget(
    current_energy: int,
    turns_until_milestone: int,
    target_energy: int,
    inventory: dict[str, int],
    current_mood: str = "GOOD",
) -> EnergyBudget:
    """Compute how much energy we can spend before a milestone.

    The idea: we want to arrive at the milestone turn with at least
    `target_energy`. We can spend energy training in the intervening
    turns, as long as items in inventory can bridge the gap.

    Args:
        current_energy: Current energy percentage (0-100).
        turns_until_milestone: Number of turns before the milestone.
        target_energy: Minimum energy needed at the milestone.
        inventory: Current item inventory {key: count}.
        current_mood: Current mood tier string.

    Returns:
        EnergyBudget with spendable energy and recovery plan.
    """
    if turns_until_milestone <= 0:
        # We're at the milestone — no spending room
        return EnergyBudget(spendable=0)

    # Work out the maximum energy we can recover using items.
    # We greedily pick items, tracking mood to ensure we can offset Kale's penalty.
    inv = {k: v for k, v in inventory.items() if v > 0}  # copy
    mood_idx = _mood_index(current_mood)
    recovery_plan: list[ItemUse] = []
    total_recovery = 0

    # Count available mood recovery capacity
    mood_recovery_available = 0
    for key, tiers in MOOD_ITEMS:
        mood_recovery_available += inv.get(key, 0) * tiers

    # Pick energy items greedily
    for key, energy_gain, mood_delta in ENERGY_ITEMS:
        count = inv.get(key, 0)
        if count <= 0:
            continue

        for _ in range(count):
            # If item drops mood, check the result is acceptable
            if mood_delta < 0:
                mood_after = mood_idx + mood_delta
                if mood_after < 1:  # Would drop to AWFUL — too risky
                    continue
                # If mood drops below NORMAL (index 2), we need recovery items
                if mood_after < 2:
                    recovery_needed = 2 - mood_after  # tiers needed to reach NORMAL
                    if mood_recovery_available < recovery_needed:
                        continue
                    mood_recovery_available -= recovery_needed
                mood_idx = mood_after  # Track cumulative mood drops

            total_recovery += energy_gain
            recovery_plan.append(ItemUse(
                item_key=key,
                reason=f"recover {energy_gain} energy",
            ))

    # If we use Kale, we need to also plan the cupcake
    kale_count = sum(1 for p in recovery_plan if p.item_key == "royal_kale")
    if kale_count > 0:
        mood_fix_needed = kale_count  # Each kale drops mood by 1
        for key, tiers in MOOD_ITEMS:
            while mood_fix_needed > 0 and inv.get(key, 0) > 0:
                recovery_plan.append(ItemUse(
                    item_key=key,
                    reason=f"offset kale mood penalty (+{tiers})",
                ))
                mood_fix_needed -= tiers
                inv[key] = inv.get(key, 0) - 1

    # Can also rest for one turn if we have turns to spare
    needs_rest = False
    rest_available = 0
    if turns_until_milestone >= 2:
        # We can dedicate one turn to resting (still have turns to train)
        rest_available = REST_ENERGY_GAIN
        needs_rest = True

    # Compute: if we use all recovery on the milestone turn,
    # what's the minimum energy we'd arrive with?
    # Energy at milestone = current_energy - (training_cost * turns_training) + recovery
    #
    # turns_training = turns_until_milestone - (1 if rest turn needed)
    # We want: energy_at_milestone >= target_energy
    # => current_energy - cost * turns_training + recovery >= target
    # => spendable = current_energy + recovery - target (can train freely up to this)

    # The "spendable" is how much total energy expenditure we can afford
    max_energy_at_milestone = current_energy + total_recovery + rest_available
    spendable = max(0, max_energy_at_milestone - target_energy)

    # But we also can't spend more than we actually have right now
    # (can't go below 0 on any given turn)
    spendable = min(spendable, current_energy)

    return EnergyBudget(
        spendable=spendable,
        recovery_plan=recovery_plan,
        total_recoverable=total_recovery + rest_available,
        needs_rest_turn=needs_rest and total_recovery < (target_energy - current_energy),
    )


@dataclass
class Milestone:
    """An upcoming event that requires preparation."""
    name: str
    turn: int                # First turn of the milestone
    target_energy: int       # Minimum energy needed
    target_mood: str = "GOOD"
    reserved_items: dict[str, int] = field(default_factory=dict)  # Items to save


# Known milestones for Trackblazer scenario
TRACKBLAZER_MILESTONES = [
    Milestone("summer_camp_1", turn=37, target_energy=90, target_mood="GREAT"),
    Milestone("summer_camp_2", turn=61, target_energy=90, target_mood="GREAT"),
    Milestone("ts_climax",     turn=72, target_energy=95, target_mood="GREAT",
              reserved_items={"master_hammer": 3, "reset_whistle": 1}),
]


def get_next_milestone(current_turn: int) -> Milestone | None:
    """Return the next upcoming milestone, or None if past all of them."""
    for m in TRACKBLAZER_MILESTONES:
        if current_turn < m.turn:
            return m
    return None


def should_conserve_energy(
    current_turn: int,
    current_energy: int,
    inventory: dict[str, int],
    current_mood: str = "GOOD",
) -> tuple[bool, str]:
    """High-level check: should we avoid energy-expensive actions this turn?

    Returns (should_conserve, reason).
    """
    milestone = get_next_milestone(current_turn)
    if milestone is None:
        return False, ""

    turns_left = milestone.turn - current_turn
    if turns_left > 5:
        return False, ""  # Too far out to worry

    budget = compute_energy_budget(
        current_energy=current_energy,
        turns_until_milestone=turns_left,
        target_energy=milestone.target_energy,
        inventory=inventory,
        current_mood=current_mood,
    )

    # If we can't afford even one training turn's worth of energy, conserve
    if budget.spendable < TRAIN_ENERGY_COST:
        reason = (f"{milestone.name} in {turns_left} turns, "
                  f"energy {current_energy}%, budget {budget.spendable}% "
                  f"(need {milestone.target_energy}%, "
                  f"can recover {budget.total_recoverable}%)")
        return True, reason

    return False, ""
