# Umaplay Comparison & Ideas

Comparison of our bot (Uma Trainer) against [Umaplay](https://github.com/Magody/Umaplay), with notes on what's worth pulling and what we already cover.

## Where We're Already Ahead

- **LLM integration**: Umaplay has none. Our 3-tier system (rules -> local LLM -> Claude API) handles unknown events and ambiguous decisions better.
- **Knowledge base depth**: 2000+ events with 6-tier fuzzy matching + master.mdb, vs their simpler event sets.
- **Apple Vision OCR**: Faster and more accurate on M1 than PaddleOCR (which they use and has poor Apple Silicon support).
- **Trackblazer scenario**: They don't have it (newer scenario).
- **Config via Pydantic**: Type-safe config vs their raw JSON.

## Where Umaplay Is Ahead

- **YOLO in production**: Trained model with 40+ classes deployed. We have class definitions but no trained weights (stub mode).
- **Platform support**: Steam + Android (BlueStacks, scrcpy, ADB, physical phones). We're Android-only.
- **Web UI**: Full Vue.js SPA with config editing, preset management, self-update via git. Ours is minimal FastAPI.
- **URA + Unity Cup scenarios**: Both implemented. We only have Trackblazer.
- **Daily task automation**: F7/F8/F9 hotkeys for team trials, daily races, roulette. We only do career runs.
- **Claw game / mini-game automation**: 29KB of claw game logic alone.

## Ideas Considered

### 1. Remote Inference Server
**Verdict: Skip.** Umaplay offloads YOLO/OCR to a separate FastAPI server. Our Apple Vision OCR is already fast on the Neural Engine, and performance is good. Only relevant if we ever need multi-instance or 8GB M1 support.

### 2. Support Card Multi-Algorithm Matching
**Verdict: Consider later, high effort.** Umaplay uses weighted scoring: template matching (48%) + perceptual hash (17%) + histogram (35%) + edge detection (25%), with portrait masking. More robust than our pixel color analysis. Worth revisiting when support card detection becomes a bottleneck.

### 3. Skill Memory / Persistence
**Verdict: Worth doing via Full Stats screen.** Umaplay persists which skills have been purchased across turns to avoid re-buying. We could pull this from the Full Stats screen, but we don't have support for parsing it yet.

### 4. Multimodal Event Matching (Image Channel)
**Verdict: Skip for now.** Umaplay uses `0.82 * text_similarity + 0.11 * image_similarity + hint_bonuses`. Given our OCR accuracy, better to double down on providing more complete event text for the text-based matcher.

### 5. Friendship Bar Analyzer
**Verdict: Already covered.** We have color-based bond analysis in `pixel_analysis.py:339-469` with blue/green/orange classification and segment counting. Comparable to Umaplay's HSV approach.

### 6. Stat Monotonicity Guards
**Verdict: Nice but treats symptoms.** The real issue is misreading the stats page itself (dividers misread as 1s). Fixing OCR at the source is better than clamping after the fact. See "Stat Divider Fix" below.

### 7. Button State Classifier (ML)
**Verdict: Consider later.** Umaplay uses LogisticRegression (25D HSV/LAB features) for active/inactive button detection. More robust than fixed pixel checks. Low effort if we collect training data.

### 8. Waiter / Polling Utility
**Verdict: Worth doing.** Umaplay has a unified `Waiter` with `click_when()`, `seen()`, `try_click_once()` — cascade detection with fast path and OCR disambiguation. Our action sequences are more ad-hoc. Would clean up screen transition handling.

### 9. Navigation Agent for Dailies
**Verdict: Good quality-of-life, later.** Separate from career loop. Automates team trials, daily races, roulette.

### 10. PAL Memory
**Verdict: Future, for URA/Unity.** Tracks special ability availability and cooldowns.

## Data Worth Importing from Umaplay

### Event Choice Outcomes
Umaplay's `events.json` has ~100 support card events with quantified reward deltas per choice option (e.g., speed+10, energy+15, mood+1) and a `default_preference` field indicating the best choice. Our `generic_events.json` has 61 events with text descriptions of effects but not structured numeric values.

**Plan**: Enrich our event DB with their numeric reward data. Match events across JP→EN by event structure/choice count since text won't match directly. Their reward values are version-independent game mechanics — the stat deltas are the same in Global. Import into our SQLite `events` table alongside existing records.

**Caveat**: Their data is Japanese version. Matching requires either skill ID crossref or manual mapping of event titles.

### Skill Grade Ratings
Umaplay has 484 skills with effectiveness ratings using ◎ (excellent), ○ (good), × (poor) grade symbols. We have 535 skills with `grade_value` (numeric) but no effectiveness tier.

**Plan**: Match by `skill_id` (shared between JP and Global) to pull their ◎/○/× ratings into our DB. This improves skill purchase prioritization — the scorer can prefer ◎ skills over × skills at the same SP cost.

**Important**: Our existing `grade_value` data takes priority. Only backfill Umaplay ratings for skills where we don't already have our own rating. Our data is Global-specific and more authoritative for our use case.

### Skill Matching Constraints
**Verdict: Skip.** Umaplay has `skill_matching_overrides.json` with require/forbid token rules for mutually exclusive skills. Not needed for our use case — we're targeting parent runs where having more skills is fine, not competitive min-maxing where conflicts matter.

## Stat Divider OCR Fix

### Problem
Thin vertical dark-gray dividers (~RGB 73,72,73) between stat columns on the training screen get misread as `1` by Apple Vision OCR during the bulk stat parse in `assembler.py:_parse_stats_bulk()`.

### Approach
Before passing the bulk crop to OCR, blend divider columns into the local background color. For each known divider x-position (+-3px), replace pixel values with the average of pixels ~5-8px to the left and right. This preserves the background gradient naturally.

### Why Blend, Not Erase
A flat fill creates a hard edge against the gradient background which could itself confuse OCR. Blending from neighbors preserves the natural gradient.

### Pseudocode
```python
DIVIDER_XS = [208, 368, 528, 693]  # needs calibration from screenshot
HALF_W = 3  # divider half-width to overwrite
SAMPLE_OFFSET = 8  # how far left/right to sample background

crop = frame[y1:y2, x1:x2].copy()
for dx in DIVIDER_XS:
    rx = dx - x1
    left_bg = crop[:, rx - SAMPLE_OFFSET:rx - SAMPLE_OFFSET + 2].mean(axis=1, keepdims=True)
    right_bg = crop[:, rx + SAMPLE_OFFSET:rx + SAMPLE_OFFSET + 2].mean(axis=1, keepdims=True)
    blend = ((left_bg + right_bg) / 2).astype(np.uint8)
    crop[:, rx - HALF_W:rx + HALF_W + 1] = blend
```

### Details
- **Performance**: Sub-millisecond. Numpy slice operations on a ~900x35px crop.
- **Divider x-positions**: Need calibrating from a real screenshot. They fall in the gaps between stat columns. See `regions.py:261-278` for column boundaries.
- **Location**: `uma_trainer/perception/assembler.py`, `_parse_stats_bulk()` (line ~551). The bulk crop spans the full stat row.
- **OCR parallelization note**: Apple Vision runs on the Neural Engine (single accelerator, can't parallelize). The PyObjC bridge also isn't thread-safe. Bulk OCR (one big crop) is faster than per-stat crops due to per-call overhead. So keeping the bulk path and fixing the dividers is the right call.

## Bond Tracking

Known to be buggy in real-world testing. The bond bar analysis (`pixel_analysis.py:379-469`) uses segment counting + color classification (blue/green/orange). Issues likely stem from edge cases in segment fill detection or color boundary thresholds. Needs more real-life testing data to diagnose.

## Priority Order

1. Stat divider OCR fix (high impact, low effort)
2. Waiter/polling utility (medium impact, medium effort)
3. Skill memory via Full Stats screen (medium impact, medium effort)
4. Navigation agent for dailies (quality-of-life, medium effort)
5. Support card multi-algorithm matching (high effort, revisit when needed)
6. Button state classifier (low effort if we have data)
