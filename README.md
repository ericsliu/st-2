# Uma Trainer

An autonomous bot that plays **Uma Musume: Pretty Derby** (Global/English version) on a MacBook Pro M1. It executes full Career Mode training runs with minimal human supervision.

> **Disclaimer**: Automation may violate the game's Terms of Service. This project is for educational and research purposes. Users accept all risk.

---

## Architecture

```
Screen Capture → YOLO Object Detection → OCR → State Assembly → Decision Engine → ADB Input
```

**Three-tier decision engine:**
1. **Tier 1 (Rule-based scorer)** — handles ~90% of decisions in <1ms
2. **Tier 2 (Local LLM via Ollama)** — handles ambiguous events (~15-20 tok/s on M1)
3. **Tier 3 (Claude API)** — high-value fallback (2–5 calls/day, <$0.10/day)

---

## System Requirements

- macOS with Apple Silicon (M1/M2/M3)
- 16 GB RAM recommended (8 GB minimum — see Memory section)
- Python 3.11+
- [MuMuPlayer](https://www.mumuplayer.com/) — Android emulator for macOS Apple Silicon
- [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) (for `adb`)
- Uma Musume: Pretty Derby (Global) installed in MuMuPlayer

---

## Installation

### 1. Clone the repository

```bash
git clone <repo-url>
cd st-2
```

### 2. Create a Python virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

For development (includes testing tools):
```bash
pip install -r requirements-dev.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in your ANTHROPIC_API_KEY
```

### 5. Extract the game's master database

The game ships with `master.mdb`, an SQLite database containing all static game data (events, skills, characters, races, support cards). Extracting it requires temporarily enabling root on MuMu:

1. **Close Uma Musume** (it won't start while rooted)
2. **Enable root** in MuMu: Settings → Other → Root Permission
3. **Pull the database**:
   ```bash
   adb connect 127.0.0.1:5555
   adb pull /data/data/com.cygames.umamusume/files/master/master.mdb data/master.mdb
   ```
4. **Disable root** in MuMu settings and restart the emulator
5. **Verify** (optional):
   ```bash
   python scripts/pull_master_mdb.py --info
   ```

> **Re-extract after game updates** — Cygames patches `master.mdb` with new events, skills, and characters. Re-pull it after major game updates to keep the knowledge base current.

### 6. Import the knowledge base

Loads the bundled event, skill, support card, and race calendar data into SQLite:

```bash
python main.py import-kb
```

---

## Setup: MuMuPlayer + ADB

1. **Install MuMuPlayer** from https://www.mumuplayer.com/mac/

2. **Enable ADB in MuMuPlayer**:
   - Settings → Other → Enable ADB debugging

3. **Install Android Platform Tools** (if not already):
   ```bash
   brew install android-platform-tools
   ```

4. **Connect ADB**:
   ```bash
   adb connect 127.0.0.1:7555   # Default MuMuPlayer ADB port
   adb devices                   # Should show the emulator
   ```

5. **Install the game**: Launch Uma Musume: Pretty Derby inside MuMuPlayer and log in.

---

## Configuration

Edit `config/default.yaml` to tune behavior:

```yaml
capture:
  backend: scrcpy        # Use 'screencapturekit' for window capture without ADB
  fps_decision: 1.5      # Frames/sec during active turns

scorer:
  stat_weights:
    speed: 1.2           # Increase to prioritize speed training
    stamina: 1.0
    # ...
  rest_energy_threshold: 20  # Rest if energy drops below this

llm:
  claude_daily_limit: 5  # Max Claude API calls per day
```

### Training presets

Pre-built stat weight presets are in `data/presets/`. Apply one with:
```bash
python main.py run --preset speed_stam_ura
```

---

## Running the Bot

Mumuplayer must be running for the following to work.

### Basic run (with web dashboard)

```bash
python main.py run
```

Open the dashboard at http://127.0.0.1:8080

### Headless run

```bash
python main.py run --headless
```

### With a specific config file

```bash
python main.py run --config config/my_config.yaml
```

### Dashboard only (no bot running)

```bash
python main.py dashboard
```

---

## YOLO Model Setup

The bot runs in **stub mode** (no object detection) until you train the YOLO model. Core functionality still works via fallback fixed-region OCR, but detection accuracy is lower.

### Step 1: Collect screenshots

With the game running in MuMuPlayer:
```bash
python main.py collect --output datasets/images --interval 5
```
Collect 500–1000 screenshots across all screen types.

### Step 2: Annotate in Label Studio

```bash
pip install label-studio
label-studio start
```
- Create a new project, import the screenshots
- Annotate the 50 classes defined in `uma_trainer/perception/class_map.py`
- Export in YOLO format to `datasets/`

See `datasets/README.md` for detailed annotation instructions.

### Step 3: Train

```bash
python scripts/train_yolo.py --epochs 100 --export-coreml
```

The trained CoreML model is automatically placed at `models/uma_yolo.mlpackage`.

---

## Local LLM Setup (Optional — Tier 2 decisions)

Install [Ollama](https://ollama.com/) and pull the recommended model:

```bash
# Install Ollama (macOS)
brew install ollama

# Start Ollama server
ollama serve

# Pull the model
ollama pull phi4-mini:q4_K_M     # ~2.5 GB, recommended
# or
ollama pull llama3.2:3b           # ~2 GB, alternative
```

If Ollama is not running, the bot skips Tier 2 and falls back to Claude API.

---

## Claude API Setup (Optional — Tier 3 decisions)

Add your key to `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
```

The bot uses Claude only for unknown events not in the knowledge base. Default limit: 5 calls/day.

---

## Memory Budget (M1 16 GB)

| Component | RAM |
|-----------|-----|
| MuMuPlayer + Game | ~3–4 GB |
| YOLO nano (CoreML) | ~50–100 MB |
| Apple Vision OCR | ~50 MB |
| Phi-4-mini Q4 (Ollama) | ~2.5 GB |
| Python bot process | ~200–400 MB |
| macOS overhead | ~3–4 GB |
| **Total** | **~9.5–12 GB** |

**On 8 GB M1**: Set `llm.local_model: ""` in config to disable local LLM. The bot will use the Claude API for all AI decisions (higher API cost).

---

## Knowledge Base

The knowledge base (`data/uma_trainer.db`) stores:
- **Events**: ~2,000 event choices (generic + character-specific)
- **Skills**: ~800 skills with priorities
- **Support cards**: ~400 cards with tier ratings
- **Race calendar**: ~120 races with grades and distances

Bundled sample data is in `data/`. To expand it:

1. Add JSON files following the schemas in `data/events/generic_events.json`, `data/skills.json`, etc.
2. Re-import: `python main.py import-kb`

Community data sources: [GameTora](https://gametora.com/umamusume), [Game8](https://game8.jp/umamusume)

---

## Running Tests

```bash
pytest tests/ -v
```

Run a specific test file:
```bash
pytest tests/test_scorer.py -v
pytest tests/test_event_lookup.py -v
pytest tests/test_fsm.py -v
```

---

## Project Structure

```
uma_trainer/
├── capture/          # Screen capture (ADB screencap or macOS Quartz)
├── perception/       # YOLO detection, Apple Vision OCR, state assembly
├── decision/         # Scoring engine, event handler, skill buyer
├── action/           # ADB input injection with human-like timing
├── knowledge/        # SQLite DB, event/skill/card lookups
├── llm/              # Ollama client, Claude API client, cache
├── web/              # FastAPI dashboard
└── fsm/              # Finite state machine orchestration
config/               # YAML configuration files
data/                 # Knowledge base JSON source files
datasets/             # YOLO training images + labels (gitignored)
models/               # Trained model weights (gitignored)
scripts/              # Training, data collection, import utilities
tests/                # Pytest test suite
main.py               # CLI entry point
```

---

## Development Phases

| Phase | Status | Description |
|-------|--------|-------------|
| Foundation | ✅ Code complete | Capture, perception stubs, KB, action injection |
| Core Loop | 🔲 Needs YOLO model | YOLO training, state assembly, end-to-end run |
| Intelligence | 🔲 Needs data | LLM integration, event matching, skill logic |
| Polish | 🔲 | Dashboard, scraper, performance tuning |

**Next steps to get the bot running end-to-end:**
1. Set up MuMuPlayer + ADB (see above)
2. Collect 500+ screenshots with `python main.py collect`
3. Annotate in Label Studio and train YOLO
4. Run `python main.py run` with supervised monitoring

---

## Troubleshooting

**ADB not connecting:**
```bash
adb kill-server && adb start-server
adb connect 127.0.0.1:7555
```

**YOLO model warnings at startup:**
Normal — the bot runs in stub mode until you train the model.

**Claude API budget exceeded:**
Increase `llm.claude_daily_limit` in `config/default.yaml`.

**High memory usage:**
Disable local LLM: set `llm.local_model: ""` in config.

**Bot stuck in error recovery:**
Check the dashboard at http://127.0.0.1:8080. Click Resume after investigating.
