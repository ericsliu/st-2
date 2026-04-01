# Uma Trainer

An autonomous bot that plays **Uma Musume: Pretty Derby** (Global/English version) on a MacBook Pro M1. It executes full Career Mode training runs (Trackblazer scenario) with minimal human supervision.

> **Disclaimer**: Automation may violate the game's Terms of Service. This project is for educational and research purposes. Users accept all risk.

---

## How It Works

```
Screen Capture (ADB) → Apple Vision OCR → State Assembly → Rule-Based Scorer → ADB Input
```

The bot runs a perception-reasoning-action loop at ~1 FPS. It screenshots the emulator via ADB, reads text/numbers with Apple Vision OCR, assembles game state, scores possible actions with a tunable rule-based engine, and injects taps via `adb shell input tap`.

No YOLO model or local LLM is required. The bot operates entirely on OCR + rule-based logic.

---

## System Requirements

- macOS with Apple Silicon (M1/M2/M3)
- Python 3.11+
- [MuMuPlayer](https://www.mumuplayer.com/mac/) (Android emulator for Apple Silicon)
- [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) (`adb`)
- Uma Musume: Pretty Derby (Global) installed in MuMuPlayer

---

## Installation

```bash
git clone <repo-url>
cd st-2
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For development (pytest, ruff, mypy):
```bash
pip install -r requirements-dev.txt
```

---

## Setup: MuMuPlayer + ADB

1. Install MuMuPlayer and set it to **portrait mode (1080x1920)**
2. Enable ADB debugging in MuMuPlayer: Settings > Other > Enable ADB
3. Install Uma Musume inside MuMuPlayer and log in
4. Connect ADB before every session:
   ```bash
   adb connect 127.0.0.1:5555
   adb devices  # Should show the emulator
   ```

---

## Running the Bot

**Always use `.venv/bin/python`** — never bare `python` or `python3`.

### Single turn (supervised)
```bash
.venv/bin/python scripts/run_one.py
```
Executes one full game turn, looping through intermediate screens (race results, popups, cutscenes) automatically.

### Dry run (no taps)
```bash
.venv/bin/python scripts/dry_run.py
```
Screenshots the current screen, runs the decision engine, and logs what it *would* do without touching the game.

### Full career
```bash
.venv/bin/python scripts/run_career.py
```
Loops `run_one_turn()` until the career ends. Logs each turn to a markdown file.

### Multiple turns
```bash
.venv/bin/python scripts/run_few_turns.py
```

---

## Configuration

### Runspec (stat weights + targets)

The bot's stat priorities are defined in `data/runspecs/parent_balanced_v1.yaml`:

```yaml
stat_targets:
  speed:
    minimum: 500
    target: 800
    excellent: 1000
    values: [1.2, 0.9, 0.6, 0.2]  # [below_min, min_to_target, target_to_excellent, above_excellent]
```

Each stat has four weight tiers based on current value relative to thresholds. The scorer multiplies training gains by these weights to rank actions.

### Skill purchase priority

Skills are ranked in `SKILL_PRIORITY` inside `scripts/auto_turn.py`. Higher number = buy first. Unknown skills (not in the list) are never purchased.

### Screen coordinates

All button positions are in `data/screen_coordinates.json`, calibrated for 1080x1920 portrait.

### Item usage

Shop purchasing and item usage are managed by `uma_trainer/decision/shop_manager.py`. Item tiers and purchase rules are configured there.

---

## Key Files

| File | Purpose |
|------|---------|
| `scripts/auto_turn.py` | Core bot logic: screen detection, OCR, state assembly, decision engine, action execution |
| `scripts/ocr_util.py` | Apple Vision OCR wrapper |
| `scripts/run_one.py` | Single-turn entry point |
| `scripts/run_career.py` | Full career loop |
| `scripts/dry_run.py` | Decision testing without taps |
| `data/screen_coordinates.json` | Button coordinates for all screens |
| `data/runspecs/parent_balanced_v1.yaml` | Stat weight configuration |
| `data/race_calendar.json` | Race schedule with grades, distances, turn numbers |
| `data/inventory.yaml` | Current item inventory (auto-updated) |
| `uma_trainer/decision/shop_manager.py` | Shop automation and item usage |
| `uma_trainer/decision/race_selector.py` | Race entry decisions |
| `uma_trainer/decision/scorer.py` | Training tile scoring engine |

---

## Project Structure

```
scripts/              # Bot entry points and utilities
uma_trainer/
├── action/           # ADB input injection
├── capture/          # Screen capture backends
├── core/             # Run context and turn execution (WIP refactor)
├── decision/         # Scorer, race selector, shop manager, skill buyer
├── fsm/              # Finite state machine for game flow
├── knowledge/        # SQLite knowledge base lookups
├── llm/              # Claude API client (low-frequency fallback)
├── perception/       # Screen detection and state assembly
├── scenario/         # Game scenario definitions
├── state/            # Game state providers (WIP refactor)
└── web/              # FastAPI dashboard (not currently used)
data/                 # Config, race calendar, coordinates, templates, runspecs
tests/                # Pytest test suite
```

---

## Running Tests

```bash
.venv/bin/pytest tests/ -v
```

---

## Game Context

The bot plays **Trackblazer** scenario Career Mode. A career spans ~72 turns across 3 in-game years. Each turn the bot picks one action: Train, Rest, Infirmary, Race, or Shop. Random events fire between turns.

Key mechanics the bot handles:
- **Training scoring**: Weighs stat gains, support card stacking, bond building, energy cost
- **Race selection**: Checks distance aptitude, prioritizes goal races, avoids consecutive race penalties
- **Shop automation**: Buys megaphones, energy drinks, cleats; uses items at optimal times
- **Summer camp**: Stockpiles megaphones, manages energy for boosted training
- **TS Climax**: Uses megaphones + energy drinks every training turn, cleats before races
- **Skill purchasing**: Priority-ranked skill buying with SP reserve management
- **Active effects**: Detects item buffs by tapping the effect indicator icons

---

## Troubleshooting

**ADB not connecting:**
```bash
adb kill-server && adb start-server
adb connect 127.0.0.1:5555
```

**Bot stuck on unknown screen:**
Run `dry_run.py` to see what the bot detects. Check if OCR is reading the screen correctly.

**Wrong button coordinates:**
Coordinates are calibrated for 1080x1920 portrait in MuMuPlayer. If your resolution differs, recalibrate using `scripts/calibrate_regions.py`.
