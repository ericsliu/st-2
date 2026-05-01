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

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from uma_trainer.types import ActionType, BotAction, GameState

if TYPE_CHECKING:
    from uma_trainer.decision.race_selector import RaceSelector
    from uma_trainer.decision.scorer import TrainingScorer

logger = logging.getLogger(__name__)

STRATEGIES_DIR = str(Path(__file__).resolve().parent.parent.parent / "data" / "strategies")
PAIR_COMMITMENTS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "pair_commitments.json"

# ---------------------------------------------------------------------------
# Config dataclasses (parsed from YAML)
# ---------------------------------------------------------------------------

ACTION_MAP = {
    "recreation": ActionType.GO_OUT,
    "race": ActionType.RACE,
    "train": ActionType.TRAIN,
    "rest": ActionType.REST,
    "infirmary": ActionType.INFIRMARY,
    "flex": ActionType.WAIT,  # WAIT = fall through to dynamic logic
}


@dataclass
class TurnAction:
    """One scheduled action for a specific turn."""
    action: str = "flex"          # "recreation" | "race" | "train" | "rest" | "flex"
    conditions: list[str] = field(default_factory=list)  # e.g. ["unless_double_friendship"]
    fallback: str = "flex"        # what to do if conditions block
    note: str = ""
    # Pair commitment fields. When `pair` is set, the engine resolves the
    # action via _decide_pair_turn instead of the normal scheduled-action path.
    # The lead role evaluates branches and writes a commitment; the follow
    # role reads it. This guarantees the pair always resolves to either
    # (race+Riko) or (train+train), never a mixed combination.
    pair: str = ""              # pair name, e.g. "sr_kyoto_kinen"
    role: str = ""              # "lead" | "follow"
    partner_turn: int = 0       # turn number of the pair partner
    race: str = ""              # explicit race name for the race branch (lead only)

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

    # Resource policy: for strategies where training turns are rare (e.g.
    # Sirius+Riko where most turns are race/recreation), we want to burn
    # energy drinks on low-energy training turns rather than waste them by
    # resting. Default False keeps legacy behavior for race-lighter strategies.
    drink_before_rest: bool = False


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
        bonds = tile.bond_levels or []
        friend_cards = sum(1 for b in bonds if b < 80)
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
        commitments_path: Path | str | None = None,
    ) -> None:
        self.playbook = playbook
        self.scorer = scorer
        self.race_selector = race_selector
        self._scenario = scenario
        self.rec_tracker = RecreationTracker.from_policy(playbook.recreation)
        self._commitments_path = (
            Path(commitments_path) if commitments_path else PAIR_COMMITMENTS_PATH
        )
        # Turns where the recreation_select handler already tried and failed
        # (no valid card on screen). Prevents infinite cancel-retry loops.
        self.skipped_recreation_turns: set[int] = set()

    # ------------------------------------------------------------------
    # Pair commitment persistence
    # ------------------------------------------------------------------

    def _load_commitments(self) -> dict:
        if not self._commitments_path.exists():
            return {}
        try:
            return json.loads(self._commitments_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read pair commitments file: %s", exc)
            return {}

    def _save_commitments(self, commitments: dict) -> None:
        self._commitments_path.parent.mkdir(parents=True, exist_ok=True)
        self._commitments_path.write_text(json.dumps(commitments, indent=2))

    def clear_pair_commitments(self) -> None:
        if self._commitments_path.exists():
            self._commitments_path.unlink()
            logger.info("Cleared pair commitments file at %s", self._commitments_path)

    def _record_commitment(self, pair: str, choice: str, lead_turn: int) -> None:
        commitments = self._load_commitments()
        commitments[pair] = {
            "choice": choice,
            "lead_turn": lead_turn,
            "decided_at": datetime.now().isoformat(),
        }
        self._save_commitments(commitments)
        logger.info(
            "Pair commitment recorded: %s = %s (lead turn %d)",
            pair, choice, lead_turn,
        )

    def _get_commitment(self, pair: str) -> dict | None:
        return self._load_commitments().get(pair)

    def _maybe_clear_stale_commitments(self, current_turn: int) -> None:
        """Auto-clear commitments that reference future turns — those are
        leftovers from a prior career run."""
        commitments = self._load_commitments()
        if not commitments:
            return
        earliest = min(c.get("lead_turn", 0) for c in commitments.values())
        if current_turn < earliest:
            logger.info(
                "Current turn %d precedes earliest commitment lead_turn %d — "
                "clearing stale pair commitments file",
                current_turn, earliest,
            )
            self.clear_pair_commitments()

    # ------------------------------------------------------------------
    # Pair branch evaluation
    # ------------------------------------------------------------------

    # Threshold for the train+train branch: best tile must show at least
    # this much total stat gain (sum across all stats on the tile) to beat
    # a race+Riko slot. Tuned for the Sirius+Riko strategy where race+Riko
    # is the default value floor.
    PAIR_TRAIN_TILE_THRESHOLD = 40

    def _evaluate_pair_branches(self, state: GameState, scheduled: TurnAction) -> str:
        """Decide whether a flex pair commits to the race+Riko branch or
        the train+train branch.

        Default bias is race + Riko (per the Sirius+Riko strategy notes).
        Forced overrides:
        - If Riko has no remaining recreation uses, must train.
        Tile-based override:
        - If the best training tile total stat gain ≥ PAIR_TRAIN_TILE_THRESHOLD,
          commit to train+train (the training side is clearly stronger).
          Tile data must already be populated on `state` — see
          needs_tile_preview() for the entry-point signal.
        """
        riko_remaining = self.rec_tracker.remaining_for("riko")
        if riko_remaining <= 0:
            logger.info(
                "Pair %s lead turn %d: Riko exhausted, committing to train+train branch",
                scheduled.pair, state.current_turn,
            )
            return "train"

        if state.training_tiles:
            best_total = max(
                (t.total_stat_gain for t in state.training_tiles),
                default=0,
            )
            if best_total >= self.PAIR_TRAIN_TILE_THRESHOLD:
                logger.info(
                    "Pair %s lead turn %d: best tile total=%d ≥ %d, committing to train+train",
                    scheduled.pair, state.current_turn,
                    best_total, self.PAIR_TRAIN_TILE_THRESHOLD,
                )
                return "train"
            logger.info(
                "Pair %s lead turn %d: best tile total=%d < %d, committing to race+Riko",
                scheduled.pair, state.current_turn,
                best_total, self.PAIR_TRAIN_TILE_THRESHOLD,
            )
        else:
            logger.info(
                "Pair %s lead turn %d: no tile data, defaulting to race+Riko (Riko has %d uses)",
                scheduled.pair, state.current_turn, riko_remaining,
            )
        return "race"

    def needs_tile_preview(self, state: GameState) -> bool:
        """Return True if the current turn is a pair lead with no commitment
        yet — the caller should populate state.training_tiles before calling
        decide_turn() so the branch evaluator can use real tile data."""
        scheduled = self._get_scheduled_action(state.current_turn)
        if not scheduled or not scheduled.pair or scheduled.role != "lead":
            return False
        existing = self._get_commitment(scheduled.pair)
        if existing and existing.get("lead_turn") == state.current_turn:
            return False  # Already committed, no need to peek
        return True

    def commit_pair_after_tiles(self, state: GameState) -> str | None:
        """Called from handle_training after tiles are scanned. If the
        current turn is a pair lead waiting for tile data, evaluate and
        commit. Returns the chosen branch ('race' or 'train'), or None if
        the current turn isn't a pair-lead-needing-commit.
        """
        scheduled = self._get_scheduled_action(state.current_turn)
        if not scheduled or not scheduled.pair or scheduled.role != "lead":
            return None
        existing = self._get_commitment(scheduled.pair)
        if existing and existing.get("lead_turn") == state.current_turn:
            return existing["choice"]
        choice = self._evaluate_pair_branches(state, scheduled)
        self._record_commitment(scheduled.pair, choice, lead_turn=state.current_turn)
        return choice

    def _decide_pair_turn(self, state: GameState, scheduled: TurnAction) -> BotAction:
        """Resolve a turn that's part of a flex pair (lead or follow)."""
        turn = state.current_turn
        pair = scheduled.pair
        role = scheduled.role

        if role == "lead":
            # Re-use any existing commitment for this pair (e.g. if the lead
            # turn re-runs after a crash partway through).
            existing = self._get_commitment(pair)
            if existing and existing.get("lead_turn") == turn:
                choice = existing["choice"]
                logger.info(
                    "Pair %s lead turn %d: reusing existing commitment '%s'",
                    pair, turn, choice,
                )
            elif state.training_tiles:
                # Tiles already populated (e.g. test path or post-peek
                # commit) — evaluate immediately.
                choice = self._evaluate_pair_branches(state, scheduled)
                self._record_commitment(pair, choice, lead_turn=turn)
            elif self.rec_tracker.remaining_for("riko") <= 0:
                # No tiles needed — Riko exhausted means train+train regardless.
                choice = "train"
                self._record_commitment(pair, choice, lead_turn=turn)
                logger.info(
                    "Pair %s lead turn %d: Riko exhausted, committing to train without tile peek",
                    pair, turn,
                )
            else:
                # No commitment and no tile data — return TRAIN so the caller
                # enters the training screen and scans tiles. handle_training
                # then calls commit_pair_after_tiles() to make the real
                # decision and either back out (race branch) or proceed
                # (train branch).
                logger.info(
                    "Pair %s lead turn %d: no tiles yet, peeking training screen",
                    pair, turn,
                )
                return BotAction(
                    action_type=ActionType.TRAIN,
                    reason=f"playbook pair {pair} lead: peeking tiles before commit",
                )

            if choice == "race":
                race_label = scheduled.race or scheduled.note or pair
                return BotAction(
                    action_type=ActionType.RACE,
                    reason=f"playbook pair {pair} lead: race {race_label}",
                )
            return BotAction(
                action_type=ActionType.TRAIN,
                reason=f"playbook pair {pair} lead: train (train+train branch)",
            )

        if role == "follow":
            if turn in self.skipped_recreation_turns:
                return BotAction(
                    action_type=ActionType.REST,
                    reason=f"playbook pair {pair} follow: recreation skipped (source not found on screen)",
                )
            commitment = self._get_commitment(pair)
            if not commitment:
                raise RuntimeError(
                    f"Pair commitment missing for '{pair}' on follow turn {turn}. "
                    f"Expected lead turn {scheduled.partner_turn} to have committed first. "
                    f"Check {self._commitments_path}. If the lead turn ran in a prior "
                    f"process, the file may have been deleted or never written."
                )
            if commitment.get("lead_turn") != scheduled.partner_turn:
                raise RuntimeError(
                    f"Pair commitment for '{pair}' has lead_turn="
                    f"{commitment.get('lead_turn')} but YAML says partner_turn="
                    f"{scheduled.partner_turn}. Stale or mismatched commitment — "
                    f"clear {self._commitments_path} and re-run the lead turn."
                )
            choice = commitment["choice"]
            if choice == "race":
                return BotAction(
                    action_type=ActionType.GO_OUT,
                    reason=f"playbook pair {pair} follow: Riko (lead turn {scheduled.partner_turn} raced)",
                )
            return BotAction(
                action_type=ActionType.TRAIN,
                reason=f"playbook pair {pair} follow: train (lead turn {scheduled.partner_turn} trained)",
            )

        raise RuntimeError(
            f"Unknown pair role '{role}' for pair '{pair}' on turn {turn}. "
            f"Expected 'lead' or 'follow'."
        )

    # ------------------------------------------------------------------
    # decide_turn
    # ------------------------------------------------------------------

    def decide_turn(self, state: GameState) -> BotAction:
        """Main entry point. Returns the action for this turn.

        Returns WAIT action_type to signal "fall through to dynamic logic".
        """
        turn = state.current_turn
        self._maybe_clear_stale_commitments(turn)
        scheduled = self._get_scheduled_action(turn)

        # Pair-tagged turns resolve through the commitment path before any
        # other condition logic, so a flex pair always commits collectively.
        if scheduled is not None and scheduled.pair:
            return self._decide_pair_turn(state, scheduled)

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

        # If this turn's recreation already failed on screen (no valid card),
        # don't re-enter the recreation flow — train instead.
        if scheduled.action == "recreation" and turn in self.skipped_recreation_turns:
            return BotAction(
                action_type=ActionType.TRAIN,
                reason=f"playbook: recreation unavailable this turn, training",
            )

        reason = f"playbook: {scheduled.action}"
        if scheduled.note:
            reason += f" ({scheduled.note})"

        return BotAction(action_type=scheduled.action_type, reason=reason)

    def wants_recreation(self, turn: int) -> bool:
        """Check if the playbook wants recreation confirmed this turn."""
        if not self.playbook.recreation.enabled:
            return False
        if turn in self.skipped_recreation_turns:
            return False
        scheduled = self._get_scheduled_action(turn)
        if not scheduled:
            return False
        if scheduled.action == "recreation":
            return True
        # Pair follow turn: if the lead committed to the race+Riko branch,
        # this turn is a Riko recreation.
        if scheduled.pair and scheduled.role == "follow":
            commitment = self._get_commitment(scheduled.pair)
            if commitment and commitment.get("choice") == "race":
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
        pair=raw.get("pair", ""),
        role=raw.get("role", ""),
        partner_turn=raw.get("partner_turn", 0),
        race=raw.get("race", ""),
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
        drink_before_rest=raw.get("drink_before_rest", False),
    )

    logger.info("Loaded playbook '%s' (%s)", config.name, config.id)
    return PlaybookEngine(config)
