# RunSpec — Run Goal Configuration

A RunSpec defines what a career run is trying to accomplish: stat targets, risk
tolerance, scoring policy, and phase-based weight adjustments. The scorer uses
it to compute piecewise utility for training decisions instead of flat stat
weights.

RunSpec files live in `data/runspecs/` as YAML. The active runspec is set in
`scripts/auto_turn.py` (currently `parent_balanced_v1`).

## File structure

```yaml
id: parent_balanced_v1
name: "Parent Balanced"
run_type: parent_builder       # parent_builder | competitive
description: "Human-readable description of what this runspec optimizes for."
distance_target: medium        # sprint | mile | medium | long
style_target: leader           # front | leader | chaser | closer
```

### stat_targets

Each of the 5 stats (speed, stamina, power, guts, wit) has a piecewise utility
curve. The scorer integrates across tiers when computing the value of a stat
gain.

```yaml
stat_targets:
  speed:
    minimum: 500       # Below this: urgent, high value per point
    target: 600        # The "good enough" level
    excellent: 800     # Diminishing returns above this
    cap: null          # Hard cap — weight zeroed when reached (optional)
    values: [1.2, 0.9, 0.7, 0.4]
    #        ^     ^     ^     ^
    #        |     |     |     +-- above excellent
    #        |     |     +-------- target → excellent
    #        |     +-------------- minimum → target
    #        +-------------------- below minimum
```

**How values work:** When a training tile gives +20 speed and the trainee is
currently at 480 speed (below the 500 minimum), the scorer computes:

- 20 points below minimum at value 1.2 = 24.0 utility

If the trainee were at 590 (between minimum and target):

- 10 points in the min→target tier at 0.9 = 9.0
- 10 points in the target→excellent tier at 0.7 = 7.0
- Total: 16.0 utility

This means the scorer naturally prioritizes stats that are behind their targets.

**cap:** When a stat reaches this value, its weight is zeroed — the scorer
stops investing in it entirely. Useful for stats like stamina/power where
returns drop off sharply past a threshold. Set to `null` or omit for no cap.

### phase_weights

Flat weight overrides applied during specific game phases. These only affect
the fallback scoring path (when OCR stat gains aren't available). Each entry
matches a `phase_alias` defined in the scenario config
(`data/scenarios/trackblazer.yaml`).

```yaml
phase_weights:
  - condition: early_game    # matches scenario phase_aliases
    weights:
      speed: 1.2
      wit: 1.0               # wit early = better training efficiency
  - condition: late_game
    weights:
      speed: 2.0             # speed is king late game
      stamina: 0.5           # diminishing returns
```

Phase aliases for Trackblazer:
- `early_game`: turns 0-23 (Junior year)
- `late_game`: turns 50-71 (late Classic through Senior)

### policy

Tunable knobs for non-stat scoring factors:

```yaml
policy:
  bond_future_value: 0.9       # How much to value future bond returns
  skill_point_value: 0.35      # SP gain weight
  race_progress_value: 0.8     # Race/grade point progress weight
  failure_risk_penalty: 1.4    # Multiplier on failure rate penalty
  energy_preservation: 0.8     # Bias toward conserving energy
  overshoot_penalty: 0.7       # Penalty for exceeding stat targets
```

### constraints

Hard rules that override scoring:

```yaml
constraints:
  must_complete_goal_races: true
  max_failure_rate: 0.12       # Never train above 12% failure
  min_energy_for_training: 50  # Energy floor for training
  rest_energy_threshold: 25    # Rest when energy drops below this
```

## Creating a new runspec

1. Copy `parent_balanced_v1.yaml` to a new file (e.g., `speed_focus_v1.yaml`)
2. Adjust stat targets and values to match your goal
3. Update `scripts/auto_turn.py` line ~65 to load your new runspec

## How the scorer uses runspecs

The scorer resolves stat values in this priority order:

1. **Piecewise utility** (primary path): When OCR reads actual stat gains from
   training tile previews, `runspec.stat_utility(stat, current, gain)` computes
   marginal utility by integrating across the tier boundaries.

2. **Phase weights** (fallback path): When OCR gains aren't available, the
   scorer uses estimated gains multiplied by flat weights. Phase weights from
   the runspec override the base weights during matching game phases.

3. **Stat caps**: After scoring, any stat at or above its cap has its weight
   zeroed — the scorer won't invest further.
