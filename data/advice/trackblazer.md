# Trackblazer Scenario Strategy Guide

## Overview

Trackblazer (Make a New Track / MANT / Climax Opening) is the third permanent
career scenario. Racing is the core mechanic — a typical run involves 30–40
races across ~72 turns (nearly half of all turns). There are no
character-specific race objectives; any character can enter any race.

The 7 action buttons: Rest, Training, Skills, Infirmary, Recreation, Shop, Races.

---

## Grade Points (Result Points)

The primary progression currency. **Surplus points do NOT carry over between years.**

### Year-End Thresholds

| Year    | Turf | Dirt |
|---------|------|------|
| Junior  |   60 |   30 |
| Classic |  300 |  200 |
| Senior  |  300 |  300 |

### Points by Race Grade and Placement

| Grade  | 1st | 2nd | 3rd | 4th–5th | 6th+ |
|--------|-----|-----|-----|---------|------|
| G1     | 100 |  60 |  40 |      20 |   10 |
| G2     |  80 |  48 |  32 |      16 |    8 |
| G3     |  60 |  36 |  24 |      12 |    6 |
| OP     |  40 |  24 |  16 |       8 |    4 |
| Pre-OP |  20 |  12 |   8 |       4 |    2 |
| Debut  |  10 |   6 |   4 |       2 |    1 |

---

## Shop

### Coin Income by Placement

- 1st: 100 coins
- 2nd–3rd: 60 coins
- 4th–5th: 30 coins
- 6th+: 0 coins

### Refresh Cycle

Refreshes every 6 turns (~3 months). 11 total refreshes across the career.
Maximum 5 copies of any item. Random 10–20% discounts may appear.

### Item Tiers

**SS Tier (buy on sight):**
- Scrolls (+15 stat, 30 coins)
- Good-Luck Charm (0% failure, 1 turn, 40 coins)
- Empowering Megaphone (+60% training, 2 turns, 70 coins)
- Scholar's Hat (10% skill cost reduction, 280 coins)
- Master Cleat Hammer (+35% race stat gain, 1 race, 40 coins)

**S Tier:**
- Grilled Carrots (all bond +5, 40 coins — buy early)
- Rich Hand Cream (cure Skin Outbreak, 15 coins)
- Motivating Megaphone (+40% training, 3 turns, 55 coins)
- Reset Whistle (rearrange support cards, 20 coins)

**A Tier:**
- Manuals (+7 stat, 15 coins)
- Vita 65 (energy +65, 75 coins)
- Ankle Weights (+50% stat training / +20% energy cost, 1 turn, 50 coins)

**B Tier:**
- Notepads (+3 stat, 10 coins)
- Vita 20/40, Cupcakes, individual condition cures

### Key Items to Stockpile

- 3 Master Cleat Hammers for Twinkle Star Climax finals
- Megaphones for Summer Camp windows
- ~150 coins reserve for final shop refresh

---

## Race Selection Strategy

### Aptitude Requirements

Characters need at least C, ideally B aptitude in Mile AND Medium distance.

Available race counts:
- Mile: 10 G1, 12 G2, 33 G3
- Medium: 14 G1, 13 G2, 17 G3
- Sprint: 3 G1, 6 G2, 18 G3
- Long: 3 G1, 5 G2, 1 G3

### Race Stat Gains (1st place, before Race Bonus)

| Grade     | Stat Gain         | Skill Points |
|-----------|-------------------|--------------|
| G1        | +10 random stat   | +35 SP       |
| G2/G3     | +10 random stat   | +25 SP       |
| OP/Pre-OP | +10 random stat   | +20 SP       |
| TS Climax | +10 ALL stats     | —            |

### Race Bonus

Formula: `floor(base * (1 + race_bonus%))`
With Cleat Hammer: `floor(floor(base * (1 + RB%)) * (1 + hammer%))`

**Critical threshold: 50% Race Bonus from support cards.**
At 50% RB, G1 1st place = +15 random stat.

Breakpoints: 50% → 60% → 63% for increasing gains.

### Selection Priority

1. **G1 races matching aptitude** — always enter
2. **Rival races** (VS icon) — skill hints + bonus shop items
3. **G2 races at good aptitude** — when Grade Points needed
4. **G3 races** — filler to meet point thresholds
5. **OP/Pre-OP** — only if desperate for points

### When NOT to Race

- Rainbow/gold training tile with 3+ support cards stacked
- Summer Camp turns with Megaphone + Ankle Weights active
- First 3–5 turns of Junior (build base stats first)

---

## Energy Management

### Race Energy Cost

Each race costs ~25–30 energy. Energy does NOT affect race performance
(only training). Entering at 0 energy triggers Race Fatigue.

### Consecutive Race Fatigue

| Chain Length | At 0 Energy           | At 1+ Energy         |
|--------------|-----------------------|----------------------|
| 2nd race     | 20% mood down + cond  | 0%                   |
| 3rd+ race    | 60% mood down + cond  | 40% mood down + cond |

