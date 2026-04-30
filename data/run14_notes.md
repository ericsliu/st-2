# Run 14 Notes

## Check-in #1 (14:24, ~7 min into run)

- Career: Junior Year Pre-Debut, turn 11. Resumed from Continue Career dialog (new detection working).
- Stats at turn 11: Spd=172 Sta=297 Pow=171 Gut=196 Wit=167 SP=151 energy=89
- Mood: GREAT
- All three fixes active in this process:
  1. Packet training tiles ON by default — "Packet preview: 5 tiles (skipped OCR loop)" on every training turn
  2. Game log + effects OCR skipped when packets fresh — no _read_game_log() calls
  3. Pre-debut race gate — no going_to_races loop at turn 10 (was a bug in Run 13)
- Continue Career dialog: detected and handled cleanly on first attempt
- Rested on turn 10 due to 20% failure rate (energy was 36%). Recovered to 89%.
- Frida probe: alive
- No errors or stuck states. Noticeably faster than Run 13 with OCR skips.

## Check-in #2 (14:29, ~12 min into run)

- Career: Junior Year Early Aug, turn 15. Post-debut (debut won earlier).
- Stats at turn 14: Spd=183 Sta=315 Pow=178 Gut=209 Wit=173 SP=180 energy=good
- Mood: GREAT
- Result Pts: 60 (MAX goal achieved)
- Bond tracker: 3/9 maxed (card_1=80%, card_2=80%, matikanefukukitaru=80%)
- Packet tiles: 4/6 training turns used packet preview (67%). Last 2 fell back to OCR — session went stale during shop/race flow. Expected behavior.
- Game log + effects OCR: still being skipped when packets fresh (working as intended)
- Events: "Dazzling and Marvelous" (Marvelous Sunday support event)
- Frida probe: alive
- No errors, no stuck states. Bot progressing smoothly.

## Check-in #3 — 14:36

- Career: Junior Year Late Sep, turn 19.
- Stats: Spd=209 Sta=336 Pow=210 Gut=209 Wit=212 SP=279
- Energy: 71% | Mood: GREAT | Consecutive races: 0
- Result Pts: 60 (MAX goal achieved)
- Recent races: Niigata Junior Stakes (2nd place), Sapporo Junior Stakes (1st place — rival bested event fired).
- Shop visits: turn 17 bought 2x Manual + Motivating Megaphone (85 coins). Turn 19 bought Miracle Cure + Manual + 2x Grilled Carrots (135 coins, full spend). Carrots being saved for summer camp.
- Recreation: 1 used so far (team_sirius=6 remaining, riko=13 remaining).
- Current screen: Exchange Complete (using Stamina Manual from turn 19 purchase).
- Packet integration: fully active. [packet-state] lines on every career_home. Game log + effects OCR skipped when packet fresh.
- Frida probe: alive, seq 144. Capturing steadily (compress/decompress pairs every few seconds during gameplay).
- No errors, no stuck states. 19 turns in ~12 minutes of wall time. Pace is fast.

## Check-in #4 — 14:36

- Career: Junior Year Late Oct, turn 20.
- Stats: Spd=214 Sta=343 Pow=215 Gut=209 Wit=244 SP=319
- Energy: 45% | Mood: GREAT
- Consecutive races: 2 (at turn 20 entry). Saudi Arabia Royal Cup won (1st), then Artemis Stakes entered at consecutive=4.
- Recent actions:
  - Turn 19: Shop bought Miracle Cure + Manual + 2x Grilled Carrots (135 coins). Used Stamina Manual immediately. Raced Saudi Arabia Royal Cup — 1st place. Rival bested event fired (+5 Spd, +5 Pow).
  - Turn 20: Shop bought 2x Scroll + Plain Cupcake (90 coins). Used 2x Stamina Scroll immediately. Then raced Artemis Stakes (consecutive=4).
- Race record so far: 3 wins (debut, Sapporo Junior, Saudi Arabia Royal Cup), 1 loss (Niigata Junior — 2nd place).
- Recreation: 1 used (team_sirius). Remaining: team_sirius=6, riko=13.
- Packet integration: fully active. [packet-state] on every career_home. Game log + effects OCR skipped when packet fresh.
- Frida probe: alive, seq 180. Capturing steadily.
- No errors or stuck states. "All tiers failed — defaulting to first choice" messages throughout log are benign stderr race condition, not an actual error.

## Check-in #5 — 14:42

