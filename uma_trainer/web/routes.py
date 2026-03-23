"""API routes for the web dashboard."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from uma_trainer.config import AppConfig
    from uma_trainer.fsm.machine import BotStatus
    from uma_trainer.knowledge.advice_loader import AdviceLoader
    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.knowledge.overrides import OverridesLoader


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class AdviceSaveRequest(BaseModel):
    content: str

class EventOverrideItem(BaseModel):
    text_contains: str
    choice: int
    note: str = ""
    energy_min: int | None = None
    energy_max: int | None = None
    turn_min: int | None = None
    turn_max: int | None = None

class StrategyPayload(BaseModel):
    data: dict


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_router(
    config: "AppConfig",
    bot_status: "BotStatus | None",
    kb: "KnowledgeBase | None" = None,
    advice: "AdviceLoader | None" = None,
    overrides: "OverridesLoader | None" = None,
) -> APIRouter:
    router = APIRouter()

    # -----------------------------------------------------------------------
    # Bot status & control
    # -----------------------------------------------------------------------

    @router.get("/api/status")
    async def get_status() -> dict[str, Any]:
        """Full bot + game state snapshot (polled by the dashboard)."""
        if bot_status is None:
            return {"fsm_state": "offline", "message": "Bot not running"}
        return bot_status.snapshot()

    @router.post("/api/pause")
    async def pause_bot() -> dict[str, str]:
        if bot_status is None:
            raise HTTPException(503, "Bot not running")
        bot_status.paused = True
        return {"status": "paused"}

    @router.post("/api/resume")
    async def resume_bot() -> dict[str, str]:
        if bot_status is None:
            raise HTTPException(503, "Bot not running")
        bot_status.paused = False
        return {"status": "resumed"}

    @router.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # -----------------------------------------------------------------------
    # Config (read-only, sanitized)
    # -----------------------------------------------------------------------

    @router.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return {
            "capture": {
                "backend": config.capture.backend,
                "fps_decision": config.capture.fps_decision,
                "fps_passive": config.capture.fps_passive,
            },
            "yolo": {
                "model_path": config.yolo.model_path,
                "confidence_threshold": config.yolo.confidence_threshold,
            },
            "llm": {
                "local_model": config.llm.local_model,
                "claude_model": config.llm.claude_model,
                "claude_daily_limit": config.llm.claude_daily_limit,
            },
            "scorer": config.scorer.model_dump(),
        }

    # -----------------------------------------------------------------------
    # Advice files (read/write Markdown)
    # -----------------------------------------------------------------------

    @router.get("/api/advice")
    async def list_advice() -> list[dict]:
        """List all advice files."""
        if advice is None:
            return []
        return advice.list_files()

    @router.get("/api/advice/{name}")
    async def get_advice(name: str) -> dict[str, str]:
        """Get the content of an advice file."""
        if advice is None:
            raise HTTPException(503, "Advice loader not available")
        content = advice.get_file(name)
        return {"name": name, "content": content}

    @router.put("/api/advice/{name}")
    async def save_advice(name: str, body: AdviceSaveRequest) -> dict[str, str]:
        """Save an advice file."""
        if advice is None:
            raise HTTPException(503, "Advice loader not available")
        try:
            advice.save_file(name, body.content)
            return {"status": "saved", "name": name}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.delete("/api/advice/{name}")
    async def delete_advice(name: str) -> dict[str, Any]:
        """Delete an advice file."""
        if advice is None:
            raise HTTPException(503, "Advice loader not available")
        deleted = advice.delete_file(name)
        return {"deleted": deleted, "name": name}

    # -----------------------------------------------------------------------
    # Event overrides (Tier 0)
    # -----------------------------------------------------------------------

    @router.get("/api/overrides/events")
    async def get_event_overrides() -> list[dict]:
        if overrides is None:
            return []
        return overrides.get_event_overrides_raw()

    @router.put("/api/overrides/events")
    async def save_event_overrides(body: list[EventOverrideItem]) -> dict[str, Any]:
        if overrides is None:
            raise HTTPException(503, "Overrides loader not available")
        raw = [item.model_dump(exclude_none=True) for item in body]
        overrides.save_event_overrides(raw)
        return {"status": "saved", "count": len(raw)}

    # -----------------------------------------------------------------------
    # Strategy overrides
    # -----------------------------------------------------------------------

    @router.get("/api/overrides/strategy")
    async def get_strategy() -> dict:
        if overrides is None:
            return {}
        return overrides.get_strategy_raw()

    @router.put("/api/overrides/strategy")
    async def save_strategy(body: StrategyPayload) -> dict[str, str]:
        if overrides is None:
            raise HTTPException(503, "Overrides loader not available")
        overrides.save_strategy(body.data)
        return {"status": "saved"}

    # -----------------------------------------------------------------------
    # Knowledge base browsing
    # -----------------------------------------------------------------------

    @router.get("/api/support-cards")
    async def get_support_cards() -> list[dict]:
        if kb is None:
            return []
        rows = kb.query_all(
            "SELECT card_id, name, type, rarity, tier, training_bonuses FROM support_cards ORDER BY tier, name"
        )
        return [dict(r) for r in rows]

    @router.get("/api/skills")
    async def get_skills() -> list[dict]:
        if kb is None:
            return []
        rows = kb.query_all(
            "SELECT skill_id, name, category, priority, description FROM skills ORDER BY priority DESC, name"
        )
        return [dict(r) for r in rows]

    @router.get("/api/events")
    async def get_events(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Browse the event knowledge base."""
        if kb is None:
            return {"events": [], "total": 0}
        total_row = kb.query_one("SELECT COUNT(*) as n FROM events")
        total = total_row["n"] if total_row else 0
        rows = kb.query_all(
            "SELECT id, event_text, best_choice_index, source, confidence, created_at FROM events "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return {"events": [dict(r) for r in rows], "total": total}

    @router.get("/api/runs")
    async def get_runs(limit: int = 20) -> list[dict]:
        """Recent run history."""
        if kb is None:
            return []
        rows = kb.query_all(
            "SELECT run_id, trainee_id, scenario, goals_completed, total_goals, "
            "turns_taken, success, started_at, finished_at FROM run_log "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    return router
