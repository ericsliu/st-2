# Uma Musume Training Mechanics Reference

Compiled from community research, Japanese wikis (NamuWiki, VIP Uma Musume Wiki, GameWith),
English guides (Game8, GameTora, umareference.com), and datamined formulas.

Note: Some base values are approximate and may vary slightly by scenario.
The formulas below are well-established through JP community datamining since 2021.

---

## 1. Training Stat Gain Formula

The master formula for stat gain per training session:

```
Stat Gain = floor(
  (Base Value + Support Card Stat Bonus)
  * (1 + Character Growth Rate)
  * (1 + Mood Modifier * (1 + Mood Effect Bonus))
  * (1 + Training Effect Bonus)
  * (1 + 0.05 * Number of Support Cards Present)
  * Friendship Training Multiplier
)
```

Each component explained below.

### Worked Example

Speed training with Air Groove (10% Speed growth), Good mood, 30% mood effect bonus
from support cards, 17% training effect, 2 support cards present, no friendship training:

```
= (4 + 0) * (1 + 0.10) * (1 + 0.10 * (1 + 0.30)) * (1 + 0.17) * (1 + 0.05 * 2) * 1
= 4 * 1.1 * 1.13 * 1.17 * 1.10 * 1
= 6.398... -> floor -> 6
```

---

## 2. Training Base Values (URA Finale Scenario)

Each training type has a primary stat, secondary stat(s), skill points, and energy cost.
When training level increases by 1, the main stat base increases by +1, and sub-stats
increase proportionally (rounded down).

### Speed Training
| Level | Speed | Power | Skill Pt | Energy Cost |
|-------|-------|-------|----------|-------------|
| 1     | 10    | 5     | 2        | -21         |
| 2     | 11    | 5     | 2        | -22         |
| 3     | 12    | 6     | 2        | -23         |
| 4     | 13    | 6     | 3        | -25         |
| 5     | 14    | 7     | 3        | -27         |

### Stamina Training
| Level | Stamina | Guts | Skill Pt | Energy Cost |
|-------|---------|------|----------|-------------|
| 1     | 9       | 4    | 2        | -19         |
| 2     | 10      | 4    | 2        | -20         |
| 3     | 11      | 5    | 2        | -21         |
| 4     | 12      | 5    | 3        | -23         |
| 5     | 13      | 6    | 3        | -25         |

### Power Training
| Level | Power | Stamina | Skill Pt | Energy Cost |
|-------|-------|---------|----------|-------------|
| 1     | 8     | 5       | 2        | -19         |
| 2     | 9     | 5       | 2        | -20         |
| 3     | 10    | 6       | 2        | -22         |
| 4     | 11    | 6       | 3        | -23         |
| 5     | 12    | 7       | 3        | -25         |

### Guts Training
| Level | Guts | Speed | Power | Skill Pt | Energy Cost |
|-------|------|-------|-------|----------|-------------|
| 1     | 8    | 4     | 3     | 2        | -21         |
| 2     | 9    | 4     | 3     | 2        | -22         |
| 3     | 10   | 5     | 4     | 2        | -23         |
| 4     | 11   | 5     | 4     | 3        | -25         |
| 5     | 12   | 6     | 5     | 3        | -27         |

### Wisdom Training
| Level | Wisdom | Speed | Skill Pt | Energy Change |
|-------|--------|-------|----------|---------------|
| 1     | 8      | 3     | 4        | +5 (recovery) |
| 2     | 9      | 3     | 4        | +5            |
| 3     | 10     | 4     | 4        | +5            |
| 4     | 11     | 4     | 5        | +5            |
| 5     | 12     | 5     | 5        | +5            |

**Key:** Wisdom is the only training that recovers energy instead of consuming it.
Stamina training has slightly lower energy cost than Speed/Guts.

### Training Level Progression (URA Scenario)

- Training level increases by 1 for every 4 completed sessions of the same type.
- Summer camp sessions do NOT count toward level progression.
- Max level is 5 (requires 16 sessions of the same type, though summer camp gives free Lv5).

---

## 3. Mood (Yaruki / Motivation) System

### Mood Levels and Training Multipliers

| Mood       | Japanese    | Training Multiplier | Race Stat Bonus |
|------------|-------------|---------------------|-----------------|
| Great      | Zetsu-kouchou | +20% (x1.2)      | +4% stats       |
| Good       | Kouchou       | +10% (x1.1)      | +2% stats       |
| Normal     | Futsuu        | 0% (x1.0)        | 0%              |
| Bad        | Fuchou        | -10% (x0.9)      | -2% stats       |
| Awful      | Zetsu-fuchou  | -20% (x0.8)      | -4% stats       |

### Mood Effect Bonus (Support Card Stat)

