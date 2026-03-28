# Uma Trainer — Autonomous Uma Musume: Pretty Derby Bot

## Development Rules

**Python execution**: ALWAYS use `.venv/bin/python scripts/some_script.py`. NEVER use bare `python`, `python3`, or `python -c "..."` inline commands. When you need to run any Python code, no matter how small, write it to a script file first and run it with `.venv/bin/python`.

**Bash commands**: Never chain commands with `&&` or `;`. Never use multiline commands. One command per Bash call.

## Project Overview

An autonomous bot that plays Uma Musume: Pretty Derby (Global/English version, launched June 26 2025) on a MacBook Pro M1. It executes full Career Mode training runs with minimal human supervision, making strategic decisions about stat allocation, skill purchases, race entries, and event choices.

The system runs entirely on-device except for optional Claude API calls for high-value research queries.

## Architecture

Perception–reasoning–action loop: **Screen Capture → YOLO Object Detection → OCR → State Assembly → Decision Engine → Input Injection**

### Game Client

- **Target**: Android version via MuMuPlayer (macOS Android emulator for Apple Silicon) + scrcpy
- **Why Android, not Steam**: The Steam client uses CrackProof kernel-level anti-cheat. The Android client does not. CrossOver/Proton on macOS crashes during races anyway.
- **Input**: ADB tap/swipe commands via `adb shell input tap <x> <y>`
- **Screen capture**: scrcpy screenshot API or macOS ScreenCaptureKit on the mirrored window
- Frame rate: 1–2 FPS during decision points, 0.2 FPS during races/cutscenes

### Perception Pipeline

