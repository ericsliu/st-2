# Uma Trainer — CLAUDE.md

## CRITICAL RULES — Read These First

**Python execution**: ALWAYS use `.venv/bin/python scripts/some_script.py`. NEVER use bare `python`, `python3`, or `python -c "..."`. When you need to run ANY Python code, no matter how small, write it to a script file first and run it with `.venv/bin/python`.

**Bash commands**: Never chain commands with `&&` or `;`. Never use multiline commands. One command per Bash call.

**No blind taps**: NEVER tap without confirmed screen state. If you don't know what screen the game is on, screenshot first.

**ADB**: Must manually run `adb connect 127.0.0.1:5555` before any scripts that interact with the emulator.

**MuMu cannot be rooted**: The emulator does not support root. Never suggest root-dependent approaches.

**Screenshot coordinates are misleading**: When you view a 1080x1920 screenshot, it is displayed scaled down. Do NOT estimate tap coordinates by visually inspecting screenshots. Instead use percentage-based estimation (% across × 1080, % down × 1920) and verify with a single ADB tap. Cross-reference with known-good coordinates in `data/screen_coordinates.json`.

---

## How to Run Things

### Run one game turn
```bash
.venv/bin/python scripts/run_one.py
```

### Dry run (screenshot + decide, no taps)
```bash
.venv/bin/python scripts/dry_run.py
```

### Full career loop
```bash
.venv/bin/python scripts/run_career.py
```

### Run tests
```bash
.venv/bin/pytest tests/ -v
```

Do NOT use `python -m pytest`. Do NOT create new test files unless explicitly asked — tests already exist in `tests/`.

---

## Architecture (Actual, Not Aspirational)

The bot does NOT use YOLO, local LLMs, or a web dashboard. Those exist in the codebase as scaffolding but are not wired into the main loop.

**What actually runs:**
```
ADB Screenshot → Apple Vision OCR → Screen Detection → State Assembly → Rule-Based Scorer → ADB Tap
```

### Core file: `scripts/auto_turn.py`

This is the monolith. Nearly all bot logic lives here:
- `screenshot()` — ADB screencap
- `detect_screen(img)` — OCR-based screen identification
- `build_game_state(img, screen)` — assembles stats, energy, mood, turn
- `run_one_turn()` — main loop: detects screen, decides action, executes, loops through intermediate screens
- `_handle_career_home()` — the big decision function for normal training turns
- `handle_skill_shop()` — skill scanning and purchasing
- `_use_training_items()` — opens Training Items bag and uses items
- `_detect_active_effects()` — taps effect indicator icon, reads popup via OCR
- `SKILL_PRIORITY` dict — skill purchase rankings
- `_INTERMEDIATE_RESULTS` set — screens that `run_one_turn()` loops through automatically

### OCR: `scripts/ocr_util.py`

Apple Vision OCR wrapper. Provides `ocr_image()`, `ocr_region()`, `ocr_full_screen()`. This is fast and accurate — do not replace with EasyOCR.

### Decision logic: `uma_trainer/decision/`

- `scorer.py` — scores training tiles based on stat weights from runspec
- `race_selector.py` — picks races based on aptitude, goals, calendar
- `shop_manager.py` — tracks inventory, active effects, purchase decisions

### Configuration

- `data/runspecs/parent_balanced_v1.yaml` — stat weight targets and thresholds
- `data/screen_coordinates.json` — all button coordinates (1080x1920 portrait)
- `data/race_calendar.json` — race schedule with grades, distances, turns
- `data/inventory.yaml` — current item counts (auto-updated at runtime)

---

## Game State Flow

Each `run_one.py` invocation starts a fresh Python process. Module-level globals (like `_game_state`, `_shop_manager`, `_current_turn`) reset every time.

`run_one_turn()` loops through intermediate screens (race results, popups, cutscenes) in a single process so that `_game_state` stays alive within one turn. It returns a result string indicating what happened.

There is NO disk persistence of game state between turns. If the bot is stopped, the next `run_one.py` call rebuilds state from scratch via OCR.

---

## Game Strategy References

Read these files before making game logic changes:
- `data/advice/general.md` — universal mechanics: energy, mood, racing, skills, items
- `data/advice/trackblazer.md` — Trackblazer-specific: grade points, shop tiers, TS Climax, phase flow
- `data/scenarios/trackblazer.yaml` — scenario config: turn ranges, grade point targets, shop settings

## Common Pitfalls — Don't Repeat These

1. **Don't create parallel implementations**. All bot logic is in `auto_turn.py`. The `uma_trainer/core/` and `uma_trainer/state/` modules are a WIP refactor (phases 3-4 pending) — don't duplicate logic there.

2. **Training Items button** is at (820, 1250), NOT where it visually appears in screenshots. Verified empirically.

3. **Active effect detection** works by tapping the effect indicator icon at (50, 650), reading the "Active Item Effects" popup via OCR, then closing it at (460, 1361). It does NOT work by OCR-scanning the home screen — that produces false positives from TS Climax UI text.

4. **Scroll coordinates** for skill shop: swipe from y=750-1350 to stay within the scrollable list area. Previous y=700-1200 range hit the Confirm button.

---

## Emulator Details

- MuMuPlayer in portrait mode, 1080x1920 resolution
- ADB at 127.0.0.1:5555
- Root is disabled (Uma Musume blocks rooted devices)
- `tap(x, y)` calls `adb shell input tap` with small random jitter
- `swipe(x1, y1, x2, y2)` for scrolling