Some support cards have a "Mood Effect Up" stat (e.g., 30%, 60%).
This modifies HOW MUCH the mood multiplier affects training:

```
Effective Mood Modifier = Base Mood Modifier * (1 + Sum of Mood Effect Bonuses)
```

Example: Great mood (+0.20 base) with 60% total mood effect bonus from cards:
```
= 0.20 * (1 + 0.60) = 0.32, so training multiplier = 1.32x instead of 1.20x
```

**Important:** Mood effect bonus also amplifies the PENALTY from bad moods.
Bad mood with 60% mood effect: -0.10 * (1 + 0.60) = -0.16, so 0.84x multiplier.

### How to Change Mood

- **Recreation (Go Out):** Raises mood by 1 level (2 with Karaoke). Costs one turn.
- **Events:** Many events have mood-raising/lowering outcomes.
- **Race wins:** Often raise mood.
- **Race losses:** Can lower mood.
- **Training failure:** Can lower mood.

### Strategy

- Start the run at Great mood. Fix it in turns 1-2 if Normal or worse.
- Maintain Great throughout. The 20% cumulative bonus over 72 turns is massive.
- Recreation is worth the turn if mood drops to Normal or below (20% difference).
- Never train in Bad/Awful mood unless absolutely forced.

---

## 4. Bond / Friendship Mechanics

### Two Separate Systems

1. **Bond Level** (persistent across careers): Relationship between player and character.
   Increases by 14 points per career completion. Max level 12 (5000 bond points).
2. **Friendship Gauge** (per-career): Bond between trainee and each support card.
   This is the one that matters for training bonuses.

### Friendship Gauge Thresholds

| Gauge Level | Visual       | Effect                                      |
|-------------|--------------|---------------------------------------------|
| 0-79%       | Green/Blue   | No friendship training                      |
| 80-100%     | Orange       | Friendship Training unlocked                |
| 100%        | Orange (max) | Maximum friendship training bonus           |

### How Friendship Gauge Increases

- **Training together:** +7 base friendship per session (varies by card).
- **Charming status:** Grants +2 additional friendship per session.
- **Events:** Support card events can give large friendship boosts.
- **Initial Friendship Gauge:** Some cards start with partial fill (support card skill).

### Friendship Training

When a support card with 80%+ friendship is present in their specialty training:
- The training tile glows **rainbow**.
- A large friendship bonus multiplier is applied to ALL stat gains from that tile.
- Multiple cards with friendship training active on the same tile stack multiplicatively.

### Friendship Training Formula

The friendship training multiplier for each participating card:

```
Friendship Multiplier = (1 + Friendship Bonus%) * (1 + Unique Friendship Bonus%)
```

These multiply together if multiple friendship-trained cards are on the same tile.

Example: Card with 35% Friendship Bonus and 10% Unique Friendship Bonus:
```
= 1.35 * 1.10 = 1.485x (NOT 1.45x -- they multiply, not add)
```

Two such cards on one tile:
```
= 1.485 * 1.485 = 2.205x
```

### Friendship Training Cap

The **pure training stat increase** from friendship training is capped at 100 per stat.
However, additional increases from scenario-specific training corrections are NOT capped.

### Strategy

- **Early game (turns 1-24):** Prioritize building friendship on all support cards.
  Train wherever the most support cards are stacked, regardless of stat type.
- **Goal:** Get all support cards to 80% (orange) by Early June of Classic Year (~turn 24).
- **Support cards with "Specialty Priority"** appear more often in their type's training,
  making them easier to build friendship on.
- **Support cards with "Initial Friendship"** start partially filled, requiring fewer turns.

---

## 5. Support Card Stacking Effects

### Headcount Bonus

Every support card present during training adds a flat multiplicative bonus:

```
Headcount Multiplier = 1 + (0.05 * Number of Support Cards)
```

| Cards Present | Multiplier |
|--------------|------------|
| 0            | 1.00x      |
| 1            | 1.05x      |
| 2            | 1.10x      |
| 3            | 1.15x      |
| 4            | 1.20x      |
| 5            | 1.25x      |
| 6            | 1.30x      |

**Note:** Director and Reporter do NOT count toward headcount bonus.

### Training Effect Bonus

Each support card has a "Training Effect Up" percentage. All cards present in the
training contribute their training effect, even if it is NOT their specialty training:

```
Training Effect Multiplier = 1 + Sum of all Training Effect Up percentages
```

Example: Three cards with 5%, 10%, and 5% training effect:
```
= 1 + 0.05 + 0.10 + 0.05 = 1.20x
```

### Stat Bonus (Flat Addition)

Support cards can provide flat stat bonuses (e.g., +1 Speed). These are added to the
base value BEFORE all multipliers are applied:

```
Effective Base = Training Base Value + Sum of Support Card Stat Bonuses
```

