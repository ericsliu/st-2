# Neural Net Decision Engine — Design Plan

## Overview

Replace or augment the rule-based training scorer with a small MLP trained
on logged game data. The model learns which actions lead to better run
outcomes (higher final stats, more goals completed, better rank).

## Architecture

### Input Features (~30 floats per decision)

**Global state:**
- 5 current stats (speed/stamina/power/guts/wit), normalized 0-1 by dividing by 1200
- Energy (0-1, divided by max energy)
- Mood (ordinal: -2 to +2 mapped from terrible→great)
- Turn progress (current_turn / max_turns)
- Phase one-hot (3 values: junior/classic/senior)
- Skill points available, normalized
- Fan count progress (fan_count / current year's fan target)

**Per-tile features (5 tiles x 6 = 30 features):**
- Card count (0-6, normalized)
- is_rainbow (0/1)
- is_gold (0/1)
- has_hint (0/1)
- has_director (0/1)
- total_stat_gain (normalized by dividing by ~60)

**Scenario-specific (optional, zero-padded if N/A):**
- Grade Points earned / target (Trackblazer)
- Consecutive race count (Trackblazer)
- Shop coins available (Trackblazer)
- Is Summer Camp window (0/1)

**Total: ~45-50 features**

### Output

Multi-class classification over possible actions:
- train_speed (0), train_stamina (1), train_power (2), train_guts (3), train_wit (4)
- rest (5)
- race (6)
- shop (7)

Softmax output → pick highest probability, or sample for exploration.

### Model

```
Input (50) → Linear(128) → ReLU → Linear(64) → ReLU → Linear(32) → ReLU → Linear(8) → Softmax
```

- ~15K parameters
- Inference: <0.1ms on CPU
- Framework: PyTorch (already runs on M1 MPS, but CPU is fine for this size)
- Model file: ~60KB saved

### Training Pipeline

**Phase 1: Imitation Learning (supervised)**
- Collect N runs of rule-based scorer decisions
- Each logged turn becomes a training sample: (features, action_taken)
- Weight samples by run quality (better runs → higher weight)
- Train to mimic the scorer, achieving baseline parity
- Loss: cross-entropy

**Phase 2: Outcome-weighted refinement**
- Same data, but weight each sample by a reward signal:
  - `reward = final_stats_total / 6000 + goals_completed / total_goals`
  - Multiply by mood bonus if run ended in good rank
- Actions from high-performing runs get upweighted
- This shifts the model away from just mimicking toward optimizing outcomes

**Phase 3: Online improvement (optional, future)**
- After each completed run, add the logged data to the training set
- Periodically retrain (every 10 runs or nightly)
- Epsilon-greedy exploration: 90% model, 10% rule-based scorer
- Track rolling average of run quality to detect regressions

### Integration

The neural net would slot in as an alternative scorer:

```python
class NeuralScorer:
    def __init__(self, model_path: str, scenario: ScenarioHandler):
        self.model = load_model(model_path)
        self.scenario = scenario
        self.feature_extractor = FeatureExtractor(scenario)

    def best_action(self, state: GameState) -> BotAction:
        features = self.feature_extractor.extract(state)
        action_probs = self.model(features)
        action_idx = action_probs.argmax()
        return self._idx_to_action(action_idx, state)
```

Decision engine would use it as:
- **Tier 0.5**: Neural net (if trained model exists and confidence > threshold)
- **Tier 1**: Rule-based scorer (fallback, always available)
- **Tier 2**: Local LLM
- **Tier 3**: Claude API

Or as a hybrid: rule-based scorer produces tile scores, neural net produces
action probabilities, final decision is a weighted blend.

### Resource Budget

| Component | Memory | Inference |
|-----------|--------|-----------|
| Model weights | ~60KB | <0.1ms CPU |
| Feature extraction | negligible | <0.1ms |
| Training (100 runs) | ~50MB peak | ~5 seconds |
| Training data (1000 runs) | ~10MB on disk | — |

No GPU needed. Training can happen between runs.

### Data Requirements

- Minimum viable: ~50 completed runs (3600 turn decisions)
- Good baseline: ~200 runs (14400 decisions)
- Robust model: ~500+ runs

At ~25 min per run, 50 runs = ~21 hours of bot runtime.
The decision logger should start collecting immediately so data
accumulates while other features are being built.

## File Structure

```
uma_trainer/
├── neural/
│   ├── __init__.py
│   ├── features.py       # FeatureExtractor: GameState → tensor
│   ├── model.py           # MLP definition + load/save
│   ├── trainer.py          # Training loop (offline)
│   └── neural_scorer.py    # NeuralScorer integration
models/
├── neural/
│   └── scorer_v1.pt        # Trained model weights
```

## Open Questions

- Should the model be per-scenario or universal with scenario features?
  Per-scenario is simpler and likely performs better with limited data.
- How to handle the cold-start problem (no data yet)?
  Rule-based scorer runs first, neural net only activates after N runs.
- Should we use the neural net for all decisions or just training tile selection?
  Start with just tile selection (the 90% case), expand later.
- Exploration strategy: epsilon-greedy vs softmax temperature?
  Softmax temperature is smoother and easier to tune.
