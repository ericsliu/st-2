# Run 13 — Career Notes

## Check-in #1 — 2026-04-29 04:06 AM (~2 min into run)

- **Career:** Junior Year Pre-Debut, turn 5-6 (started at turn 11 remaining, now 6 remaining)
- **Mood:** GREAT (was NORMAL at start, got boosted by "I Want You to Rest!" event)
- **Stats at turn 5:** Spd=167 Sta=184 Pow=191 Gut=171 Wit=114 SP=149 energy=47
- **Packet-driven tiles:** WORKING — "Packet preview: 5 tiles (skipped OCR loop)" on every training turn. No OCR tile scan happening.
- **Frida probe:** alive and capturing
- **Events handled:** "Riko Kashimoto, the Hard Worker" (dialogue), "I Want You to Rest!" (+3 mood to GREAT)
- **Decisions so far:**
  - Turn 4: trained Guts (4 cards, score 400.9)
  - Turn 5: backed out to rest due to 9% failure rate on Speed
- **Current screen:** Training preview (Stamina), turn 6
- **Issues:** None observed. Bot is running smoothly and fast without OCR tile scanning.
- **Device:** BlueStacks (emulator-5554), ADB_SERIAL defaulted correctly after code change

## Check-in #2 — 2026-04-29 04:11 AM (~7 min into run)

- **Career:** Just completed Junior Make Debut (1st place, Symboli Rudolf). Now on turn 13, post-debut.
- **Stats at turn 13:** Spd=242 Sta=223 Pow=235 Gut=176 Wit=159 SP=219 energy=9
- **Mood:** GREAT
- **Positive statuses:** pure passion
- **Race loop bug:** Bot got stuck in a going_to_races loop at turn 10 — Trackblazer rhythm (every 2 turns) tried to race but Races button was locked during pre-debut. Looped 4 times before breaking out. **Fixed**: added `is_pre_debut` gate before race selector in `_handle_career_home` (auto_turn.py line ~4685).
- **Packet tiles:** Worked for first few turns, but OCR fallback kicked in after the race loop delay (session staleness). Should recover now that bot is progressing normally.
- **Frida probe:** alive
- **Victory event:** chose option 2 (correct per strategy)
- **Energy very low at 9% post-debut** — likely will rest next turn

## Check-in #3 — 2026-04-29 04:17 AM (~13 min into run)

- **Career:** Junior Year Early Aug, turn 15. Post-debut, training phase.
- **Stats at turn 15:** Spd=244 Sta=253 Pow=235 Gut=176 Wit=179 SP=223 energy=98
- **Mood:** GREAT
- **Rested on turn 14** due to 26% failure rate (energy was at 9% post-debut). Recovered to 98%.
- **Event:** "A Diligent Effort" gave +10 Sta
- **Packet tiles:** NOT active in current process — the default flip from "0" to "1" only takes effect on next bot restart. Bot is using OCR tile scanning (2s per tile). This is expected; the fix landed in code but the running process has the old default.
- **Shop:** Visited turn 15, 65 coins. Scrolled through 8 pages of Motivating Megaphone skips (too expensive with reserve). No items bought. Shop OCR is slow but functional.
- **Bond levels:** Looking good — speed cards at 79/80, indicating approaching friendship threshold.
- **Frida probe:** alive
- **No errors or stuck states.** Bot progressing well.

## Check-in #4 — 2026-04-29 04:22 AM (~18 min into run)

- **Career:** Junior Year Late Aug, turn 16-17.
- **Stats at turn 16:** Spd=260 Sta=253 Pow=242 Gut=196 Wit=179 SP=226
- **Mood:** GREAT, energy near max after rest
- **Result Pts:** 60 — MAX Goal Achieved! Excellent for Junior.
- **Races:** Won Niigata Junior Stakes (G3, 1st place). Plaque matcher correctly identified it (score 0.79). Race strategy set to pace. Used View Results to skip animation.
- **Post-race flow:** Result pts popup → standings (1st) → placement → Victory event (chose option 2) → Trackblazer Rival Bested event (+5 Sta, +5 Wit). All handled cleanly.
- **Consecutive races:** 2
- **Shop:** Visited after race win (flagged by "Won race! Will visit shop next career_home").
- **Packet tiles:** Still OCR fallback (current process has old default). Will fix on restart.
- **Frida probe:** alive
- **No errors or stuck states.** Bot performing well — strong Junior year so far.