### Stacking Strategy

The power of stacking comes from multiplicative interactions:
- 4 speed-type cards with maxed friendship on speed training = massive rainbow training.
- A single rainbow with 4 cards can yield 80-100+ stat points in one turn.
- Late game, always prioritize tiles with the most rainbow-active cards.

---

## 6. Rainbow and Gold Training

### Rainbow Training

**Trigger:** A support card with 80%+ friendship gauge trains in their specialty stat.
**Visual:** The training tile glows rainbow.
**Bonus:** Friendship training multiplier applied (see Section 4).
**Stacking:** Multiple rainbow cards on one tile = multiplicative friendship bonuses.

A "triple rainbow" or "quad rainbow" (3-4 friendship-active cards on one tile) can
yield 80-120+ points in a single stat, which is one of the largest possible gains.

### Gold Training

**Trigger:** Scenario-specific mechanic. In some scenarios, gold training appears when
specific conditions are met (e.g., high scenario gauge, special character present).
**Bonus:** Varies by scenario but typically provides additional stat gains or special effects.

In URA Finale, gold indicators are less prominent compared to later scenarios.

### Decision Priority

1. Triple/Quad rainbow with high energy -> Always take it
2. Double rainbow -> Almost always take it
3. Single rainbow -> Take it unless a much better option exists
4. Gold training -> Scenario-dependent, usually good
5. High stacking (3+ cards, no rainbow) -> Good for bond building early
6. G1 race available -> Often better than single rainbow

---

## 7. Energy Management

### Energy Basics

- Energy ranges from 0 to 100.
- Energy below 50 triggers failure rate on training.
- Energy below 30 dramatically increases failure rate.
- Wisdom training restores +5 energy (does not consume energy).

### Energy Costs by Training Type (at Level 1)

| Training | Energy Cost |
|----------|-------------|
| Speed    | -21         |
| Stamina  | -19         |
| Power    | -19         |
| Guts     | -21         |
| Wisdom   | +5          |

Energy costs increase as training level increases (approximately +1-2 per level).

### Rest Mechanics

Resting uses one turn and recovers energy randomly:

| Outcome        | Energy Recovery | Probability  |
|----------------|-----------------|--------------|
| All Refreshed  | +50             | Most common  |
| Well-Rested!   | +70             | Less common  |
| Sleep Deprived | +30             | Least common |

**Sleep Deprived risk:** Can inflict "Night Owl" condition (-10 energy randomly each turn).
Night Owl can be cured at the Infirmary or sometimes by resting again.

### Optimal Energy Strategy

- **Keep energy above 50** at all times to avoid failure rate.
- **Rest when failure rate reaches 15-25%.** Do not push past 25%.
- **Use Wisdom training** as a soft rest: gains stats + recovers 5 energy.
- **Prepare for Summer Camp:** Enter July with full energy (all training becomes Lv5).
- **Prefer event-based recovery** over resting -- events that restore energy are free turns.
- **Don't rest at 70+ energy** -- risk capping and wasting recovery.

---

## 8. Failure Rate Mechanics

### Failure Rate Threshold

- Energy >= 50: 0% failure rate (base).
- Energy < 50: Failure rate scales up as energy decreases.
- Energy near 0: Approaches 100% failure rate.

### Approximate Failure Rate Table

| Energy Level | Approximate Failure Rate |
|-------------|-------------------------|
| 50+         | 0%                      |
| 40-49       | ~3-8%                   |
| 30-39       | ~10-20%                 |
| 20-29       | ~25-40%                 |
| 10-19       | ~50-70%                 |
| 0-9         | ~80-100%                |

### Failure Rate Modifiers

| Condition       | Effect                  |
|-----------------|-------------------------|
| Practice Perfect | -2% failure rate        |
| Practice Poor    | +2% failure rate        |
| Shining Brightly | -5% (Super Creek only) |
| Under the Weather| +5% (Super Creek only) |

### Consequences of Failure

- Training fails -- no stat gains for the turn.
- Trainee sent to infirmary.
- Can inflict negative conditions (Practice Poor, stat debuffs).
- Mood may decrease.
- Effectively wastes 2+ turns (failed turn + recovery).

### Strategy

- **Below 10% failure:** Generally safe to train.
- **10-15% failure:** Acceptable risk if the training is very valuable (rainbow, high stack).
- **15-25% failure:** Rest unless triple rainbow or comparably irreplaceable training.
- **25%+ failure:** Always rest. No training is worth this risk.
- **Wisdom training** at 40-49 energy is excellent: no failure risk, recovers energy.

---

## 9. Stat Caps and Diminishing Returns

### Hard Cap

- Each stat is capped at **1200** during training.
- When a stat exceeds 1200, all further gains are **halved**.
- Additionally, the total increase per training session for an over-cap stat is limited to **50**.

