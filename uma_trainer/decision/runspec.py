"""RunSpec: goal-conditioned run definitions.

A RunSpec describes what a run is trying to accomplish — stat targets,
risk tolerance, and policy preferences.  The scorer uses it to compute
piecewise utility instead of flat stat weights.

Usage::

    spec = load_runspec("parent_long_v1")
    # or
    spec = load_runspec("parent_long_v1", "data/runspecs")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from uma_trainer.types import StatType, TraineeStats

logger = logging.getLogger(__name__)

RUNSPECS_DIR = "data/runspecs"


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class PhaseWeight:
    """Flat weight override for a game phase."""
    condition: str  # matches a phase alias from the scenario config
    weights: dict[str, float] = field(default_factory=dict)


@dataclass
class StatTarget:
    """Piecewise utility tiers for a single stat."""
    minimum: int = 300
    target: int = 600
    excellent: int = 800
    cap: int | None = None  # hard cap — weight zeroed when stat reaches this
    # Value coefficients per tier (multiplied against raw gain)
    value_below_min: float = 1.0
    value_to_target: float = 0.8
    value_to_excellent: float = 0.25
    value_above_excellent: float = 0.05

    def utility(self, current: int, gain: int) -> float:
        """Compute the marginal utility of gaining `gain` points at `current` level.

        Integrates piecewise across tiers so a +20 that crosses a boundary
        gets partial credit from each tier.
        """
        total = 0.0
        remaining = gain
        pos = current

        # Tier boundaries in order
        tiers = [
            (self.minimum, self.value_below_min),
            (self.target, self.value_to_target),
            (self.excellent, self.value_to_excellent),
            (float("inf"), self.value_above_excellent),
        ]

        for ceiling, value in tiers:
            if remaining <= 0:
                break
            if pos >= ceiling:
                continue
            chunk = min(remaining, ceiling - pos)
            total += chunk * value
            pos += chunk
            remaining -= chunk

        return total


@dataclass
class PolicyWeights:
    """Tunable knobs for non-stat scoring factors."""
    bond_future_value: float = 0.9
    skill_point_value: float = 0.35
    race_progress_value: float = 0.8
    failure_risk_penalty: float = 1.2
    energy_preservation: float = 0.7
    overshoot_penalty: float = 0.8

@dataclass
class HardConstraints:
    """Non-negotiable rules that override scoring."""
    must_complete_goal_races: bool = True
    max_failure_rate: float = 0.15
    min_energy_for_training: int = 45
    rest_energy_threshold: int = 20


@dataclass
class RunSpec:
    """Complete run goal specification."""
    id: str = "default"
    name: str = "Default"
    run_type: str = "parent_builder"    # parent_builder | competitive
    description: str = ""
    distance_target: str = "medium"     # sprint | mile | medium | long
    style_target: str = "leader"        # front | leader | chaser | closer

    stat_targets: dict[str, StatTarget] = field(default_factory=dict)
    phase_weights: list[PhaseWeight] = field(default_factory=list)
    policy: PolicyWeights = field(default_factory=PolicyWeights)
    constraints: HardConstraints = field(default_factory=HardConstraints)
    deck: dict[str, int] = field(default_factory=dict)  # e.g. {"guts": 3, "speed": 1, "wit": 2}

    def __post_init__(self) -> None:
        # Ensure all 5 stats have targets
        for stat in StatType:
            if stat.value not in self.stat_targets:
                self.stat_targets[stat.value] = StatTarget()

    def get_stat_caps(self) -> dict[str, int]:
        """Return {stat_name: cap} for all stats that have a hard cap defined."""
        return {
            name: t.cap
            for name, t in self.stat_targets.items()
            if t.cap is not None
        }

    def get_phase_weights(
        self,
        base_weights: dict[str, float],
        phase_checker: "Callable[[str], bool] | None" = None,
        turn: int = 0,
        max_turns: int = 72,
    ) -> dict[str, float]:
        """Apply phase-based weight overrides to base weights.

        Args:
            base_weights: Starting flat weights.
            phase_checker: Scenario callback that checks phase membership.
            turn: Current turn (fallback when no phase_checker).
            max_turns: Total turns (fallback when no phase_checker).
        """
        weights = dict(base_weights)
        for pw in self.phase_weights:
            applies = False
            if pw.condition == "always":
                applies = True
            elif phase_checker:
                applies = phase_checker(pw.condition)
            else:
                frac = turn / max(max_turns, 1)
                if pw.condition == "early_game":
                    applies = frac < 0.333
                elif pw.condition == "late_game":
                    applies = frac > 0.694
            if applies:
                weights.update(pw.weights)
        return weights

    def stat_utility(self, stat: str, current: int, gain: int) -> float:
        """Compute marginal utility for a stat gain."""
        target = self.stat_targets.get(stat)
        if target is None:
            return gain * 0.5  # Unknown stat, half value
        return target.utility(current, gain)

    def compute_deficits(self, stats: TraineeStats) -> dict[str, dict[str, float]]:
        """Compute deficit/overshoot features for all stats."""
        result = {}
        for stat_name, target in self.stat_targets.items():
            current = stats.get(StatType(stat_name))
            result[stat_name] = {
                "current": current,
                "deficit_to_min": max(0, target.minimum - current),
                "deficit_to_target": max(0, target.target - current),
                "overshoot_target": max(0, current - target.target),
                "overshoot_excellent": max(0, current - target.excellent),
                "pct_to_target": min(1.0, current / max(target.target, 1)),
            }
        return result

    def summary(self) -> dict[str, Any]:
        """Serializable summary for API/dashboard."""
        return {
            "id": self.id,
            "name": self.name,
            "run_type": self.run_type,
            "description": self.description,
            "distance_target": self.distance_target,
            "style_target": self.style_target,
            "stat_targets": {
                stat: {
                    "minimum": t.minimum,
                    "target": t.target,
                    "excellent": t.excellent,
                    "values": [
                        t.value_below_min, t.value_to_target,
                        t.value_to_excellent, t.value_above_excellent,
                    ],
                }
                for stat, t in self.stat_targets.items()
            },
            "policy": {
                "bond_future_value": self.policy.bond_future_value,
                "skill_point_value": self.policy.skill_point_value,
                "race_progress_value": self.policy.race_progress_value,
                "failure_risk_penalty": self.policy.failure_risk_penalty,
                "energy_preservation": self.policy.energy_preservation,
                "overshoot_penalty": self.policy.overshoot_penalty,
            },
            "constraints": {
                "must_complete_goal_races": self.constraints.must_complete_goal_races,
                "max_failure_rate": self.constraints.max_failure_rate,
                "min_energy_for_training": self.constraints.min_energy_for_training,
                "rest_energy_threshold": self.constraints.rest_energy_threshold,
            },
        }


# ── Loading ─────────────────────────────────────────────────────────────────

def _parse_stat_target(raw: dict) -> StatTarget:
    values = raw.get("values", [1.0, 0.8, 0.25, 0.05])
    cap_raw = raw.get("cap")
    return StatTarget(
        minimum=raw.get("minimum", 300),
        target=raw.get("target", 600),
        excellent=raw.get("excellent", 800),
        cap=int(cap_raw) if cap_raw is not None else None,
        value_below_min=values[0] if len(values) > 0 else 1.0,
        value_to_target=values[1] if len(values) > 1 else 0.8,
        value_to_excellent=values[2] if len(values) > 2 else 0.25,
        value_above_excellent=values[3] if len(values) > 3 else 0.05,
    )


def load_runspec(name: str, runspecs_dir: str = RUNSPECS_DIR) -> RunSpec:
    """Load a RunSpec from a YAML file."""
    path = Path(runspecs_dir) / f"{name}.yaml"
    if not path.exists():
        logger.warning("RunSpec %s not found at %s, using defaults", name, path)
        return RunSpec(id=name, name=name)

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    stat_targets = {}
    for stat_name, stat_raw in raw.get("stat_targets", {}).items():
        stat_targets[stat_name] = _parse_stat_target(stat_raw)

    phase_weights = []
    for pw_raw in raw.get("phase_weights", []):
        phase_weights.append(PhaseWeight(
            condition=str(pw_raw.get("condition", "always")),
            weights={k: float(v) for k, v in pw_raw.get("weights", {}).items()},
        ))

    policy_raw = raw.get("policy", {})
    constraints_raw = raw.get("constraints", {})
    deck_raw = raw.get("deck", {})

    return RunSpec(
        id=raw.get("id", name),
        name=raw.get("name", name),
        run_type=raw.get("run_type", "parent_builder"),
        description=raw.get("description", ""),
        distance_target=raw.get("distance_target", "medium"),
        style_target=raw.get("style_target", "leader"),
        stat_targets=stat_targets,
        phase_weights=phase_weights,
        policy=PolicyWeights(**{k: v for k, v in policy_raw.items() if hasattr(PolicyWeights, k)}),
        constraints=HardConstraints(**{k: v for k, v in constraints_raw.items() if hasattr(HardConstraints, k)}),
        deck={k: int(v) for k, v in deck_raw.items()},
    )


def list_runspecs(runspecs_dir: str = RUNSPECS_DIR) -> list[dict[str, str]]:
    """List available RunSpec files with basic info."""
    specs_path = Path(runspecs_dir)
    if not specs_path.exists():
        return []

    results = []
    for yaml_file in sorted(specs_path.glob("*.yaml")):
        try:
            with open(yaml_file) as f:
                raw = yaml.safe_load(f) or {}
            results.append({
                "id": raw.get("id", yaml_file.stem),
                "name": raw.get("name", yaml_file.stem),
                "run_type": raw.get("run_type", "unknown"),
                "description": raw.get("description", ""),
                "file": yaml_file.name,
            })
        except Exception:
            results.append({
                "id": yaml_file.stem,
                "name": yaml_file.stem,
                "run_type": "error",
                "description": f"Failed to parse {yaml_file.name}",
                "file": yaml_file.name,
            })
    return results