**Exception:** Fatigue cannot trigger after Late December. Safe to chain
3 races at year boundaries.

### Optimal Race Pattern

**2 races → 1 free turn → 2 races → ...** is the safe rhythm.
3-race chains only at year-end or with condition cure items ready.

### Guaranteed Recovery Events

- Classic Early Feb: mood +1
- Classic Early Mar: energy +20
- Classic Late Sep: mood +1
- Senior Late Jun: energy +20
- Senior Late Oct: mood +1
- Senior Late Dec: energy +30

---

## Phase-by-Phase Flow

### Junior Year (turns 1–24)

- Grade Points needed: 60 (turf) / 30 (dirt)
- Director bond target: ≥19
- Fan target: 5,000+
- Focus: debut race → Mile races, build support card bonds
- Buy Grilled Carrots from shop ASAP (all bond +5)
- Training focus: Wisdom (energy recovery)

### Classic Year (turns 25–48)

- Grade Points needed: 300 (turf) / 200 (dirt)
- Director bond target: ≥31
- Fan target: 60,000+
- **Summer Camp (Early July – Late August)**: all facilities Level 5
  - Deploy Empowering Megaphones + Ankle Weights here
  - This is the primary training window
- Schedule G1 and G2 races aggressively

### Senior Year (turns 49–72)

- Grade Points needed: 300 (turf/dirt)
- Director bond target: ≥51
- Fan target: 120,000+
- Second Summer Camp: another major training window
- Save 3 Master Cleat Hammers for Twinkle Star Climax
- Keep ~150 coins for final shop refresh

### "Umamusume of the Year" Selection

Meeting fan + bond thresholds at Late December triggers selection:
- Unique skill level-up
- Unique skill hint
- Stat bonuses

---

## Twinkle Star Climax (Finals)

3-race mini-league. Highest cumulative Victory Points wins.

### VP by Placement

| Place   | VP |
|---------|-----|
| 1st     | 10  |
| 2nd     |  8  |
| 3rd     |  6  |
| 4th     |  4  |
| 5th–6th |  3  |

Maximum: 30 VP. Distance/surface based on career race history.

### Strategy

- Use 3 Master Cleat Hammers (saved from shop)
- TS races give +10 ALL stats (not random) — hammers are very valuable
- With 50% RB + Master Hammer: ~20 to all stats per race
- Difficulty is lower than URA Finals
- Save alarm clocks for G1 races during the career, not for TS

---

## Turn-by-Turn Decision Priority

1. **G1 race available?** → Race (unless exceptional training turn)
2. **Rival race?** → Race (skill hints + shop items)
3. **Summer Camp + Megaphone active?** → Train
4. **Rainbow/gold training, 3+ cards stacked?** → Train
5. **Race needed for Grade Points?** → Race
6. **Shop has SS/S tier items?** → Shop
7. **Energy < 30?** → Rest or Vita item
8. **Default** → Train (prefer Wisdom for energy recovery)

---

## Unique Trackblazer Mechanics