### Practical Impact

Most builds target 1000-1200 for primary stats. Going above 1200 is inefficient due to
the halving penalty, so late-game training should shift to uncapped stats.

---

## 10. Event Choice Optimization

### Event Databases

- **GameTora Training Event Helper:** Select character + support cards, browse events.
  https://gametora.com/umamusume/training-event-helper
- **Game8 Event Choice Checker:** Search by event name or keyword.
  https://game8.co/games/Umamusume-Pretty-Derby/archives/539000
- **UmaEvents.io:** Screen-capture based event lookup (2000+ events, JP/CN servers).
  https://umaevents.io/en

### Generic Event Probabilities (from umareference.com)

| Event                    | Occurrence Rate | Notes                              |
|--------------------------|-----------------|------------------------------------|
| Fast Learner opportunity | 40% per run     | 10% chance of actually getting it  |
| Slow Metabolism debuff   | 10%             | From +30 energy event choice       |
| Extra Training event     | 6%              | After successful training          |
| Debuff cure option       | 20%             | Gives +5 stat, -5 energy           |

### General Event Choice Rules

1. **Energy recovery choices:** Prefer when energy < 50 and no debuff risk.
2. **Stat gain choices:** Prefer when energy is comfortable.
3. **Mood improvement choices:** Prefer when mood is Normal or below.
4. **Bond/friendship choices:** Prefer in early game (turns 1-24).
5. **Skill hint choices:** Prefer when the skill is relevant to build.
6. **Choices with debuff risk:** Avoid unless the positive outcome is very strong.

---

## 11. URA Finale Scenario Specifics

### Overview

- Original scenario, released at JP launch (Feb 2021), updated Dec 2022.
- 72 turns across 3 in-game years (Junior, Classic, Senior).
- Training level increases every 4 sessions of the same type.
- All stats cap at 1200.

### URA Finale Races

- 3 races: Qualifier, Semifinal, Finals.
- One training turn before each race.
- Must win all 3 to get the best ending.
- Race distance based on most-trained distance during the career.

### Happy Meek Duel Mechanic (post-Dec 2022)

- Happy Meek randomly appears at training facilities.
- Orange "Duel" mark on her icon triggers a duel event.
- Winning duels levels up Happy Meek (max level).
- Max-level Happy Meek appears as a stronger opponent in URA Finals.
- Beating max-level Happy Meek improves awards and increases chance of URA Finale spark.

### Fan Count Milestones

| Fans      | Reward                                      |
|-----------|---------------------------------------------|
| 50,000    | Lv1 skill hint + 20 Skill Points + 20 Wisdom |
| 100,000   | 30 Skill Points (end of year 2)             |
| 240,000   | 30 Skill Points (end of year 3)             |

### Summer Camp

- July-August of years 2 and 3.
- ALL training automatically becomes **Level 5**.
- Sessions during summer camp do NOT count toward training level progression.
- **Critical:** Enter summer camp with full energy and Great mood for maximum gains.

### Scenario Link Character

- Aoi Kiryuin is the URA Finale scenario link character.
- Using her support cards strengthens training events in this scenario.

### URA Phase Strategy

| Phase              | Turns   | Priority                                    |
|--------------------|---------|---------------------------------------------|
| Junior Year        | 1-24    | Bond building, mood management, light stats  |
| Classic Year       | 25-48   | Friendship training, main stat push          |
| Senior Year        | 49-72   | Pure stat maximization, race preparation     |
| URA Finale         | 73-75   | Win all 3 races                              |

---

## 12. Key Formulas Summary (Quick Reference)

```
STAT GAIN = floor(
  (Base + Card_Stat_Bonus)
  * (1 + Growth_Rate)
  * (1 + Mood_Mod * (1 + Mood_Effect_Sum))
  * (1 + Training_Effect_Sum)
  * (1 + 0.05 * Num_Cards)
  * Product_of(Friendship_Multipliers)
)

FRIENDSHIP_MULTIPLIER_per_card = (1 + Friendship_Bonus) * (1 + Unique_Friendship_Bonus)

HEADCOUNT_BONUS = 1 + 0.05 * number_of_support_cards

MOOD_EFFECT = Mood_Base * (1 + Sum_of_Mood_Effect_Up)
  where Mood_Base: Great=+0.20, Good=+0.10, Normal=0, Bad=-0.10, Awful=-0.20

STAT_CAP: 1200 (gains halved above 1200, max +50 per session above cap)

FRIENDSHIP_TRAINING_CAP: 100 per stat (pure training increase only)

REST_RECOVERY: 30 / 50 / 70 (random, 50 most common)

FAILURE_THRESHOLD: Energy < 50 triggers failure rate
```