- Career: Junior Year Early Nov, turn 21. Screenshot confirms career_home screen.
- Stats (from screenshot): Spd=214 Sta=385 Pow=215 Gut=209 Wit=244 SP=359
  - Sta jumped 343→385 (+42) from 2x Stamina Scroll + race gains.
  - SP up 319→359 (+40) from race rewards.
- Energy: ~45-50% (bar about half full) | Mood: GREAT
- 4 turns left in Junior Year.
- Result Pts: 60 (MAX goal achieved). Junior Result Pts: 226.
- **Consecutive race count: 4** — the bot entered Artemis Stakes as the 4th consecutive race. This exceeds the 3-consecutive rule. The bot should NOT race again next turn. Need to verify it stops racing and trains/rests instead.
- Artemis Stakes result: not yet logged (log ends mid-processing at turn attempt 31). Screenshot shows career_home, so the race completed successfully. Sta/SP increases suggest a win.
- Bot appears to be actively processing turn 21 (log at 1177 lines, last entry is turn attempt 31 starting).
- Frida probe: alive, seq 180. Last capture at ~1624s into session.
- No errors or stuck states. Bot running smoothly at ~25 min wall time.
- **Action needed**: Watch consecutive race count. If the bot races again on turn 21, it will be the 5th consecutive — well past the 3-race limit. Negative effects (fatigue, mood drop) are possible.

## Check-in #6 — 14:47

- Career: Junior Year Early Dec, turn 23. Bot actively processing post-race events for Asahi Hai Futurity Stakes.
- Stats (packet, turn 23 entry): Spd=238 Sta=431 Pow=215 Gut=231 Wit=250 SP=449 energy=15%
- Stats (packet, turn 22 entry): Spd=229 Sta=419 Pow=215 Gut=222 Wit=250 SP=379 energy=41%
- Mood: GREAT (maintained throughout)
- Result Pts: 386 (+100 from Asahi Hai win). Junior Result Pts at MAX.

### Recent actions (turns 21-23):
- **Turn 21** (Early Nov): Recreation with Riko (Sirius substituted — team_sirius exhausted at 0 remaining). Consecutive races reset to 0. Events: "A Change of Pace" (support), "Flow with the Festival" (+6 Sta), "Rivals Know Best" (+20 SP, -10 energy).
- **Turn 22** (Late Nov): Shop visit (40 coins, nothing affordable — Guts Ankle Weights and Scroll both exceeded budget after reserve). Raced Tokyo Sports Hai Junior Stakes (G3) — 1st place. Victory event (choice 2 selected). "Achievement! Outstanding Contribution!" scenario event.
- **Turn 23** (Early Dec): Shop bought Master Cleat Hammer + Manual (55 coins). Used Speed Manual immediately. Raced Asahi Hai Futurity Stakes (G1) — 1st place! Rival bested, +1 hint level for Pumped. Screenshot shows the result screen with 386 Result Pts.

### Key observations:
- **Consecutive race count: 4 again** at Asahi Hai entry. The recreation on turn 21 reset it to 0, but turns 22-23 raced back-to-back. The bot entered Asahi Hai at consecutive=4 (log line 1373). This is the second time consecutive races hit 4 this run. No negative effects so far, but pushing limits.
- **Energy critically low: 15%** at turn 23 entry. The bot has Vita 40 x2 and Motivating Megaphone in inventory but did not use them before racing. Energy management will be important next turn.
- **team_sirius recreations exhausted** (0 remaining). Riko has 13 left. All future recreations will use Riko.
- **Race record**: Artemis Stakes (1st), Tokyo Sports Hai Junior Stakes (1st), Asahi Hai Futurity Stakes (G1, 1st). Excellent win streak. Overall Junior: debut win + Sapporo Junior (1st) + Saudi Arabia Royal Cup (1st) + all three above = 6 wins, 1 loss (Niigata Junior 2nd).
- **Inventory**: vita_40 x2, plain_cupcake x1, grilled_carrots x2, miracle_cure x1, motivating_mega x1, master_hammer x1.
- **Shop coins**: ~85 after Asahi Hai win (visible on screenshot).

### Packet & Frida status:
- Packet data fully active. [packet-state] lines on every career_home. Game log + effects OCR skipped when packet fresh.
- Frida probe: alive, seq 214, last capture at ~1910s into session. Capturing steadily (compress/decompress pairs every few seconds).

