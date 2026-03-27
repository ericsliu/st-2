# RunSpec: Goal-Conditioned Decision Making

## Problem

The current scorer uses flat stat weights (e.g., speed: 1.2, stamina: 1.0) that don't change based on where you are in the run or how your stats compare to what you actually need. A run where you're 400 speed short of your goal should behave very differently from one where you're already past target — but flat weights can't express that.

## Core Idea

Replace flat stat weights with a **RunSpec** — a structured definition of what a run is trying to accomplish. Every decision becomes:

```python
action = choose_action(game_state, run_spec)
```

A RunSpec defines:
- **What kind of run** (parent builder vs. competitive ace)
- **Distance/style targets** (long runner, sprint leader, etc.)
- **Stat targets with tiers** (minimum, target, excellent)
- **How aggressively to play** (risk profile, energy management)

## Stat Target Tiers

Instead of a single weight per stat, define three thresholds with diminishing value:

```
minimum  →  target  →  excellent
```

- **Below minimum**: High urgency. Every point matters a lot.
- **Minimum → target**: Normal value. This is the productive zone.
- **Target → excellent**: Low value. Nice to have but don't chase it.
- **Above excellent**: Near-zero value. Actively avoid training this stat.

Each tier has a `value` coefficient that the scorer multiplies against the raw stat gain. This naturally creates piecewise utility — the bot pivots away from stats it's already strong in.

### Example (parent_long_v1)

```yaml
stat_targets:
  speed:
    minimum: 500
    target: 850
    excellent: 950
    values: [1.0, 0.8, 0.25, 0.05]  # below_min, to_target, to_excellent, above
  stamina:
    minimum: 550
    target: 700
    excellent: 800
    values: [1.2, 0.9, 0.1, 0.0]
  power:
    minimum: 350
    target: 500
    excellent: 600
    values: [0.9, 0.6, 0.15, 0.0]
  guts:
    minimum: 250
    target: 350
    excellent: 450
    values: [0.7, 0.4, 0.1, 0.0]
  wit:
    minimum: 300
    target: 450
    excellent: 550
    values: [0.8, 0.5, 0.15, 0.0]
```

## Derived Features

At each turn, the scorer computes per-stat:
- `deficit_to_minimum`: max(0, minimum - current). If > 0, this stat is critically behind.
- `deficit_to_target`: max(0, target - current). Positive means still productive to train.
- `overshoot_above_target`: max(0, current - target). Training this has diminishing returns.
- `overshoot_above_excellent`: max(0, current - excellent). Training this is wasteful.
- `turns_remaining`: How many turns left in the run.
- `needed_per_turn`: deficit_to_target / turns_remaining. Are we on pace?

These feed into the scoring function so the bot naturally shifts priorities as the run progresses.

## Policy Weights

Beyond stat targets, a RunSpec carries policy-level preferences:

```yaml
policy:
  bond_future_value: 0.9       # How much to value bond-building (high early, decays)
  skill_point_value: 0.35      # Value of skill point gains
  race_progress_value: 0.8     # Value of racing for GP/fans
  failure_risk_penalty: 1.2    # How much to penalize high failure rate tiles
  energy_preservation: 0.7     # How conservative with energy
  overshoot_penalty: 0.8       # Penalty for training stats past excellent
```

## Hard Constraints

Non-negotiable rules that override scoring:

```yaml
constraints:
  must_complete_goal_races: true
  max_acceptable_failure_rate: 0.15   # Don't train if failure > 15% (parent run, safe)
  min_energy_before_training: 45      # Rest instead of training below this
```

For ace runs, these would be looser (willing to accept 30% failure for a rainbow tile, etc.).

## Run Archetypes

Predefined RunSpecs stored in `data/runspecs/`:

| Archetype | Run Type | Notes |
|-----------|----------|-------|
| `parent_long_v1` | Parent builder | Long-distance focus, conservative, speed+stamina priority |
| `parent_sprint_v1` | Parent builder | Sprint focus, speed+power priority |
| `parent_balanced_v1` | Parent builder | Even stat spread, lowest risk |
| `ace_long_v1` | Competitive | High targets, accepts more risk, skill point focus |

Parent archetypes have lower targets and tighter constraints (lower failure tolerance, higher energy thresholds). This matches the current project goal.

## Integration Plan

1. **Define `RunSpec` dataclass** — stat targets, policy weights, constraints
2. **Load from YAML** — `data/runspecs/parent_long_v1.yaml`, etc.
3. **Modify `TrainingScorer._score_tile()`** — replace flat `stat_weights[stat] * gain` with piecewise utility using the tier values and current stats
4. **Add derived feature computation** — deficit/overshoot/pace calculations at the start of each turn
5. **Wire into `DecisionEngine.decide()`** — pass RunSpec alongside GameState
6. **CLI integration** — `python main.py run --runspec parent_long_v1`

The existing `scorer_config` and `stat_weight_overrides` system gets replaced by RunSpec. The scenario system (Trackblazer, URA) stays separate — RunSpec is about *what to achieve*, scenario is about *how the game works*.
