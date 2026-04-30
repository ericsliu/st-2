# Run 14 Retro — 2026-04-29

## Results

| Metric | Run 14 | Run 13 | Delta |
|--------|--------|--------|-------|
| Speed | A 981 | S 1153 | -172 |
| Stamina | S 1031 | A 814 | +217 |
| Power | A 865 | A 849 | +16 |
| Guts | B 702 | B 705 | -3 |
| Wit | C 599 | B 781 | -182 |
| SP remaining | 2484 | 2468 | +16 |
| Win rate | 100% (8/8)* | 74% (34/46) | +26% |
| TS Climax | 3/3 1st | Won | — |
| Fans | 801,805 | — | — |
| Stars | 3 | — | — |
| Mood at end | NORMAL | — | — |

*Only observed turns 48–77 in this session; 8/8 wins in that window. Earlier turns were from a previous session.

## Key Issues

### 1. Stamina way too high (1031 vs 600 cap)
The runspec caps stamina at 600 with near-zero weight above it, so the scorer never targets stamina. But stamina grew +173 between turns 64–77 purely from:
- Race victory stat bonuses (5 G1 wins in turns 68-72)
- Power training side effects (+8 stamina per power train)
- Scrolls/manuals purchased from shop
- Random events

**Action:** Stamina growth is passive and uncontrollable given the race-heavy late schedule. The real question is whether those racing turns should have been training turns instead.

### 2. Speed only 981 — should be 1000+
The runspec targets speed at 1000. Speed grew from 715 (turn 64) to 981 (final). In the observed window:
- 6/7 training sessions targeted speed
- But only 3 actual training turns happened between turns 64-72 — everything else was racing
- The playbook mandated races for turns 68-72 (Tenno Sho Autumn, QE2, Japan Cup, Arima Kinen)
- TS Climax added ~125 speed (856→981) across 3 training turns

**Root cause:** The late-game schedule is too race-heavy. 3 consecutive G1s (turns 68-70: Tenno Sho, QE2, Japan Cup), a forced rest at turn 71 (0% energy + skin outbreak), then Arima Kinen at turn 72 — only 1 training turn (67) in the 8-turn window from 65-72.

**Action:** Consider limiting late-career racing to 3 consecutive max, interleaving training turns to push primary stats. Or, if the G1 schedule is mandatory, accept that training will happen mostly in TS Climax.

### 3. Mood dropped to NORMAL (was GREAT → BAD → NORMAL)
- GREAT through turn 69
- Dropped to BAD at turn 70 (after 4+ consecutive races, 0% energy)
- Rested at turn 71: energy recovered to 98%, but mood only recovered to NORMAL
- Never recovered to GREAT — would need Recreation for that
- **0 recreation used the entire run** (team_sirius=7, riko=13 remaining)

**Root cause:** The playbook never triggered recreation. Recreation is the only way to recover from BAD/NORMAL to GREAT, and the bot never used it. With 20 recreation charges remaining, this is a significant waste.

**Action:** Recreation should trigger when mood drops below GREAT in late game, especially before TS Climax where stat gains matter most. A GREAT mood bonus on TS Climax training turns could have pushed speed past 1000.

### 4. SP=2484 unspent — skill buyer broken
The packet skill buyer returned 0 targets with 2061+ SP budget. The skill shop was visited once (turn 71) and instantly exited. This is the same issue as Run 13 (2468 SP unspent).

**Root cause:** Needs investigation. Likely `buyable_skills` on GameState is empty because the SkillCatalog → adapter wiring isn't complete, or the buyer's priority matching can't find any of the available skills.

**Action:** Investigate in a separate session. See TODO below.

## What Went Well
- 100% win rate in observed window (8/8 races, all 1st place)
- TS Climax swept 3/3 races with 1st place each
- Packet integration stable throughout — inventory sync, training preview, game log skip all working
- Summer camp pre-scoring from packets worked (skipped OCR preview loop)
- Whistle logic worked correctly (scored 28.1 < 50, used whistle, re-scored at 40.0)
- Ankle weights and cleat hammers used effectively
- Miracle Cure auto-applied for skin outbreak condition
- Shop purchasing worked reliably across multiple visits
- 0 crashes, 0 stuck states

## Session Fixes Applied
1. Inventory sync every turn when packets fresh (was every 6 turns, caused drift)
2. Empowering mega max_stock 2→4
3. Summer camp pre-scoring from packets (skips OCR preview loop)
4. Post-whistle 2s sleep for fresh packet arrival

## TODO — Investigate Before Run 15
- [ ] Why does `decide_from_packet` return 0 targets? Trace through `buyable_skills` population
- [ ] Why was recreation never used? Check playbook recreation trigger conditions
- [ ] Consider capping consecutive races at 3 in late game (currently 6 hard cap)
- [ ] Mood recovery: should bot use recreation when mood < GREAT in Senior Year?
