"""Decision logger: records every turn decision for later analysis and training.

Logs game state features, the action taken, tile scores, and the decision
tier to a SQLite table. Each turn within a run is one row. Run outcomes
(final stats, goals, rank) are stored in the existing run_log table and
linked via run_id.

Data format is designed for easy extraction into training samples for the
neural net scorer (see docs/neural_net_plan.md).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.scenario.base import ScenarioHandler
    from uma_trainer.types import BotAction, GameState, TrainingTile

logger = logging.getLogger(__name__)

# Schema applied on first use
_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    scenario TEXT NOT NULL DEFAULT '',

    -- Global state
    energy INTEGER NOT NULL DEFAULT 100,
    mood TEXT NOT NULL DEFAULT 'normal',
    phase TEXT NOT NULL DEFAULT '',
    stat_speed INTEGER NOT NULL DEFAULT 0,
    stat_stamina INTEGER NOT NULL DEFAULT 0,
    stat_power INTEGER NOT NULL DEFAULT 0,
    stat_guts INTEGER NOT NULL DEFAULT 0,
    stat_wit INTEGER NOT NULL DEFAULT 0,

    -- Tile features (JSON array of 5 tile objects)
    tiles TEXT NOT NULL DEFAULT '[]',

    -- Decision output
    action_type TEXT NOT NULL DEFAULT '',
    action_target TEXT NOT NULL DEFAULT '',
    action_reason TEXT NOT NULL DEFAULT '',
    tier_used INTEGER NOT NULL DEFAULT 1,

    -- Tile scores from the rule-based scorer (JSON: [{stat, score}])
    tile_scores TEXT NOT NULL DEFAULT '[]',

    -- Scenario-specific context (JSON dict, flexible)
    context TEXT NOT NULL DEFAULT '{}',

    timestamp REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_decision_log_run ON decision_log (run_id);
CREATE INDEX IF NOT EXISTS idx_decision_log_turn ON decision_log (run_id, turn);
"""


def _tile_to_dict(tile: "TrainingTile") -> dict:
    """Convert a TrainingTile to a compact dict for logging."""
    return {
        "stat": tile.stat_type.value,
        "cards": len(tile.support_cards),
        "rainbow": tile.is_rainbow,
        "gold": tile.is_gold,
        "hint": tile.has_hint,
        "director": tile.has_director,
        "failure_rate": tile.failure_rate,
        "stat_gains": tile.stat_gains,
        "total_gain": tile.total_stat_gain,
    }


class DecisionLogger:
    """Logs every turn decision to SQLite for analysis and model training."""

    def __init__(
        self,
        kb: "KnowledgeBase",
        scenario: "ScenarioHandler | None" = None,
    ) -> None:
        self.kb = kb
        self.scenario = scenario
        self._schema_applied = False

    def _ensure_schema(self) -> None:
        if self._schema_applied:
            return
        try:
            for statement in _SCHEMA.strip().split(";"):
                statement = statement.strip()
                if statement:
                    self.kb.execute(statement)
            self._schema_applied = True
        except Exception as e:
            logger.warning("Failed to apply decision_log schema: %s", e)

    def log_decision(
        self,
        run_id: str,
        state: "GameState",
        action: "BotAction",
        tile_scores: list[dict] | None = None,
        context: dict | None = None,
    ) -> None:
        """Record a single turn decision.

        Args:
            run_id: Unique identifier for the current career run.
            state: Full game state at decision time.
            action: The action that was chosen.
            tile_scores: Optional scored tiles from the rule-based scorer.
            context: Optional scenario-specific data (GP, shop coins, etc.)
        """
        self._ensure_schema()

        phase = ""
        if self.scenario:
            phase = self.scenario.phase_at(state.current_turn)

        tiles_json = json.dumps(
            [_tile_to_dict(t) for t in state.training_tiles],
            separators=(",", ":"),
        )
        scores_json = json.dumps(tile_scores or [], separators=(",", ":"))
        context_json = json.dumps(context or {}, separators=(",", ":"))

        try:
            self.kb.execute(
                """INSERT INTO decision_log
                   (run_id, turn, scenario, energy, mood, phase,
                    stat_speed, stat_stamina, stat_power, stat_guts, stat_wit,
                    tiles, action_type, action_target, action_reason, tier_used,
                    tile_scores, context, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    state.current_turn,
                    state.scenario,
                    state.energy,
                    state.mood.value,
                    phase,
                    state.stats.speed,
                    state.stats.stamina,
                    state.stats.power,
                    state.stats.guts,
                    state.stats.wit,
                    tiles_json,
                    action.action_type.value,
                    action.target,
                    action.reason,
                    action.tier_used,
                    scores_json,
                    context_json,
                    time.time(),
                ),
            )
        except Exception as e:
            # Never let logging failures crash the bot
            logger.debug("Failed to log decision: %s", e)

    def get_run_decisions(self, run_id: str) -> list[dict]:
        """Retrieve all logged decisions for a run."""
        self._ensure_schema()
        rows = self.kb.query_all(
            "SELECT * FROM decision_log WHERE run_id = ? ORDER BY turn",
            (run_id,),
        )
        return [dict(r) for r in rows]

    def get_run_count(self) -> int:
        """Total number of unique runs with logged decisions."""
        self._ensure_schema()
        row = self.kb.query_one(
            "SELECT COUNT(DISTINCT run_id) as cnt FROM decision_log",
        )
        return row["cnt"] if row else 0

    def get_decision_count(self) -> int:
        """Total number of logged decisions across all runs."""
        self._ensure_schema()
        row = self.kb.query_one("SELECT COUNT(*) as cnt FROM decision_log")
        return row["cnt"] if row else 0
