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


def _print_shop_plan(live_scan: bool = False):
    """Show the shop want list the bot would build this turn.

    If live_scan=True, actually tap into the shop, OCR its contents (dry
    handle_shop call — no purchases), then back out. This is the only way
    to know what's actually on the shelf this turn.
    """
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE, ItemTier

    tier_overrides, ankle_stock, buyable = at._build_shop_plan()
    if not buyable:
        print("   Shop plan: nothing to buy")
        return

    inv = dict(at._shop_manager.inventory)
    print(f"   Shop want list ({len(buyable)} items, best first):")
    for tier, cost, key in buyable[:10]:  # Top 10 — full list is long
        item = ITEM_CATALOGUE[key]
        owned = inv.get(key, 0)
        max_s = ankle_stock.get(key, item.max_stock)
        marker = "*" if key in ("pretty_mirror", "grilled_carrots") else " "
        note = ""
        if key in tier_overrides:
            note = f" [override: {tier.name}]"
        print(f"    {marker} {key:<26} {tier.name:<5} {cost:>3}c  own={owned}/{max_s}{note}")
    if len(buyable) > 10:
        print(f"    (+ {len(buyable) - 10} more lower-priority items)")

    if not live_scan:
        return

    # ── Live scan: enter shop, OCR contents, back out ──
    print("\n   --- Live shop scan (read-only) ---")
    ch.tap(*at.BTN_SHOP, delay=2.5)
    shop_img = ch.screenshot(f"dry_shop_{int(time.time())}")
    if at.detect_screen(shop_img) != "shop":
        print("   (Could not enter shop — staying on career_home)")
        return
    try:
        result = at.handle_shop(shop_img, dry=True)
    except Exception as e:
        print(f"   Shop scan error: {e}")
        return
    if not result:
        print("   (handle_shop returned nothing)")
        return
    available, would_buy = result

    # Organize by status
    would_buy_set = set(would_buy)
    on_shelf_buy = [a for a in available if a["key"] in would_buy_set]
    on_shelf_purchased = [a for a in available if a["purchased"]]
    on_shelf_skip = [a for a in available if not a["purchased"] and a["key"] not in would_buy_set]

    print(f"   {len(available)} items scanned on shelf")
    if on_shelf_buy:
        print(f"   WOULD BUY ({len(on_shelf_buy)}):")
        for a in on_shelf_buy:
            marker = "*" if a["key"] in ("pretty_mirror", "grilled_carrots") else " "
            print(f"    {marker} {a['name']:<30} {a['tier']:<5} {a['cost']:>3}c")
    else:
        print("   WOULD BUY: nothing")
    if on_shelf_purchased:
        names = ", ".join(a["name"] for a in on_shelf_purchased)
        print(f"   Already purchased: {names}")
    if on_shelf_skip:
        print(f"   Skipped (wrong tier / owned / unaffordable):")
        for a in on_shelf_skip:
            marker = "*" if a["key"] in ("pretty_mirror", "grilled_carrots") else " "
            print(f"    {marker} {a['name']:<30} {a['tier']:<5} {a['cost']:>3}c")

    # Flag missed critical items
    critical = {"pretty_mirror", "grilled_carrots", "rich_hand_cream"}
    on_shelf_keys = {a["key"] for a in available}
    missing = critical - on_shelf_keys
    if missing:
        print(f"   NOT ON SHELF: {sorted(missing)}")


def _print_item_use_plan():
    """Show use-immediately items that would be consumed this turn."""
    from uma_trainer.decision.shop_manager import ITEM_CATALOGUE

    inv = dict(at._shop_manager.inventory)
    use_now = {}
    for key, count in inv.items():
        item = ITEM_CATALOGUE.get(key)
        if item and item.use_immediately and count > 0:
            use_now[key] = count

    # Apply carrot-defer rule (mirrors auto_turn.py)
    if "grilled_carrots" in use_now:
        try:
            sirius_bond = at._card_tracker.get_bond("team_sirius") if at._card_tracker.is_tracked("team_sirius") else -1
        except Exception:
            sirius_bond = -1
        bond_met = sirius_bond >= 60 or at._sirius_bond_unlocked
        if bond_met and at._current_turn < 36:
            print(f"   Carrots deferred (Sirius bond={sirius_bond}, unlocked={at._sirius_bond_unlocked}, turn<{36})")
            del use_now["grilled_carrots"]

    if use_now:
        print(f"-> Would USE items: {use_now}")