**Object Detection (YOLO)**
- Model: YOLOv8n or YOLO11n (nano for speed on M1)
- Export: CoreML for Metal GPU acceleration
- Training data: 500–1000 labeled screenshots from the English global client, annotated via Label Studio
- ~50 object classes: buttons, stat indicators, support card icons, rainbow/gold training indicators, mood icons, screen-state identifiers
- Target: <50ms inference per frame on M1 GPU
- Prior art: [Umaplay](https://github.com/Magody/Umaplay) demonstrates this working with 40+ classes on 300+ images

**OCR**
- Primary: Apple Vision framework via PyObjC (`VNRecognizeTextRequest`) — native, uses Neural Engine, zero GPU cost
- Fallback: EasyOCR (pure Python, works on M1 CPU/MPS)
- For numeric stats specifically: consider template matching or lightweight CNN (game uses consistent fonts)
- PaddleOCR has historically poor Apple Silicon support — avoid unless the PaddlePaddle 3.2+ ARM builds stabilize

**State Assembly** — combines YOLO + OCR into structured game state:
- `current_screen` (enum: training, event, race, skill_shop, etc.)
- `trainee_stats` (Speed, Stamina, Power, Guts, Wit)
- `energy_level` (0–100)
- `mood` (enum)
- `training_tiles[5]` (stat type, support cards present, rainbow, gold, hint flags)
- `bond_levels` (per support card)
- `career_goals` (races + fan requirements)
- `current_turn / max_turns`
- `event_text + choices` (when on event screen)
- `available_skills + costs` (when on skill screen)

### Decision Engine (Three-Tier)

**Tier 1: Scoring System (rule-based, handles ~90% of decisions)**
Each training tile scored on: stat alignment with preset weights, support card stacking count, rainbow/gold indicators, director presence, hint icons, energy cost penalty, bond-building priority (early run). Highest score wins. Sub-millisecond, deterministic, highly tunable via presets.

**Tier 2: Local LLM (ambiguous decisions)**
- Model: Phi-4-mini 3.8B Q4_K_M (~2.5 GB RAM) or Llama 3.2 3B
- Runtime: Ollama or MLX (MLX is 30–50% faster on Apple Silicon)
- Use cases: unknown events (not in KB), skill build planning, unexpected screen recovery
- ~15–20 tok/s on M1, sufficient for game-speed decisions

**Tier 3: Claude API (research, low-frequency)**
- Model: claude-sonnet-4-6
- Use cases: support card evaluations, meta strategy, high-stakes unknown events, post-run analysis, knowledge base structuring
- Expected: 2–5 calls/day, <$0.10/day
- Always request structured JSON output for machine parsing
- Cache responses by input hash

### Input Injection

- ADB via scrcpy for Android emulator
- Human-like variance: ±5–15px random offset, 200–800ms random delay between taps, occasional 1–3s pauses, randomized breaks between runs (5–30min)
- Never acts during loading screens, never faster than human speed

## Knowledge Base

SQLite database + JSON files, stored locally. Critical for minimizing AI calls.

| Dataset | Source | ~Size |
|---------|--------|-------|
| Event choices (character + generic) | GameTora, Game8, wikis | ~2,000 events |
| Skill database | In-game + wiki | ~800 skills |
| Support card database | GameTora tier lists | ~400 cards |
| Character aptitudes | In-game + wiki | ~80 characters |
| Race calendar | In-game data | ~120 races |
| Training presets | Community-optimized | ~30 presets |

**Event matching**: exact hash → fuzzy match (>85% similarity via rapidfuzz) → local LLM → Claude API fallback

## Resource Budget (M1 16GB)

| Component | Memory |
|-----------|--------|
| Android Emulator + Game | ~3–4 GB |
| YOLO nano (CoreML) | ~50–100 MB |
| OCR (Apple Vision) | ~50 MB (ANE, not GPU) |
| Local LLM (Phi-4-mini Q4) | ~2.5 GB |
| Python runtime + bot | ~200–400 MB |
| macOS overhead | ~3–4 GB |
| **Total** | **~9.5–12 GB** |

If on 8GB M1: drop local LLM, use Claude API for all AI decisions.

## Project Structure

```
uma_trainer/
├── capture/          # Screen capture, frame preprocessing
├── perception/       # YOLO detection, OCR, state assembly
├── decision/         # Scoring engine, LLM integration, strategy
├── action/           # Input injection (ADB, PyAutoGUI), action sequences
├── knowledge/        # SQLite DB, JSON loaders, event/skill/card lookup
├── llm/              # Local LLM client (Ollama/MLX), Claude API client
├── web/              # FastAPI dashboard for monitoring/config
├── fsm/              # Finite state machine for game flow control
models/               # YOLO weights, OCR models, LLM configs
data/                 # Knowledge base JSON files, training presets
datasets/             # YOLO training data (screenshots + labels)
scripts/              # Data scrapers, model training, utilities
tests/                # Unit and integration tests
```

## Key Dependencies

- Python 3.11+
- `ultralytics` — YOLO training/inference
- `coremltools` — CoreML export for Metal
- `pyobjc-framework-Vision` — Apple Vision OCR
- `easyocr` — fallback OCR
- `ollama` (Python client) — local LLM
- `anthropic` — Claude API
- `fastapi` + `uvicorn` — web dashboard
- `adb-shell` — ADB for Android input
- `Pillow`, `numpy` — image processing
- `rapidfuzz` — fuzzy string matching for event lookup
- `sqlite3` (stdlib) — knowledge base

## Game Context (Uma Musume Career Mode)

A Career run is the core loop (~20–40 min each). Player selects: 1 Trainee, 2 Legacy Umamusume, 6 Support Cards. The run spans ~72 turns across 3 in-game years.

Each turn the player picks one action: Train (stat), Rest, Infirmary, Go Out, or Race. Random events (2–3 choices) fire between turns. Career goals require winning specific races and hitting fan count thresholds. Winning all goals qualifies for the URA Finale (or scenario equivalent).

Key stats: Speed, Stamina, Power, Guts, Wit. Key resources: Energy (0–100), Mood, Support Card Bond gauges. Scenarios available: URA Finale, Unity Cup, Trackblazer (added March 2026).

## Anti-Cheat Notes

- Steam version uses CrackProof (kernel-level) — **we avoid this entirely by using the Android client**
- Bot operates purely via screen capture + ADB input — no memory reading, no packet manipulation
- All game logic is server-side; client is display-only
- Human-like input patterns + session limits to reduce behavioral detection risk
- **Automation may violate ToS. Educational/research purposes. Users accept all risk.**

## Development Phases

1. **Foundation (Weeks 1–3)**: Emulator setup, screen capture pipeline, YOLO dataset collection, OCR pipeline, initial knowledge base
2. **Core Loop (Weeks 4–6)**: Train YOLO, state assembly, scoring engine, ADB input, FSM, first supervised end-to-end run
3. **Intelligence (Weeks 7–9)**: Local LLM integration, Claude API client, event matching, skill purchase logic, error recovery, YOLO retrain
4. **Polish (Weeks 10–12)**: Web dashboard, daily task automation, session management, KB scraper, performance tuning, docs

## Open Questions

- MuMuPlayer stability under 6+ hour sustained load on M1 16GB
- YOLO nano accuracy on subtle indicators (faint hint icons) — may need small variant
- Phi-4-mini quality for game-specific reasoning — may need fine-tuning on Uma Musume Q&A data
- OCR accuracy on decorative/stylized game fonts
- Rate of new content additions to the global client (affects KB maintenance burden)
