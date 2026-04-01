"""Dry-run: use auto_turn's actual logic to decide what would happen, no taps.

Takes a screenshot, runs through the same detect_screen → build_game_state →
decision logic as auto_turn, but with tap() stubbed out so nothing changes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.career_helper as ch

# Stub out tap and swipe so no game state changes
_tap_log = []

_real_tap = ch.tap
def _fake_tap(x, y, delay=0.0):
    _tap_log.append(("tap", x, y))
    print(f"  [DRY] tap({x}, {y})")

_real_adb = ch.adb
def _fake_adb(cmd):
    if "input tap" in cmd or "input swipe" in cmd:
        _tap_log.append(("adb", cmd))
        print(f"  [DRY] {cmd}")
        return
    return _real_adb(cmd)

ch.tap = _fake_tap
ch.adb = _fake_adb

# Now import auto_turn (it uses career_helper.tap)
import scripts.auto_turn as at
at.tap = _fake_tap
at.swipe = lambda *a, **kw: print(f"  [DRY] swipe{a}")

# Run detection + decision
img = ch.screenshot("dry_run")
screen = at.detect_screen(img)
print(f"\nScreen: {screen}")

if screen in ("career_home", "career_home_summer", "ts_climax_home"):
    energy = at.get_energy_level(img)
    at.build_game_state(img, screen, energy=energy)
    # Read aptitudes (needed for race filtering)
    if at._cached_aptitudes:
        print(f"Aptitudes (cached): {at._cached_aptitudes}")
    else:
        print("Aptitudes: not cached yet")
    # Rebuild game state with aptitudes
    at._game_state = at.build_game_state(img, screen, energy=energy)
    print(f"Turn: {at._current_turn}")
    print(f"Stats: Spd={at._current_stats.speed} Sta={at._current_stats.stamina} Pow={at._current_stats.power} Gut={at._current_stats.guts} Wit={at._current_stats.wit}")
    print(f"SP: {at._skill_pts}")
    print(f"Energy: ~{energy}%")
    print(f"Consecutive races: {at._consecutive_races}")
    print(f"Aptitudes in state: {at._game_state.trainee_aptitudes}")

    # Check what the race selector would say
    race_action = None
    if hasattr(at, '_race_selector') and at._race_selector:
        at._game_state.energy = energy
        race_action = at._race_selector.should_race_this_turn(at._game_state)
        if race_action:
            print(f"Race selector: {race_action.reason}")
        else:
            print("Race selector: no race this turn")

    # Trace the decision logic
    print(f"\n--- Decision trace ---")
    if screen == "career_home_summer":
        mood = at.detect_mood(img)
        print(f"Summer camp turn, mood={mood}, energy={energy}%")
        if mood in ("AWFUL", "BAD"):
            print("→ Would do Recreation (mood fix)")
        elif energy < 50:
            inv = at._shop_manager.inventory
            vita = next((k for k in ("vita_65", "vita_40", "vita_20", "royal_kale") if inv.get(k, 0) > 0), None)
            if vita:
                print(f"→ Would use {vita} for energy, then train")
            else:
                print("→ Would rest (low energy, no items)")
        else:
            print("→ Would train")
    elif screen == "ts_climax_home":
        print(f"TS Climax training turn, energy={energy}%")
        # Load inventory from yaml
        at._shop_manager.load_inventory()
        inv = at._shop_manager.inventory
        print(f"Inventory: {dict(inv)}")
        if inv.get("reset_whistle", 0) > 0:
            print("→ Would use Reset Whistle (reshuffle training tiles)")
        if inv.get("empowering_mega", 0) > 0:
            print("→ Would use Empowering Megaphone")
        elif inv.get("motivating_mega", 0) > 0:
            print("→ Would use Motivating Megaphone")
        for key, gain in [("vita_65", 65), ("vita_40", 40), ("vita_20", 20)]:
            if inv.get(key, 0) > 0 and energy + gain <= 100:
                print(f"→ Would use {key} (+{gain}) before training")
                break
        print("→ Would train")
    else:
        # Normal career_home logic
        pre_summer = (35, 36, 59, 60)
        if at._current_turn in pre_summer and energy < 80:
            print(f"→ Pre-summer turn, energy {energy}% < 80% — would REST")
        elif at._skill_pts > 1000:
            print(f"→ SP {at._skill_pts} > 1000 — would visit SKILL SHOP")
        elif race_action:
            print(f"→ Would RACE: {race_action.reason}")
        elif energy < 10:
            print(f"→ Critically low energy {energy}% — would REST")
        elif at._current_turn < 36 and energy < 50:
            print(f"→ Bond phase, low energy — would REST")
        else:
            print(f"→ Would TRAIN")
else:
    print(f"Non-decision screen: {screen}")
    print("(auto_turn would handle this screen type directly)")
