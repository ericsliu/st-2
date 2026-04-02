"""Dry-run: gather full game state (with real taps), then show what the bot would do.

Opens Full Stats, Training Items, and active effects just like the real bot,
but stops before executing any action (no training, racing, resting, or shopping).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.career_helper as ch
import scripts.auto_turn as at


def gather_and_decide():
    img = ch.screenshot("dry_run")
    screen = at.detect_screen(img)
    print(f"\nScreen: {screen}")

    if screen not in ("career_home", "career_home_summer", "ts_climax_home"):
        print(f"Non-decision screen — auto_turn would handle directly")
        return

    # ── Phase 1: Gather state (real taps to open/close info screens) ──

    energy = at.get_energy_level(img)
    at.build_game_state(img, screen, energy=energy)

    is_pre_debut = at._current_turn < 12

    # Active effects
    if not is_pre_debut:
        at._detect_active_effects()
        time.sleep(1)
        img = ch.screenshot("dry_run_post_effects")
        energy = at.get_energy_level(img)

    # Full Stats (aptitudes + conditions)
    at.read_fullstats()
    time.sleep(1)
    img = ch.screenshot("dry_run_post_stats")
    energy = at.get_energy_level(img)
    at._game_state = at.build_game_state(img, screen, energy=energy)

    # Inventory from Training Items screen
    if not is_pre_debut:
        at.read_inventory_from_training_items()
        time.sleep(1)
        img = ch.screenshot("dry_run_post_inv")
        energy = at.get_energy_level(img)

    # ── Phase 2: Display state ──

    print(f"\n{'='*50}")
    print(f"Turn: {at._current_turn}")
    print(f"Stats: Spd={at._current_stats.speed} Sta={at._current_stats.stamina} "
          f"Pow={at._current_stats.power} Gut={at._current_stats.guts} Wit={at._current_stats.wit}")
    print(f"SP: {at._skill_pts}")
    print(f"Energy: ~{energy}%")
    print(f"Consecutive races: {at._consecutive_races}")

    if at._cached_aptitudes:
        print(f"Aptitudes: {at._cached_aptitudes}")

    if at._active_conditions:
        print(f"Conditions: {at._active_conditions}")

    inv = dict(at._shop_manager.inventory)
    if inv:
        print(f"Inventory: {inv}")
    else:
        print("Inventory: (empty)")

    if at._shop_manager._active_effects:
        print(f"Active effects: {[e.item_key for e in at._shop_manager._active_effects]}")

    # ── Phase 3: Decision trace (no taps) ──

    print(f"\n--- Decision trace ---")

    # Race selector
    race_action = None
    at._game_state.energy = energy
    race_action = at._race_selector.should_race_this_turn(at._game_state)
    if race_action:
        print(f"Race selector: {race_action.reason}")
    else:
        print("Race selector: no race this turn")

    if screen == "career_home_summer":
        mood = at.detect_mood(img)
        print(f"Summer camp, mood={mood}, energy={energy}%")
        if mood in ("AWFUL", "BAD"):
            print("-> Would do Recreation (mood fix)")
        elif energy < 50:
            vita = next((k for k in ("vita_65", "vita_40", "vita_20", "royal_kale") if inv.get(k, 0) > 0), None)
            if vita:
                print(f"-> Would use {vita} for energy, then train")
            else:
                print("-> Would rest (low energy, no items)")
        else:
            print("-> Would train")

    elif screen == "ts_climax_home":
        print(f"TS Climax, energy={energy}%")
        if inv.get("reset_whistle", 0) > 0:
            print("-> Would use Reset Whistle")
        if inv.get("empowering_mega", 0) > 0:
            print("-> Would use Empowering Megaphone")
        elif inv.get("motivating_mega", 0) > 0:
            print("-> Would use Motivating Megaphone")
        for key, gain in [("vita_65", 65), ("vita_40", 40), ("vita_20", 20)]:
            if inv.get(key, 0) > 0 and energy + gain <= 100:
                print(f"-> Would use {key} (+{gain}) before training")
                break
        print("-> Would train")

    else:
        # Normal career_home decision
        has_energy_items = any(inv.get(k, 0) > 0 for k in ("vita_65", "vita_40", "vita_20", "royal_kale"))

        # Conditions to cure?
        if at._active_conditions:
            curable = []
            cure_map = at.CONDITION_CURE_MAP if hasattr(at, 'CONDITION_CURE_MAP') else {}
            for cond in at._active_conditions:
                cure_key = cure_map.get(cond)
                if cure_key and inv.get(cure_key, 0) > 0:
                    curable.append((cond, cure_key))
            if curable:
                print(f"-> Would cure conditions: {curable}")

        # Shop?
        should_shop = at._needs_shop_visit or (
            at._current_turn >= 6 and at._current_turn % 6 == 0
        )
        if should_shop:
            print("-> Would visit shop")

        # Consecutive race break
        if at._consecutive_races >= 3:
            if energy < 30:
                print(f"-> 3+ consecutive races, energy {energy}% — would REST")
            else:
                print(f"-> 3+ consecutive races — would TRAIN")
            return

        # Pre-summer
        pre_summer = (35, 36, 59, 60)
        if at._current_turn in pre_summer and energy < 80:
            if race_action and has_energy_items:
                print(f"-> Pre-summer but have energy items — would RACE: {race_action.reason}")
            elif race_action:
                print(f"-> Pre-summer, energy {energy}% < 80%, no energy items — would REST (skip race)")
            else:
                print(f"-> Pre-summer, energy {energy}% < 80% — would REST")
            return

        # SP check
        if at._skill_pts > 1000:
            print(f"-> SP {at._skill_pts} > 1000 — would visit SKILL SHOP")
            return

        # Race
        if race_action:
            print(f"-> Would RACE: {race_action.reason}")
            return

        # Energy checks
        if energy < 30:
            print(f"-> Low energy {energy}% — would REST")
        elif at._current_turn < 36 and energy < 50:
            print(f"-> Bond phase, low energy {energy}% — would REST")
        else:
            print(f"-> Would TRAIN")


gather_and_decide()
