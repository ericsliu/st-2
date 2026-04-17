"""Summer camp energy / failure-insurance planner.

A summer camp block is 4 turns. Each turn we want 0% effective failure, which
means: **active charm** (0% flat) OR **energy ≥ SAFE_ENERGY_FLOOR**.

Available resources per turn:
  - Good-Luck Charm: 0% failure, 1 turn. No energy cost. Max stock = 4.
  - Kale (royal_kale): +100 energy, -1 mood (needs cupcake to restore).
  - Drinks: vita_65 / vita_40 / vita_20, pure energy recovery, no side effects.

Strategy:
  1. If charms alone cover every remaining turn, use a charm each turn and save
     drinks for post-summer.
  2. Kale is only efficient near 0 energy (no +100 overflow). If we hold kale,
     burn charms first to pull energy down, then cash in kale.
  3. Otherwise prefer the biggest drink that won't overshoot 100%.
  4. Fall back to charm when drinks are out.
  5. Rest if nothing covers the turn.
"""

from __future__ import annotations

from dataclasses import dataclass


# Tunables
SAFE_ENERGY_FLOOR = 50  # train without charm/drink at or above this
KALE_EFFICIENT_AT = 30  # kale best used when energy < this (no +100 overflow)
DRINK_OVERSHOOT_ALLOWANCE = 5  # tolerate this much overshoot past 100%

DRINK_VALUES: dict[str, int] = {"vita_65": 65, "vita_40": 40, "vita_20": 20}


@dataclass
class SummerAction:
    """Single-turn summer action chosen by the planner.

    kind ∈ {"charm", "drink", "kale", "kale_cupcake", "none"}:
      - "none" means no pre-training item needed; just train.
    """
    kind: str
    item_key: str | None = None
    reason: str = ""


def _pick_drink(energy: int, inventory: dict[str, int]) -> str | None:
    """Pick the biggest drink that fits within energy headroom.

    Falls back to the smallest held drink if all overshoot.
    """
    held = [(k, v) for k, v in DRINK_VALUES.items() if inventory.get(k, 0) > 0]
    if not held:
        return None
    held.sort(key=lambda d: -d[1])  # biggest first
    for key, val in held:
        if energy + val <= 100 + DRINK_OVERSHOOT_ALLOWANCE:
            return key
    return held[-1][0]  # smallest — overshoot accepted


def plan_summer_turn(
    *,
    energy: int,
    turns_remaining: int,
    inventory: dict[str, int],
) -> SummerAction:
    """Pick the next summer-camp action.

    Args:
      energy: current energy %, 0–100.
      turns_remaining: summer turns left including this one.
      inventory: current item counts (shop_manager.inventory shape).
    """
    charms = inventory.get("good_luck_charm", 0)
    kale = inventory.get("royal_kale", 0)
    has_cupcake = (inventory.get("plain_cupcake", 0) > 0
                   or inventory.get("berry_cupcake", 0) > 0)

    # 1. Charms alone cover the rest of summer.
    if charms >= turns_remaining:
        return SummerAction(
            "charm",
            reason=f"{charms} charms cover remaining {turns_remaining} turn(s)",
        )

    # 2. Low energy — need real recovery this turn.
    if energy < SAFE_ENERGY_FLOOR:
        # Kale is most efficient when energy is very low (no +100 overflow).
        if kale > 0 and energy < KALE_EFFICIENT_AT:
            return SummerAction(
                "kale_cupcake" if has_cupcake else "kale",
                item_key="royal_kale",
                reason=f"energy {energy}% — kale captures full +100",
            )
        drink = _pick_drink(energy, inventory)
        if drink:
            return SummerAction("drink", item_key=drink, reason=f"{drink} recovery")
        if kale > 0:
            # No drinks left — spend kale even if not ideal
            return SummerAction(
                "kale_cupcake" if has_cupcake else "kale",
                item_key="royal_kale",
                reason="no drinks — kale fallback",
            )
        if charms > 0:
            return SummerAction(
                "charm",
                reason=f"energy {energy}% + no drinks — charm covers this turn",
            )
        return SummerAction("none", reason=f"energy {energy}%, no resources — rest")

    # 3. Energy OK (≥50). If we hold kale, burn a charm now so that next
    #    low-energy turn lands in kale's sweet spot.
    if kale > 0 and charms > 0:
        return SummerAction(
            "charm",
            reason="kale strategy — charm now, kale when energy drops",
        )

    # 4. Energy OK, no kale to optimise for — just train.
    return SummerAction("none", reason=f"energy {energy}% sufficient")