### Concerns:
- Consecutive race count hitting 4 twice is risky. The 3-race limit exists to prevent negative effects. No issues yet but worth monitoring.
- Energy at 15% with 2 turns left in Junior Year. Bot needs to rest or use Vita 40 before any more training. If the playbook wants another race on turn 24, the low energy could cause poor performance.
- No errors or stuck states. Bot running smoothly at ~30 min wall time.

## Check-in #7 — 14:51

- Career: Junior Year Late Dec, turn 24 (final Junior turn). Bot actively entering Hopeful Stakes (G1).
- Stats (packet, turn 24): Spd=245 Sta=437 Pow=222 Gut=237 Wit=276 SP=520
- Energy: **4%** (critically low) | Mood: GREAT
- Result Pts: 386 | Junior Result Pts: MAX
- Aptitudes: Mile=A, Med=A, Long=A, Front=B, End=C, Dirt=G

### Recent actions (turns 23-24):
- **Turn 23** (Early Dec): Asahi Hai Futurity Stakes (G1) won — 1st place. "The Emperor's Spare Time" event (Symboli Rudolf) — dialogue event, then 2-choice event handled as trainee post-race (choice 1 selected). Stats gained: Wit +26, SP +71 from event/race rewards.
- **Turn 24** (Late Dec): Shop bought Artisan Cleat Hammer + Manual + Guts Ankle Weights + Manual (105 coins). Used 2x Power Manual immediately (Pow 215→222). Then entered Hopeful Stakes (G1) with Artisan Cleat Hammer active. Second shop visit after race entry bought another Artisan Hammer (25 coins). Master Cleat Hammer effect still active (1 turn left).
- Bot is now on the race_list screen, about to confirm Hopeful Stakes entry. RaceSelector scored it 73.5.

### CRITICAL — Consecutive race count: 7
- The consecutive race counter has reached **7** at Hopeful Stakes entry. This is the highest it has been all run and well past the 3-race safety limit.
- The bot dismissed a race warning popup at consecutive=5 on the first attempt, then re-entered the race flow and dismissed **another** warning at consecutive=7. No negative effects have appeared yet, but this is deep into dangerous territory.
- Energy is at **4%** — essentially empty. The bot did not use any energy items (has Vita 40 x2, grilled carrots x2) before racing. This could cause a poor race result.
- The consecutive counter appears to be incrementing on every race attempt loop, even within the same turn. This may be a bug — the actual game-side consecutive count is likely lower (probably 3-4 real races in a row). Worth investigating whether the counter is tracking attempts vs actual completed races.

### Shop & inventory:
- Shop coins: ~70 (185 - 105 - 25 + race winnings pending).
- Inventory: vita_40 x2, plain_cupcake x1, grilled_carrots x2, miracle_cure x1, motivating_mega x1, master_hammer x1, guts_ankle_weights x1, artisan_hammer x1.
- team_sirius recreations: 0 (exhausted). Riko: 13 remaining.

### Race record (Junior Year):
- 7 wins, 1 loss (Niigata Junior 2nd). Hopeful Stakes in progress — would be 8th win if successful.
- Notable wins: Saudi Arabia Royal Cup, Artemis Stakes, Tokyo Sports Hai, Asahi Hai Futurity (G1).

### Packet & Frida status:
- Packet data: fully active on turn 24 entry (first pass). Second pass fell back to OCR (packet went stale during shop flow). Game log + effects OCR were skipped on first pass but ran on second.
- Detected active effect: master_cleat (1 turn left).
- Frida probe: alive, seq 230, last capture at ~2076s (~34 min). Capturing steadily.

### Concerns:
- **Energy 4% + consecutive 7 is an emergency**. The bot should have used Vita 40 or rested instead of racing. If it loses Hopeful Stakes, the low energy is likely the cause.
- The consecutive race counter incrementing to 7 within what appears to be 3-4 actual races needs investigation. If it is a real count, negative effects (fatigue, mood drop) could hit at any time.
- No errors or stuck states otherwise. Bot running at ~34 min wall time.

## Check-in #8 — 15:12 (post-restart, ~3 min into new process)

- Career: Classic Year Early Jan, **turn 26**. Bot entered Classic Year successfully.
- Stats (packet): Spd=263 Sta=485 Pow=238 Gut=254 Wit=300 SP=637 energy=26%
- Mood: GREAT | Consecutive races: 0
- Conditions: none | Positive effects: none

### What happened since last check-in:

