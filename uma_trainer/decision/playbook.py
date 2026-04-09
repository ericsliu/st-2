"""Playbook: YAML-driven turn schedule with conditional overrides.

A Playbook sits above the existing decision components (scorer, race_selector)
and answers "what action this turn?" before dynamic scoring kicks in.

Priority chain:
1. Fixed schedule (turn X -> recreation)
2. Conditional overrides (race unless double-friendship training available)
3. Dynamic fallback (existing scorer + race_selector for flexible turns)

When no playbook is loaded, the bot behaves exactly as before.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from uma_trainer.types import ActionType, BotAction, GameState

if TYPE_CHECKING:
    from uma_trainer.decision.race_selector import RaceSelector
    from uma_trainer.decision.scorer import TrainingScorer

logger = logging.getLogger(__name__)

STRATEGIES_DIR = str(Path(__file__).resolve().parent.parent.parent / "data" / "strategies")

# ---------------------------------------------------------------------------
# Config dataclasses (parsed from YAML)
# ---------------------------------------------------------------------------

ACTION_MAP = {
    "recreation": ActionType.GO_OUT,
    "race": ActionType.RACE,
    "train": ActionType.TRAIN,
    "rest": ActionType.REST,
    "flex": ActionType.WAIT,  # WAIT = fall through to dynamic logic
}


@dataclass
class TurnAction:
    """One scheduled action for a specific turn."""
    action: str = "flex"          # "recreation" | "race" | "train" | "rest" | "flex"
    conditions: list[str] = field(default_factory=list)  # e.g. ["unless_double_friendship"]
    fallback: str = "flex"        # what to do if conditions block
    note: str = ""

    @property
    def action_type(self) -> ActionType:
        return ACTION_MAP.get(self.action, ActionType.WAIT)

    @property
    def fallback_type(self) -> ActionType:
        return ACTION_MAP.get(self.fallback, ActionType.WAIT)


@dataclass
class ScheduleBlock:
    """A repeating pattern of actions for a turn range."""
    start_turn: int = 0
    end_turn: int = 78
    pattern: list[str] = field(default_factory=list)
    repeat: bool = False
    flex_overrides: dict[str, list[str]] = field(default_factory=dict)
    # flex_overrides maps action names to skip conditions:
    # {"race": ["double_friendship_training"]}
    # means: on "race" pattern slots, skip if double_friendship_training is available


@dataclass
class RecreationSource:
    """One source of recreation uses (e.g., Team Sirius or Riko)."""
    total: int = 0
    special_index: int | None = None  # nth use has special effect
    available_after: str | None = None  # event name gating availability


@dataclass
class RecreationPolicy:
    """When and why to use recreation."""
    enabled: bool = False
    sources: dict[str, RecreationSource] = field(default_factory=dict)
    energy_recovery: bool = False
    mood_recovery: bool = False


@dataclass
class RacePolicy:
    """Which races to take and when to skip."""
    g1_policy: str = "always"           # "always" | "skip_for_training"
    g2_policy: str = "default"          # "default" | "skip_for_training"
    g3_policy: str = "default"
    skip_for: list[str] = field(default_factory=list)  # conditions to skip for
    rival_speed_target: int = 0         # min speed for rival races


@dataclass
class SkillPolicy:
    """When to buy skills."""
    defer_until_sp: int = 0     # don't buy until SP >= this
    defer_until_turn: int = 0   # don't buy until turn >= this


@dataclass
class FriendshipDeadline:
    """Deadline for a friendship unlock."""
    by_turn: int = 0
    on_miss: str = "warn"  # "restart" | "warn" | "ignore"


@dataclass
class FriendshipPolicy:
    """Friendship gauge priorities and deadlines."""
    priority_order: list[str] = field(default_factory=list)
    deadlines: dict[str, FriendshipDeadline] = field(default_factory=dict)


@dataclass
class PlaybookConfig:
    """Full playbook definition, parsed from YAML."""
    id: str = ""
    name: str = ""
    scenario: str = "trackblazer"
    runspec: str = ""
    max_turns: int = 78

    schedule: dict[int, TurnAction] = field(default_factory=dict)
    schedule_blocks: list[ScheduleBlock] = field(default_factory=list)

    recreation: RecreationPolicy = field(default_factory=RecreationPolicy)
    race: RacePolicy = field(default_factory=RacePolicy)
    skills: SkillPolicy = field(default_factory=SkillPolicy)
    friendship: FriendshipPolicy = field(default_factory=FriendshipPolicy)
    item_priorities: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Runtime state (not from YAML)
# ---------------------------------------------------------------------------

@dataclass
class RecreationTracker:
    """Tracks recreation usage across sources at runtime."""
    uses_remaining: dict[str, int] = field(default_factory=dict)
    total_used: int = 0
    # Source priority order and availability gates (set from policy)
    _priority_order: list[str] = field(default_factory=list)
    _availability_gates: dict[str, str | None] = field(default_factory=dict)
    _special_indices: dict[str, int | None] = field(default_factory=dict)

    @classmethod
    def from_policy(cls, policy: RecreationPolicy) -> RecreationTracker:
        remaining = {name: src.total for name, src in policy.sources.items()}
        priority = list(policy.sources.keys())
        gates = {name: src.available_after for name, src in policy.sources.items()}
        specials = {name: src.special_index for name, src in policy.sources.items()}
        return cls(
            uses_remaining=remaining,
            total_used=0,
            _priority_order=priority,
            _availability_gates=gates,
            _special_indices=specials,
        )

    def best_source(self, current_turn: int, scenario=None) -> str | None:
        """Pick the best recreation source considering priority and availability.

        Returns the source name, or None if no sources have remaining uses.
        """
        for name in self._priority_order:
            if self.uses_remaining.get(name, 0) <= 0:
                continue
            gate = self._availability_gates.get(name)
            if gate and scenario:
                gate_turns = scenario.get_event_turns(gate)
                if gate_turns and current_turn < min(gate_turns):
                    continue  # Not yet available
            return name
        return None

    def on_recreation_used(self, source: str | None = None) -> None:
        """Record a recreation use. Decrements from the specified source,
        or from the first source with remaining uses."""
        if source and source in self.uses_remaining:
            self.uses_remaining[source] = max(0, self.uses_remaining[source] - 1)
        elif not source:
            for name in self._priority_order or self.uses_remaining:
                if self.uses_remaining.get(name, 0) > 0:
                    self.uses_remaining[name] -= 1
                    source = name
                    break
        self.total_used += 1
        logger.info(
            "Recreation used: source=%s, total=%d, remaining=%s",
            source or "unknown", self.total_used, self.uses_remaining,
        )

    def is_special_use(self, source: str) -> bool:
        """Check if the next use of this source would be the special one."""
        special_idx = self._special_indices.get(source)
        if special_idx is None:
            return False
        total_for_source = 0
        # Count how many have been used: original total - remaining
        # We need the original total, which we can infer
        # special_index is 1-based (7th use), total_used for this source = original - remaining
        remaining = self.uses_remaining.get(source, 0)
        # The next use will be use number (original - remaining + 1)
        # We don't store original, but we can check: remaining == 1 means
        # the next use is the last one. For special_index=7 with total=7,
        # the 7th use happens when remaining==1.
        if special_idx is not None and remaining == 1:
            return True
        return False

    @property
    def any_remaining(self) -> bool:
        return any(v > 0 for v in self.uses_remaining.values())

    def remaining_for(self, source: str) -> int:
        return self.uses_remaining.get(source, 0)


# ---------------------------------------------------------------------------
# Condition evaluators
# ---------------------------------------------------------------------------

def _has_double_friendship_training(state: GameState) -> bool:
    """Check if any training tile has 2+ support cards with potential
    friendship gains (i.e. not all bonds maxed on those cards)."""
    for tile in (state.training_tiles or []):
        friend_cards = sum(
            1 for c in tile.support_cards
            if c.bond_level < 80
        )
        if friend_cards >= 2:
            return True
    return False


def _has_strong_training(state: GameState) -> bool:
    """Check if any training tile has exceptional stat gains (>= 30 total)."""
    for tile in (state.training_tiles or []):
        if tile.total_stat_gain >= 30:
            return True
    return False


CONDITION_EVALUATORS: dict[str, callable] = {
    "double_friendship_training": _has_double_friendship_training,
    "strong_training": _has_strong_training,
}


# ---------------------------------------------------------------------------
# PlaybookEngine
# ---------------------------------------------------------------------------

class PlaybookEngine:
    """Decision glue between the playbook schedule and existing components."""

    def __init__(
        self,
        playbook: PlaybookConfig,
        scorer: TrainingScorer | None = None,
        race_selector: RaceSelector | None = None,
        scenario=None,
    ) -> None:
        self.playbook = playbook
        self.scorer = scorer
        self.race_selector = race_selector
        self._scenario = scenario
        self.rec_tracker = RecreationTracker.from_policy(playbook.recreation)

    def decide_turn(self, state: GameState) -> BotAction:
        """Main entry point. Returns the action for this turn.

        Returns WAIT action_type to signal "fall through to dynamic logic".
        """
        turn = state.current_turn
        scheduled = self._get_scheduled_action(turn)

        if scheduled is None or scheduled.action == "flex":
            return BotAction(action_type=ActionType.WAIT, reason="playbook: flex turn")

        # Check conditions — if any condition is met, use fallback
        if scheduled.conditions:
            for cond in scheduled.conditions:
                # "unless_X" means: if X is true, skip this action
                negate = cond.startswith("unless_")
                eval_key = cond.removeprefix("unless_")
                evaluator = CONDITION_EVALUATORS.get(eval_key)
                if evaluator:
                    result = evaluator(state)
                    if negate and result:
                        logger.info(
                            "Playbook: condition '%s' triggered on turn %d, using fallback '%s'",
                            cond, turn, scheduled.fallback,
                        )
                        return BotAction(
                            action_type=scheduled.fallback_type,
                            reason=f"playbook: {scheduled.fallback} (condition: {cond})",
                        )
                    elif not negate and not result:
                        logger.info(
                            "Playbook: condition '%s' not met on turn %d, using fallback '%s'",
                            cond, turn, scheduled.fallback,
                        )
                        return BotAction(
                            action_type=scheduled.fallback_type,
                            reason=f"playbook: {scheduled.fallback} (condition: {cond} not met)",
                        )

        reason = f"playbook: {scheduled.action}"
        if scheduled.note:
            reason += f" ({scheduled.note})"

        return BotAction(action_type=scheduled.action_type, reason=reason)

    def wants_recreation(self, turn: int) -> bool:
        """Check if the playbook wants recreation confirmed this turn."""
        if not self.playbook.recreation.enabled:
            return False
        scheduled = self._get_scheduled_action(turn)
        if scheduled and scheduled.action == "recreation":
            return True
        return False

    def on_recreation_completed(self, source: str | None = None, turn: int = 0) -> None:
        """Call after a recreation is confirmed and completed."""
        if not source:
            source = self.rec_tracker.best_source(turn, self._scenario)
        is_special = source and self.rec_tracker.is_special_use(source)
        self.rec_tracker.on_recreation_used(source)
        if is_special:
            logger.info("SPECIAL recreation used from %s! (max energy recovery)", source)
        logger.info(
            "Recreation completed: source=%s, total=%d, remaining=%s",
            source or "unknown", self.rec_tracker.total_used, self.rec_tracker.uses_remaining,
        )

    def check_friendship_deadline(self, turn: int, skip_cards: set[str] | None = None) -> str | None:
        """Check if any friendship deadline has been missed.
        Returns "restart" if a restart-triggering deadline was missed,
        "warn" if a warning deadline was missed, or None.
        skip_cards: card names to skip (e.g. bond already confirmed unlocked).
        """
        for name, deadline in self.playbook.friendship.deadlines.items():
            if skip_cards and name in skip_cards:
                continue
            if turn >= deadline.by_turn:
                remaining = self.rec_tracker.remaining_for(name)
                total = self.playbook.recreation.sources.get(name, RecreationSource()).total
                # If no recreations have been used by the deadline, friendship wasn't unlocked
                if remaining == total and total > 0:
                    logger.warning(
                        "Friendship deadline missed: %s by turn %d (action: %s)",
                        name, deadline.by_turn, deadline.on_miss,
                    )
                    return deadline.on_miss
        return None

    def should_defer_skills(self, skill_pts: int, turn: int) -> bool:
        """Check if skill buying should be deferred per playbook policy."""
        policy = self.playbook.skills
        if policy.defer_until_sp > 0 and skill_pts < policy.defer_until_sp:
            return True
        if policy.defer_until_turn > 0 and turn < policy.defer_until_turn:
            return True
        return False

    def _get_scheduled_action(self, turn: int) -> TurnAction | None:
        """Resolve the scheduled action for a turn.

        Priority: explicit schedule entry > schedule block pattern > None.
        """
        # 1. Check explicit schedule
        if turn in self.playbook.schedule:
            return self.playbook.schedule[turn]

        # 2. Check schedule blocks
        for block in self.playbook.schedule_blocks:
            if block.start_turn <= turn <= block.end_turn and block.pattern:
                offset = turn - block.start_turn
                pattern_len = len(block.pattern)

                if block.repeat:
                    idx = offset % pattern_len
                elif offset < pattern_len:
                    idx = offset
                else:
                    continue

                action_str = block.pattern[idx]
                conditions = []
                if action_str in block.flex_overrides:
                    conditions = [
                        f"unless_{c}" for c in block.flex_overrides[action_str]
                    ]

                return TurnAction(
                    action=action_str,
                    conditions=conditions,
                    fallback="train" if action_str == "race" else "flex",
                )

        return None


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def _parse_turn_action(raw: dict | str) -> TurnAction:
    """Parse a TurnAction from YAML (can be a string or dict)."""
    if isinstance(raw, str):
        return TurnAction(action=raw)
    return TurnAction(
        action=raw.get("action", "flex"),
        conditions=raw.get("conditions", []),
        fallback=raw.get("fallback", "flex"),
        note=raw.get("note", ""),
    )


def _parse_schedule_block(raw: dict) -> ScheduleBlock:
    return ScheduleBlock(
        start_turn=raw.get("start_turn", 0),
        end_turn=raw.get("end_turn", 78),
        pattern=raw.get("pattern", []),
        repeat=raw.get("repeat", False),
        flex_overrides=raw.get("flex_overrides", {}),
    )


def _parse_recreation_policy(raw: dict) -> RecreationPolicy:
    sources = {}
    for name, src_raw in raw.get("sources", {}).items():
        sources[name] = RecreationSource(
            total=src_raw.get("total", 0),
            special_index=src_raw.get("special_index"),
            available_after=src_raw.get("available_after"),
        )
    return RecreationPolicy(
        enabled=raw.get("enabled", False),
        sources=sources,
        energy_recovery=raw.get("energy_recovery", False),
        mood_recovery=raw.get("mood_recovery", False),
    )


def _parse_race_policy(raw: dict) -> RacePolicy:
    return RacePolicy(
        g1_policy=raw.get("g1_policy", "always"),
        g2_policy=raw.get("g2_policy", "default"),
        g3_policy=raw.get("g3_policy", "default"),
        skip_for=raw.get("skip_for", []),
        rival_speed_target=raw.get("rival_speed_target", 0),
    )


def _parse_skill_policy(raw: dict) -> SkillPolicy:
    return SkillPolicy(
        defer_until_sp=raw.get("defer_until_sp", 0),
        defer_until_turn=raw.get("defer_until_turn", 0),
    )


def _parse_friendship_policy(raw: dict) -> FriendshipPolicy:
    deadlines = {}
    for name, dl_raw in raw.get("deadlines", {}).items():
        deadlines[name] = FriendshipDeadline(
            by_turn=dl_raw.get("by_turn", 0),
            on_miss=dl_raw.get("on_miss", "warn"),
        )
    return FriendshipPolicy(
        priority_order=raw.get("priority_order", []),
        deadlines=deadlines,
    )


def load_playbook(
    name: str,
    strategies_dir: str = STRATEGIES_DIR,
) -> PlaybookEngine:
    """Load a playbook from YAML and return a configured PlaybookEngine."""
    path = Path(strategies_dir) / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Playbook '{name}' not found at {path}")

    raw = yaml.safe_load(path.read_text()) or {}

    # Parse schedule (sparse turn -> action)
    schedule = {}
    for turn_str, action_raw in raw.get("schedule", {}).items():
        schedule[int(turn_str)] = _parse_turn_action(action_raw)

    # Parse schedule blocks
    blocks = [_parse_schedule_block(b) for b in raw.get("schedule_blocks", [])]

    config = PlaybookConfig(
        id=raw.get("id", name),
        name=raw.get("name", name),
        scenario=raw.get("scenario", "trackblazer"),
        runspec=raw.get("runspec", ""),
        max_turns=raw.get("max_turns", 78),
        schedule=schedule,
        schedule_blocks=blocks,
        recreation=_parse_recreation_policy(raw.get("recreation", {})),
        race=_parse_race_policy(raw.get("race", {})),
        skills=_parse_skill_policy(raw.get("skills", {})),
        friendship=_parse_friendship_policy(raw.get("friendship", {})),
        item_priorities=raw.get("item_priorities", []),
    )

    logger.info("Loaded playbook '%s' (%s)", config.name, config.id)
    return PlaybookEngine(config)
