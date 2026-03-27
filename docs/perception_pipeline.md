# Perception Pipeline: Screen Identification & State Extraction

## Problem

The bot needs to know what screen it's on and extract structured data from it. Currently, we have the code infrastructure (ScreenIdentifier, StateAssembler, OCR engines) but lack the calibrated templates and regions to make it work reliably on the actual game.

Parent runs prioritize **reliability over accuracy** — it's better to correctly identify 95% of screens and make passable decisions than to perfectly OCR every stat but get stuck on an unknown screen.

## Screen Inventory

Based on actual screenshots taken from the Global client (1080x1920 portrait):

### Primary Screens (bot makes decisions here)

| Screen | Identifier | Bot Action | Key Data to Extract |
|--------|-----------|------------|-------------------|
| Home/action menu | "Career" title top-left, action buttons visible | Choose Train/Race/Rest/etc. | Turn counter, energy bar, mood, stats, Result Pts |
| Training tiles | "Training" title, 5 tile bubbles at bottom | Score tiles, tap best | Tile levels, support cards on each, rainbow/gold indicators, stat gains (selected tile) |
| Event | Event title banner, choice buttons | Look up event, choose option | Event title text, choice text, character name |
| Race entry list | "Race List" title, race rows | Score races, pick one or go back | Race names, grades, distances, surfaces, Result Pts |
| Race confirmation | "Race Details" modal popup | Tap "Race" or "Cancel" | Race name, grade (already decided at this point) |
| Pre-race / View Results | Character stats display, "View Results" + "Race" buttons | Tap "View Results" if available, otherwise "Race" | Presence of "View Results" button |
| Skill shop | "Learn" title, skill list with costs | Buy priority skills or go back | Skill names, costs, Skill Points remaining, hint badges |
| Item shop | "Shop" title, item list with coin costs | Buy priority items or go back | Item names, costs, Shop Coins remaining, stock |

### Transition Screens (bot just taps through)

| Screen | Identifier | Bot Action |
|--------|-----------|------------|
| Result Pts popup | "Received Result Pts!" text, "Close" button | Tap "Close" |
| Race results (placings) | Placement list (1st/2nd/3rd), "Next" button | Tap "Next" |
| Race results (fans/grade) | Fan count, grade tier display, "Next" button | Tap "Next" |
| Post-race event | Same as event screen but follows race | Choose bottom option (safe default for parent runs) |
| Victory concert | "Watch Concert" button visible | Tap "Next" (skip concert) |
| Rest/Rec/Infirmary confirm | Modal popup with confirm button | Tap confirm |
| Recreation pal card choice | Pal card outing option | Pick pal card outing if available (better stats) |
| Loading screens | No recognizable UI elements | Wait |
| Cutscenes | Varied art, no standard UI | Wait / tap to advance |
| Race (live) | 3D race view, fast-forward buttons | Tap fast-forward, wait for finish |

### End-of-Career Screens

| Screen | Identifier | Bot Action |
|--------|-----------|------------|
| Twinkle Star Climax | Climax-specific UI | Follow climax flow (alternating train/race) |
| Final grade | Letter grade display | Done — log result, start new run |

## Screen Identification Strategy

### Approach: Title Text Anchors + Template Matching

Each screen has a **title label** in the top-left area that is consistent and OCR-friendly:
- "Career" → home screen
- "Training" → training tiles
- "Race List" → race entry
- "Shop" → item shop
- "Learn" → skill shop

For screens without clean titles, use **template matching** on unique UI elements:
- "Race Details" modal → race confirmation
- "View Results" button → pre-race screen
- "Received Result Pts!" → result popup
- "Close" / "Next" buttons in specific positions → transition screens

### Implementation

```
Frame → Grayscale → Check title region OCR → Match known titles
                  → If no match: template match against anchor images
                  → If no match: check for modal overlays (popups)
                  → If no match: UNKNOWN (wait + retry)
```

The title region is roughly `(20, 0, 300, 60)` at 1080x1920. OCR this region first — it's the cheapest and most reliable check.

### Calibration Steps

For each screen type:
1. Crop the title/anchor region from the screenshot
2. Save as template in `data/templates/<screen_name>/`
3. Record the OCR text for title-based matching
4. Define the fixed regions for data extraction (stat positions, button positions, etc.)

Use `scripts/extract_templates.py` to automate this from the screenshots in `screenshots/`.

## Data Extraction Regions

For each screen, define rectangular regions where specific data lives. These are fixed at 1080x1920 and scale proportionally.

### Home Screen Regions
- Turn counter: top-left area, large yellow number
- Energy bar: below turn counter, horizontal bar
- Mood: right of energy bar, text label (NORMAL/GOOD/BAD)
- Stats: row of 5 values near bottom (Speed/Stamina/Power/Guts/Wit)
- Skill Points: right end of stats row
- Result Pts: top banner area

### Training Screen Regions
- Selected tile label: top area ("Speed Lvl 2", "Stamina Lvl 1", etc.)
- Stat gain previews: small "+N" indicators above each stat in the stats row
- Failure rate: orange bar below character
- Tile bubbles: 5 circular buttons at bottom with labels
- Support card icons on tiles: small portraits on each bubble

### Event Screen Regions
- Event title banner: colored bar with event name
- Character name: may appear in banner
- Choice buttons: 2-3 rounded rectangles with choice text
- Effects button: bottom-right, opens the effects preview

### Race Entry Regions
- Race rows: each row has name, distance, surface, grade, pts
- Currently selected race: highlighted row at top with detail card

## Failure Handling

For parent runs, when the bot can't identify a screen:
1. Wait 2 seconds (animation may be settling)
2. Retry identification
3. If still unknown after 3 retries: tap center-bottom of screen (advances most transitions)
4. If stuck for 30+ seconds: tap "Back" button position, return to home screen
5. Log the frame for later review

This is deliberately aggressive about recovering — a parent run that stumbles through a few transitions is still valuable. We can tighten error handling for ace runs later.

## Dependencies on master.mdb

Now that we have `master.mdb`, event matching becomes much more reliable:
- OCR the event title → fuzzy match against `text_data` (category 189)
- Get the story_id → look up optimal choices from community data / pre-computed table
- For parent runs: if no match found, pick the bottom choice (safe default)

This removes the need for the local LLM (Tier 2) in most event cases.