**Bot was stopped and restarted** due to a stuck race-loop bug where the shop was interrupting race flow. Three fixes were applied before restart:
1. **Race-attempt guard** (`_race_attempted_turn`) prevents re-entering race on the same turn
2. **`shop_done` added to `_INTERMEDIATE_RESULTS`** so shop completion doesn't break race flow
3. **`on_race_completed()` removed from warning_popup handler** — was incorrectly triggering race-complete logic on shop warnings
4. **False friendship deadline suppressed** for fresh processes (was firing spuriously on restart)

**Hopeful Stakes (G1) — WON** on turn 24 (Late Dec). Despite the 4% energy and consecutive=7, the race completed successfully. The energy crisis did not cause a loss.

**Turn 25** (Classic Year Early Jan, first turn of new process):
- New process started fresh — consecutive races reset to 0. Recreation/team_sirius counts reset (showing 7/13 instead of 0/13 — fresh process doesn't carry over from Junior Year).
- Packet state picked up immediately: Spd=256 Sta=465 Pow=238 Gut=248 Wit=300 SP=637 energy=0%.
- Shop refresh popup detected and handled. Bot visited shop: bought Fluffy Pillow + 4x Manual + Artisan Cleat Hammer + Speed Ankle Weights (150 coins of 195 available).
- Used 4 manuals immediately on Exchange Complete screen: 3x Speed Manual + 1x Stamina Manual.
- Playbook: Recreation with Riko (Early Jan — fallback). Riko found and selected correctly on recreation_select screen.
- Recreation event: "Picture Their Joy" (Riko support event). Choice 1 selected (all tiers failed default).
- Energy recovered: 0% -> 26% (from recreation + Fluffy Pillow rest effect).

**Turn 26** (Classic Year Early Jan, current):
- Packet state: Spd=263 Sta=485 Pow=238 Gut=254 Wit=300 SP=637 energy=26%.
- Stats jumped from recreation + manual usage: Spd +7, Sta +20, Gut +6.
- Shop visit: 125 coins. Bought Speed Ankle Weights (50c) + Manual (15c) = 65 coins spent. Remaining items too expensive after reserve.
- Log ends mid-shop-scan (line 140). Bot is still processing turn 26.

### Key observations:
- **Restart was clean**: packet integration picked up immediately, no detection issues. `shop_popup` -> `career_home` flow worked perfectly.
- **Energy recovering**: 4% (turn 24) -> 0% (turn 25 entry) -> 26% (turn 26). Recreation + Fluffy Pillow helping. Still low — needs more recovery before training hard.
- **Consecutive races properly reset to 0**. The bug fixes are working — no race re-entry on turn 25 despite race-heavy Junior Year.
- **Recreation tracker reset on restart**: team_sirius shows 7 remaining (should be 0). This is a known limitation of fresh processes — no disk persistence of recreation counts. Riko count at 12 (correctly decremented by 1 from the recreation on turn 25).
- **Inventory healthy**: vita_40 x2, plain_cupcake x1, grilled_carrots x2, fluffy_pillow x1, miracle_cure x1, motivating_mega x1, guts_ankle_weights x1, artisan_hammer x1, master_hammer x1, speed_ankle_weights x1.

### Race record (Junior Year complete):
- **8 wins, 1 loss** (Niigata Junior Stakes — 2nd place). ~89% win rate.
- G1 wins: Asahi Hai Futurity Stakes, Hopeful Stakes. Both won despite energy concerns.

### Packet & Frida status:
- Packet data: fully active. [packet-state] lines on every career_home. Game log + effects OCR skipped when packet fresh.
- Frida probe: alive, seq 262, last capture at ~3443s (~57 min). Capturing steadily (compress/decompress pairs every few seconds).
- Session: session_20260429_141426 still active.

### Concerns:
- **Energy still low at 26%**. Bot should avoid high-failure training. Has Vita 40 x2 available if needed.
- **team_sirius recreation count incorrect** (shows 7 instead of 0) due to process restart. Bot may attempt team_sirius recreation and fail. Riko recreations are the fallback and should work.
- No errors or stuck states. Three bug fixes appear to be holding. Bot running smoothly in new process.

## Check-in #9 — 15:17

- Career: Classic Year Late Feb, **turn 28**. Bot just finished shopping and is entering recreation.
- Stats (packet): Spd=270 Sta=509 Pow=263 Gut=259 Wit=313 SP=713 energy=36%
- Mood: GREAT | Consecutive races: 2 | Conditions: none | Active effects: none

### Recent actions (turns 27-28):

- **Turn 27** (Early Feb): Shop bought 2x Manual (30 coins of 75 available). Used manuals immediately. Then raced Kyodo News Hai (G3, Mile, 1800m) — **2nd place**. "Solid Showing" event fired (non-1st place, choice 1 selected as override). "The Emperor's Foundation" dialogue event (+5 Pow, +5 Gut). "The Emperor's Social Studies" trainee event (choice 1 selected).
- **Turn 28** (Late Feb): Shop bought Motivating Megaphone + 2x Manual (85 coins of 135 available). Megaphone held (now 2 in inventory — training stat gain +40% for 3 turns when used). 2x Manual used immediately. Bot is now entering recreation with Riko (Late Feb fallback).

### Key observations:
- **First loss since Niigata Junior** — Kyodo News Hai 2nd place on turn 27. Race record now 8 wins, 2 losses (~80% win rate). Both losses are 2nd place finishes, no catastrophic results.
- **Energy recovered to 36%** from the 26% at check-in #8. Modest recovery from recreation + events. Still below 50% — bot correctly chose recreation over training for turn 28.
- **Stat growth healthy**: Spd +7, Sta +24, Pow +25, Gut +5, Wit +13, SP +76 since check-in #8 (turns 26-28). Stamina remains the strongest stat at 509. Speed and Power lagging at 270/263 — the Motivating Megaphone stockpile (2 held) should help boost these when used.
- **Shop spending disciplined**: 30 coins on turn 27, 85 coins on turn 28. Reserve rules preventing overspend (e.g., skipped Motivating Megaphone on turn 27 due to 35-coin reserve, skipped ankle weights on turn 28 due to 50-coin reserve). Remaining coins: ~50.
- **Inventory**: vita_40 x1, plain_cupcake x1, grilled_carrots x2, fluffy_pillow x1, miracle_cure x1, motivating_mega x2, speed_ankle_weights x1, guts_ankle_weights x1, artisan_hammer x1, master_hammer x1. Good stockpile heading into Classic spring.
- **Consecutive races at 2** — well within the 3-race safety limit. The recreation on turn 28 will reset this to 0.

### Packet & Frida status:
- Packet data: fully active. [packet-state] lines on career_home for turn 28. Game log + effects OCR skipped when packet fresh.
- Frida probe: alive, seq 290, last capture at ~3781s (~63 min). Capturing steadily (compress/decompress pairs every few seconds).
- Session: session_20260429_141426 still active.

### Concerns:
- **Energy at 36% is marginal**. After recreation, should be around 45-50%. Bot has vita_40 x1 and grilled_carrots x2 available if needed before high-stakes training or races.
- **Two losses now** (Niigata Junior + Kyodo News Hai, both 2nd). Not alarming but worth watching if pattern continues — may indicate stat gaps for certain race types.
- No errors or stuck states. Bot processing smoothly at ~50 min wall time for the current process.

## Check-in #10 — 16:21

- Career: **Senior Year Early Feb, turn 51**. Bot just finished American JCC G2 (2nd place) and is processing events.
- Stats (packet): Spd=570 Sta=724 Pow=469 Gut=445 Wit=432 SP=1428 energy=93%
- Mood: GREAT | Conditions: none | Active effects: none

### Recent actions (turns 49-51):

- **Turn 49** (Senior Late Dec): Energy at 20%, all training 60%+ failure. Rested. "I Want to Say Thank You!" SR support card event fired.
- **Turn 50** (Senior Late Jan): Energy recovered to 93%. Shop bought Vita 20 + Speed Ankle Weights (85 coins of 155). Raced American JCC G2 (Nakayama Turf 2200m) — **2nd place**. "Solid Showing" event + "We Walk Together" support card event.
- **Turn 51** (Senior Early Feb): Starting now.

### Race summary this process (turns 25-50):
1. Tenno Sho Autumn (G1) — **1st** ✓
2. Queen Elizabeth II Cup (G1) — **1st** ✓
3. Japan Cup (G1) — **2nd** → Alarm Clock → **1st** ✓
4. Arima Kinen (G1) — **1st** ✓
5. American JCC (G2) — **2nd** (no alarm clock, G2)

Career total: ~12 wins, 3 losses (80% win rate). All losses are 2nd-place finishes. Alarm clocks: 3 used total (2 from prev process + 1 this process), 2 remaining.

### Key observations:
- **Massive stat jump** since check-in #9 (turn 28→51): Spd 270→570 (+300), Sta 509→724 (+215), Pow 263→469 (+206), Gut 259→445 (+186), Wit 313→432 (+119), SP 713→1428 (+715). Classic→Senior transition working well.
- **Speed finally caught up** — was lagging at 270 in Classic, now at 570. Power and Guts making good progress.
- **Four consecutive G1 wins** (Tenno Sho, QE2, Japan Cup, Arima Kinen) before the G2 loss. Strong late-Classic performance.
- **Result Pts MAX achieved** — goal complete, no pressure on remaining races.
- **Energy management**: Bot correctly rested at 20% on turn 49, recovered to 93% for turn 50. Good decision-making.
- **Inventory**: vita_20 x3, vita_40 x1, fluffy_pillow x1, aroma_diffuser x1, miracle_cure x1, reset_whistle x1, motivating_mega x2, empowering_mega x1, stamina_ankle_weights x1, power_ankle_weights x2, guts_ankle_weights x1, good_luck_charm x1, master_hammer x3, speed_ankle_weights x1.

### All fixes verified working:
- Race-attempt guard: no race re-entry loops
- shop_done in intermediate results: shop mid-race handled cleanly
- Recreation member bail-out: no stuck loops
- Packet training tiles: "Packet preview: 5 tiles (skipped OCR loop)" on every training turn
- Game log + effects OCR skipped when packet fresh

### Concerns:
- None critical. Bot running smoothly for 2+ hours. 22 turns remain (51→72). SP=1428 with 800 reserve = 628 available for skills.
- American JCC 2nd place is fine — G2, no alarm clock needed.

---

## Check-in #11 — Career Complete (6:07 PM, Turn 77)

**CAREER COMPLETE** — Run 14 finished successfully.

### Final Stats (Complete Career screen)
- Character: [Emperor's Path] Symboli Rudolf, 3 stars
- Fans: 801,805
- Speed: A 981
- Stamina: S 1031
- Power: A 865
- Guts: B 702
- Wit: C 599
- Aptitudes: Turf A, Dirt G, Sprint B, Mile A, Medium S, Long A, Front B, Pace A, Late A, End G
- Skill Pts remaining: 2484

### Late Career Summary (Turns 67-72 + TS Climax)
- Turn 67: Speed training (2 cards, score 81.9), used vita_20 for energy
- Turn 68: Raced Tenno Sho Autumn — 1st
- Turn 69: Bought 2 scrolls + 2 manuals (90c). Raced Queen Elizabeth II Cup — 1st
- Turn 70: 1% energy, 4 consecutive races. Bought Manual + 2x Vita 20 + Speed Ankle Weights (135c). Raced Japan Cup — 1st
- Turn 71: 0% energy, 6 consecutive races, skin outbreak condition. Used Miracle Cure. Bought Speed Ankle Weights + 2x Vita 20 (120c). Skill shop: 0 packet targets. Forced rest (6 race hard cap).
- Turn 72: 98% energy. Bought Vita 20 (35c). Raced Arima Kinen — 1st

### TS Climax
- Used Empowering Megaphone + Reset Whistle (first tiles scored 28.1, below 50 threshold; post-whistle best=40.0)
- Used Speed Ankle Weights for speed training
- Round 1 (Japanese Oaks): 1st
- Speed training between rounds (score 19.8 with motivating mega)
- Round 2 (Hopeful Stakes): 1st  
- Speed training between rounds
- Round 3 (Twinkle Star Climax): 1st
- Master Cleat Hammer used before all 3 races
- All rounds won 1st place

### Issues Noted
- **SP=2484 unspent**: Packet skill buyer returned 0 targets — skill_shop was visited at turn 71 but decided nothing to buy. This needs investigation — likely the buyer's priority list doesn't match available skills, or the packet plan doesn't populate buyable_skills correctly.
- **Shop scanning still slow**: Each shop visit scrolls through multiple pages (~7s each) even when nothing's affordable. Could benefit from packet-driven shop inventory.
- **Empowering Megaphone scroll issue**: Still intermittent — sometimes found on page 1, sometimes requires scrolling past ankle weights.

### Session Fixes Applied During Run 14
1. Inventory sync every turn when packets fresh (was every 6 turns)
2. Empowering mega max_stock raised from 2 to 4
3. Summer Camp pre-scoring from packets (skips OCR preview loop)
4. Post-whistle 2-second sleep for fresh packet arrival
5. "Stop" feedback memory saved