def _print_skill_shop_plan():
    """Show whether the bot would visit the skill shop this turn."""
    sp = at._skill_pts or 0
    pb = at._playbook_engine
    if pb and pb.playbook.skills:
        sp_thresh = getattr(pb.playbook.skills, "defer_until_sp", None)
        turn_thresh = getattr(pb.playbook.skills, "defer_until_turn", None)
        gate = []
        if sp_thresh is not None:
            gate.append(f"SP>={sp_thresh}")
        if turn_thresh is not None:
            gate.append(f"turn>={turn_thresh}")
        ok_sp = sp_thresh is None or sp >= sp_thresh
        ok_turn = turn_thresh is None or at._current_turn >= turn_thresh
        if ok_sp or ok_turn:
            print(f"-> Would visit SKILL SHOP (SP={sp}, gate={' or '.join(gate) or 'none'})")
        else:
            print(f"   Skill shop deferred: SP={sp}, turn={at._current_turn}, gate={' or '.join(gate)}")
    else:
        if sp > 1000:
            print(f"-> Would visit SKILL SHOP (SP={sp} > 1000)")
        else:
            print(f"   Skill shop deferred: SP={sp}")


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

    # TS Climax is always late-game; override turn if parsing failed
    if screen == "ts_climax_home" and at._current_turn < 72:
        at._current_turn = 72
        print(f"(Forced turn=72 for TS Climax)")

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
    print(f"Consecutive races: {at._scenario._consecutive_races}")

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

    # Playbook check
    if at._playbook_engine:
        at._game_state.energy = energy
        pb_action = at._playbook_engine.decide_turn(at._game_state)
        sched = at._playbook_engine._get_scheduled_action(at._current_turn)
        print(f"Playbook schedule: {sched}")
        print(f"Playbook decision: {pb_action.action_type.value} — {pb_action.reason}")
        deadline = at._playbook_engine.check_friendship_deadline(at._current_turn)
        if deadline:
            print(f"Friendship deadline: {deadline}")
        rec = at._playbook_engine.rec_tracker
        if rec and rec.uses_remaining:
            for name, remaining in rec.uses_remaining.items():
                print(f"  Recreation '{name}': {remaining} uses left")

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
            has_kale = inv.get("royal_kale", 0) > 0
            has_cupcake = inv.get("plain_cupcake", 0) > 0 or inv.get("berry_cupcake", 0) > 0
            if has_kale and has_cupcake and energy < 30:
                cupcake_key = "plain_cupcake" if inv.get("plain_cupcake", 0) > 0 else "berry_cupcake"
                print(f"-> Would use royal_kale (+100 energy) + {cupcake_key} (mood restore), then train")
            elif has_kale and energy < 20:
                print(f"-> Would use royal_kale (+100 energy, mood will drop), then train")
            else:
                vita = next((k for k in ("vita_65", "vita_40", "vita_20") if inv.get(k, 0) > 0), None)
                if vita:
                    print(f"-> Would use {vita} for energy, then train")
                elif inv.get("good_luck_charm", 0) > 0:
                    print(f"-> Would use good_luck_charm (0% failure), then train")
                else:
                    print("-> Would rest (low energy, no items)")
        else:
            if inv.get("good_luck_charm", 0) > 0:
                print(f"-> Would use good_luck_charm (0% failure this turn)")
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
        if inv.get("good_luck_charm", 0) > 0:
            print(f"-> Would use good_luck_charm (0% failure this turn)")
        print("-> Would train")

    else:
        # Normal career_home decision — mirrors auto_turn.py phase order:
        # Phase 2 housekeeping (cure, shop, item use) ALWAYS happens first,
        # then Phase 3 final action (playbook overrides fallback logic).
        has_energy_items = any(inv.get(k, 0) > 0 for k in ("vita_65", "vita_40", "vita_20", "royal_kale"))

        # Conditions to cure?
        if at._active_conditions:
            cure_map = getattr(at, "CONDITION_CURES", {})
            curable_now = []        # in inventory
            curable_post_shop = []  # in shop want list
            uncurable = []
            _, _, shop_buyable = at._build_shop_plan()
            shop_keys = {key for _, _, key in shop_buyable}
            for cond in at._active_conditions:
                cure_key = cure_map.get(cond)
                if cure_key and inv.get(cure_key, 0) > 0:
                    curable_now.append((cond, cure_key))
                elif cure_key and cure_key in shop_keys:
                    curable_post_shop.append((cond, cure_key))
                else:
                    uncurable.append(cond)
            if curable_now:
                print(f"-> Would cure now: {curable_now}")
            if curable_post_shop:
                print(f"-> Would cure after shop: {curable_post_shop}")
            if uncurable:
                print(f"-> CANNOT cure: {uncurable} (no items in inv or shop)")

        # Shop plan (every turn post-debut) — actually enter shop to read shelves
        should_shop = at._needs_shop_visit or at._current_turn >= 6
        if should_shop:
            reason = "flagged (race win)" if at._needs_shop_visit else "per-turn"
            print(f"-> Would visit shop ({reason})")
            _print_shop_plan(live_scan=True)

        # Use-immediately items that would be consumed this turn
        _print_item_use_plan()

        # Skill shop plan
        _print_skill_shop_plan()

        # Phase 3 final action — playbook wins if it has a non-wait decision
        print()
        if at._playbook_engine and pb_action.action_type.value != "wait":
            print(f"=> FINAL ACTION (playbook): {pb_action.action_type.value.upper()} — {pb_action.reason}")
            return

        # Legacy fallback logic (when playbook is wait/flex)
        if at._scenario._consecutive_races >= 3:
            if energy < 30:
                print(f"=> FINAL ACTION: REST (3+ consecutive races, energy {energy}%)")
            else:
                print(f"=> FINAL ACTION: TRAIN (3+ consecutive races)")
            return

        pre_summer = (35, 36, 59, 60)
        if at._current_turn in pre_summer and energy < 80:
            if race_action and has_energy_items:
                print(f"=> FINAL ACTION: RACE — {race_action.reason} (pre-summer w/ energy items)")
            elif race_action:
                print(f"=> FINAL ACTION: REST (pre-summer, no energy items, skip race)")
            else:
                print(f"=> FINAL ACTION: REST (pre-summer, energy {energy}%)")
            return

        if race_action:
            print(f"=> FINAL ACTION: RACE — {race_action.reason}")
            return

        if energy < 30:
            print(f"=> FINAL ACTION: REST (low energy {energy}%)")
        elif at._current_turn < 36 and energy < 50:
            print(f"=> FINAL ACTION: REST (bond phase, low energy {energy}%)")
        else:
            print(f"=> FINAL ACTION: TRAIN")

    # ── Phase 4: Tile scan (tap Training, preview all tiles, score, then back out) ──

    # Auto-scan when we'd train (or playbook says flex/train)
    would_train = (
        (at._playbook_engine and pb_action.action_type.value in ("wait", "train"))
        or (not at._playbook_engine and energy >= 30)
    )
    if not would_train:
        return

    print("\n--- Scanning training tiles ---")
    ch.tap(*at.BTN_TRAINING, delay=2.0)
    img = ch.screenshot("dry_train_entry")
    train_screen = at.detect_screen(img)
    if train_screen != "training":
        print(f"Not on training screen ({train_screen}), aborting scan")
        return

    import numpy as np
    from uma_trainer.perception.pixel_analysis import read_bond_levels
    from uma_trainer.types import StatType, TrainingTile

    tiles = []
    pre_gains = at._ocr_training_gains(img)
    pre_raised_tile = None
    if pre_gains:
        banner_text = at.ocr_region(img, 0, 280, 540, 350, save_path="/tmp/train_banner.png")
        for t, c in banner_text:
            tl = t.strip().lower()
            for tn in at.TRAINING_TILES:
                if tn.lower() in tl:
                    pre_raised_tile = tn
                    break
            if pre_raised_tile:
                break
        if pre_raised_tile:
            print(f"  Pre-raised tile: {pre_raised_tile}")

    # Detect hint badges from tile buttons on the initial screenshot
    initial_rgb = np.array(img.convert("RGB"))
    initial_bgr = initial_rgb[:, :, ::-1].copy()
    tile_hints = at._detect_tile_hints(initial_bgr)

    for tile_name, (tx, ty) in at.TRAINING_TILES.items():
        if tile_name == pre_raised_tile:
            tile_img = img
            gains = pre_gains
        else:
            ch.tap(tx, ty, delay=1)
            tile_img = ch.screenshot(f"dry_preview_{tile_name.lower()}")
            screen_check = at.detect_screen(tile_img)
            if screen_check != "training":
                print(f"  {tile_name}: interrupted by {screen_check}")
                return
            gains = at._ocr_training_gains(tile_img)

        fail_rate = at._ocr_failure_rate(tile_img)
        n_cards = at.count_portraits(tile_img)
        frame_rgb = np.array(tile_img.convert("RGB"))
        frame_bgr = frame_rgb[:, :, ::-1].copy()
        bond_levels = read_bond_levels(frame_bgr)
        if len(bond_levels) < n_cards:
            bond_levels.extend([80] * (n_cards - len(bond_levels)))
        bond_levels = bond_levels[:n_cards]

        card_ids = at._card_tracker.identify_cards(frame_bgr, n_cards, bond_levels)
        has_hint = tile_hints.get(tile_name, False)

        stat_type = StatType(tile_name.lower())
        tile = TrainingTile(
            stat_type=stat_type,
            tap_coords=(tx, ty),
            stat_gains={k.lower(): v for k, v in gains.items()},
            support_cards=card_ids,
            bond_levels=bond_levels,
            has_hint=has_hint,
        )
        tiles.append(tile)

        hint_str = " HINT" if has_hint else ""
        gains_str = ", ".join(f"{k}+{v}" for k, v in sorted(gains.items()))
        cards_str = ", ".join(card_ids) if card_ids else "none"
        bonds_str = f" bonds={bond_levels}" if bond_levels else ""
        fail_str = f" fail={fail_rate}%" if fail_rate is not None else " fail=?"
        print(f"  {tile_name}: {gains_str} | cards=[{cards_str}]{bonds_str}{hint_str}{fail_str}")

    # Score tiles
    state = at.build_game_state(tile_img, "training", energy=energy)
    state.training_tiles = tiles
    state.all_bonds_maxed = at._card_tracker.all_bonds_maxed()

    if at._card_tracker.card_count > 0:
        print(f"\nBond tracker: {at._card_tracker.summary()}")

    scored = at._scorer.score_tiles(state)
    print(f"\n--- Tile scores (best first) ---")
    for tile, score in scored:
        cards_str = ", ".join(tile.support_cards) if tile.support_cards else "none"
        print(f"  {tile.stat_type.value:8s}: {score:6.1f}  cards=[{cards_str}]  gains={dict(tile.stat_gains)}")

    action = at._scorer.best_action(state)
    best_score = scored[0][1] if scored else 0
    print(f"\n-> Would {action.action_type.value}: score={best_score:.1f}, stat={scored[0][0].stat_type.value if scored else '?'}")

    # Whistle check (mirrors handle_training summer/TS Climax logic)
    summer_turns = set(range(37, 41)) | set(range(61, 65))
    is_whistle_turn = at._current_turn in summer_turns or at._current_turn >= 72
    whistle_count = inv.get("reset_whistle", 0)
    if is_whistle_turn and best_score < at.WHISTLE_THRESHOLD and whistle_count > 0:
        phase = "TS CLIMAX" if at._current_turn >= 72 else "SUMMER CAMP"
        print(f"-> Would WHISTLE ({phase}): best score {best_score:.1f} < {at.WHISTLE_THRESHOLD}, {whistle_count} whistles left")
    elif is_whistle_turn and best_score >= at.WHISTLE_THRESHOLD:
        print(f"   Whistle not needed: best score {best_score:.1f} >= {at.WHISTLE_THRESHOLD}")
    elif is_whistle_turn and whistle_count == 0:
        print(f"   WARNING: would whistle (score {best_score:.1f} < {at.WHISTLE_THRESHOLD}) but no whistles in inventory!")

    # Back out to career home
    ch.tap(80, 1855, delay=1.5)
    print("(Backed out to career home)")


gather_and_decide()
