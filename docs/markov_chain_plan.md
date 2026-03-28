# Markov Chain / Data-Driven Decision Engine — Design Notes

## Context

Exploring whether a Markov Chain (MDP) approach could replace or augment the
rule-based scorer. After analysis, the conclusion is that the core ideas can
be incorporated into the existing scorer without a separate Markov model,
at least until enough logged data accumulates to justify one.

## Original Idea: Full MDP

Model the career as a Markov Decision Process:
- **States**: Discretized game state (turn phase, stat buckets, energy, mood, tile quality, bond status)
- **Actions**: 8 base actions (train ×5, rest, race, shop)
- **Transitions**: P(s'|s,a) learned from logged gameplay
- **Reward**: Sparse terminal reward from RunResult (final stats + goals)

### State Discretization

| Dimension | Bins | Values |
|-----------|------|--------|
| Turn phase | 3 | junior / classic / senior |
| Each stat progress (×5) | 4 | low / on-track / target / excess (relative to RunSpec) |
| Energy | 4 | critical / low / ok / full |
| Mood | 5 | terrible through great (already discrete) |
| Best tile quality | 3 | weak / decent / exceptional (by total_gain) |
| Bond status | 2 | building / maxed |

Theoretical state space: ~184K states. Tractable for tabular methods but
sparse with limited data.

### Cold Start Problem

The main blocker. Options considered:

1. **Simulation** — build lightweight game sim from known mechanics, run thousands of careers
2. **Log aggressively** — DecisionLogger already collects data, need ~50+ runs for baseline
3. **Manual replay** — OCR past manual gameplay into training data
4. **Pre-populate from game math** (best option) — use known stat gain formulas, energy costs, etc. to fill transition table analytically. Only learn stochastic parts (events, failure outcomes) from real data.

### Why We Deferred This

- Not enough logged data yet
- The rule-based scorer handles ~90% of decisions well
- The piecewise utility in RunSpec already captures most of what value iteration would learn
- Adding the Markov model is significant infrastructure for marginal early-stage gains

## Consumable Item Modeling

### The Problem

Items create compound actions. Instead of just "train speed", the bot must
consider "use ankle weights → train speed" or "use vita → train speed".

### Valid Item + Action Pairings

| Item Category | Pairs With | Effect |
|---|---|---|
| Ankle Weights / Megaphones / Charm | TRAIN only | Training gain multiplier (1.2×–1.6×), or 0% failure |
| Vita drinks (20/40/65) | TRAIN or REST | Energy restore → enables training when otherwise too low |
| Cupcakes | TRAIN | Mood +1/+2 → better mood multiplier on training |
| Scrolls / Manuals | standalone | Direct stat gain, no action pairing needed |
| Hammers | RACE only | Race stat gain boost (+20%/+35%) |
| Condition cures | standalone | Remove debuff |

~20 valid compound actions, not 80 (most combos are nonsensical).

### MDP Approach (if we build it later)

Add to discretized state:
- **Capability flags** (not individual counts): `has_training_boost`, `has_energy_restore`, `has_mood_restore`, `has_race_boost`
- **Active boost turns remaining**: 0, 1, 2, 3+

Compound action space:
```
TRAIN_{stat}              ×5  (base training)
BOOST_TRAIN_{stat}        ×5  (use best training item + train)
VITALIZE_TRAIN_{stat}     ×5  (use vita + train)
REST                      ×1
RACE                      ×1
BOOST_RACE                ×1  (use hammer + race)
USE_CURE                  ×1
USE_STAT_ITEM             ×1
```

Pre-population works because item effects are deterministic multipliers:
```
P(s' | s, BOOST_TRAIN_speed) = P(s' | s, TRAIN_speed) with gain × item_multiplier
```

Value iteration naturally learns optimal item timing — it discovers that
using weights on a rainbow tile with 3 stacked cards yields more value than
using them on a mediocre tile, without hardcoded `save_for` rules.

### Recommended Approach: Extend the Existing Scorer

Instead of a separate Markov model, evaluate item+action pairs inside the
current scoring loop:

```
for each tile:
    base_score     = score(tile, state, no_item)
    boosted_score  = score(tile, state, best_training_item)
    vitalized_score = score(tile, state, vita)   # only if energy is bottleneck
→ pick best (tile, item_or_none) pair
```

How items modify scoring inputs:
- **Megaphone/Weights/Charm**: Multiply tile `stat_gains` by item multiplier, zero `failure_rate` if charm. Re-score.
- **Vita**: Set `energy = energy + vita_amount`, removing energy penalty and potentially flipping rest→train.
- **Cupcake**: Set `mood` one tier higher, increasing mood multiplier.

**When to use** heuristic: compare `score(best_tile, with_item) - score(best_tile, without_item)`. Use the item when the delta exceeds a threshold, meaning the current tile is high-value enough to justify spending the consumable. This generalizes the current `save_for: "summer_camp"` hardcoding to "save for any tile scoring above the Nth percentile."

This captures ~90% of what the MDP would learn about item timing, with zero
data requirements and zero new infrastructure.

## Relationship to Neural Net Plan

See `docs/neural_net_plan.md`. The neural net and Markov approaches solve
similar problems differently:

- **Neural net**: Learns a policy π(s) → a directly from (state, action, outcome) tuples via supervised learning. Needs ~50-200 runs of data. Doesn't model transitions explicitly.
- **Markov MDP**: Learns a transition model P(s'|s,a) and derives policy via value iteration. Can be pre-populated from game math. Models transitions explicitly, which is more interpretable.
- **Scorer enhancements**: No learning, just better heuristics. Works day one. The item-pair evaluation described above is the immediate win.

Progression path:
1. **Now**: Enhance scorer with item-pair evaluation
2. **After ~50 runs**: Train neural net (imitation learning on scorer decisions)
3. **After ~200 runs**: Consider MDP with learned transitions refining the pre-populated model
4. **Ongoing**: All three can coexist in the tier system (MDP/neural as Tier 0.5, scorer as Tier 1 fallback)

## Files That Would Change

### Scorer enhancement (item pairs)
- `uma_trainer/decision/scorer.py` — add item-aware scoring path
- `uma_trainer/decision/shop_manager.py` — expose item candidates for scorer to evaluate

### Full MDP (future)
```
uma_trainer/markov/
├── __init__.py
├── state_discretizer.py   # GameState → DiscreteState (hashable tuple)
├── chain.py               # TransitionModel: P(s'|s,a) + R(s,a)
├── collector.py           # Extract transitions from decision_log after runs
├── planner.py             # Value iteration: V*(s), π*(s)
├── scorer.py              # MarkovScorer: Tier 0.5 in DecisionEngine
└── persistence.py         # Save/load transition tables + value functions
```