- **No character-specific race objectives** — any character enters any race
- **+5 energy consumption** on all activities vs other scenarios
- **Reduced skill point income** from races (Scholar's Hat is key)
- **Stat caps**: Stamina 1900 (+700), Wisdom 1500 (+300), others 1200
- **Alarm clocks**: retry failed races, restore mood. Save for G1s.
- **Scenario skills**: "Kira Castle" (from Best Umamusume), "Morning Star" (from TS championship)

---

## Summer Camp Deep Dive

Summer Camp occurs three times (Junior, Classic, Senior years) during
Early July – Late August (4 turns each). All training facilities are
forced to Level 5 regardless of actual level.

### Why Summer Camp Is Critical

- Training facility base values in Trackblazer are the same as Unity Cup
  and **lower** than URA Finale. Since fewer turns are spent training
  (most turns go to races), facilities level up slowly. Summer Camp's
  forced Lv5 is often 2-3 levels above your natural facility level.
- This is your **primary stat-building window**. The bulk of raw stat
  gains from training happen here.

### Summer Camp Checklist

1. **Before camp**: Stockpile Megaphones, Ankle Weights, Good-Luck
   Charms, Reset Whistles, and energy items (Vita 65, Royale Kale Juice)
2. **Enter camp at 60+ energy** to avoid wasting turns resting
3. **Deploy Empowering Megaphone (+60%)** on turns with rainbow/gold
   training or 3+ stacked support cards
4. **Stack Ankle Weights** on top of Megaphone for maximum gains
5. **Use Good-Luck Charm** if energy is low but training is excellent —
   allows training even at 0 energy with 0% failure
6. **Use Reset Whistle** if support cards are poorly distributed — forces
   them to rearrange for better stacking
7. **Use Vita/Juice** to sustain multiple training turns without resting
8. **Avoid racing during camp** unless it's a G1 that can't be skipped

### Camp Priority by Year

- **Junior Camp**: Less impactful (bonds still building, few friendship
  trains). Use for stat training if bonds are ready, otherwise continue
  bond building with lower-priority training.
- **Classic Camp**: The **most important** camp. Bonds should be at
  orange (80+) by now. Stack every megaphone and weight here.
- **Senior Camp**: Second most important. Save some items for TS Climax
  prep turns. Facilities may be naturally higher by now, but Lv5 forced
  still helps.

---

## Parent Building in Trackblazer

### Overview

Parent building (creating strong Legacy Umas for inheritance) is one of
the endgame loops. Parents are valued for their Blue Sparks (stat
inheritance factors), not their final career performance.

### What Is a "9-Star" Parent?

- Blue sparks come in 1-star (+5 stat), 2-star (+12), or 3-star (+21)
- A "9-star" Uma has 3-star blue spark herself + both parents have 3-star
  blue sparks (3+3+3 = 9 stars)
- Using 9-star parents gives ~160 free stats before training begins,
  plus another 200+ across two Inspiration events

### Blue Spark Requirements

| Stat Level | Chance of 3-Star Blue |
|------------|----------------------|
| 1100       | ~20%                 |
| 1150       | ~35%                 |
| 1200+      | ~50% (cap)           |

For guaranteed single blue factors, focus one stat to 1200. For multiple
blue factors, aim for 1150 in 2-3 stats.

### Parent Building Strategy

1. **Start with any 2-star blue Uma** you have from normal play
2. **Borrow 3-star guest parents** from friend list (3 attempts/day)
3. **Raise new Umas** using that parent + guest — any 2-star result = 7-star
4. **Upgrade gradually**: 2-star + 3-star guest → if you hit 3-star yourself = 8-star
5. **Final step**: 8-star Uma + 3-star guest → hit 3-star = 9-star parent
6. **URA Finale is faster for parents** since stats don't need to be as
   high and runs are shorter. Use Trackblazer only when you need the run
   for other purposes (like trophy farming).

### Compatibility Score

- Displayed as triangle (low) / circle (mid) / double-circle (high)
- Hidden numeric score: 0–84
- Shared distance, surface, and strategy give the biggest boosts
- Winning the same G1 races as parents/grandparents adds +1 each
- Target 60+ (double-circle) to ensure both Inspirations trigger

### Pink Sparks (Aptitude Inheritance)

- Up to 9 pink spark types, totaling 18 stars maximum
- Critical for Trackblazer: Mile and Medium aptitude sparks let
  characters with low base aptitude participate in the majority of races
- Sprinters and Dirt-only characters benefit hugely from Mile/Turf sparks

---

## Support Deck Building

### Rule #1: Race Bonus >= 50%

Every support card should contribute Race Bonus. This is the single
most important deckbuilding constraint for Trackblazer. Aim for 50-75%
total Race Bonus across your deck.

### Recommended Archetypes

- Speed + Wit: 2 Speed, 2 Wit, 2 flex (often 2 Power for skills)
- Wit + Guts: 3 Guts, 2 Wit, 1 flex (buy Guts Anklets from shop)
- Always include 2 Wit cards for energy management
- F2P-friendly: reduced reliance on gacha supports due to race-heavy
  stat income

### Flex Slot Guidance

- **Speed + Wit deck**: fill flex with 2 Power cards that have good
  skill hints. Power training rarely happens but the passive skills and
  race bonus are valuable.
- **Guts deck**: 4th Guts or 1 Speed card depending on character growths
  and skill needs.

---

## Alarm Clocks

- Up to 5 alarm clocks per career (from shop and events)
- Retries a race you just lost — restores the turn as if the race
  never happened
- **Priority**: Save for G1 races where you placed 2nd-3rd (losing
  100 GP and 40+ coins vs winning)
- **Never use on**: G3, OP, or Pre-OP races (not worth the limited
  resource)
- **Never use on TS Climax**: Victory Points system is forgiving; you
  don't need to win all 3

---

## Rival Races

Rival races appear randomly, marked with a VS icon on the race button.

### Rewards

| Result              | Reward                                      |
|---------------------|---------------------------------------------|
| 1st place           | Skill hint (distance/style) + chance of new shop items |
| Beat rival (not 1st)| Draw — no skill hint, reduced shop chance    |
| Lose to rival       | Nothing extra                                |

### Strategy

- Always enter Rival Races when they appear — the extra rewards are free
- Rival Races don't cost extra energy or have special penalties
- New shop items from Rival wins last 3 turns before disappearing

---

## Common Mistakes

1. **Chaining 3+ races before Senior year** — mood/condition penalties
   are devastating early when you need training efficiency
2. **Ignoring the shop** — proper item usage is the difference between
   A-rank and S-rank runs
3. **Training during G1 windows** — the stat opportunity cost of
   skipping a G1 with 50%+ Race Bonus is almost always worse than
   training
4. **Hoarding scrolls/manuals** — use immediately, no benefit to saving
5. **Entering TS Climax without Hammers** — +10 ALL stats per race with
   hammers means +60 total stats from 3 races at 50% RB
6. **Neglecting Wit training** — energy recovery from Wit training
   sustains the race-heavy schedule
