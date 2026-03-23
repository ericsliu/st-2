"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from uma_trainer.web.routes import create_router

if TYPE_CHECKING:
    from uma_trainer.config import AppConfig
    from uma_trainer.fsm.machine import BotStatus
    from uma_trainer.knowledge.advice_loader import AdviceLoader
    from uma_trainer.knowledge.database import KnowledgeBase
    from uma_trainer.knowledge.overrides import OverridesLoader


def create_app(
    config: "AppConfig",
    bot_status: "BotStatus | None" = None,
    kb: "KnowledgeBase | None" = None,
    advice: "AdviceLoader | None" = None,
    overrides: "OverridesLoader | None" = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Uma Trainer Dashboard",
        description="Monitoring and control interface for the Uma Trainer bot",
        version="0.1.0",
    )

    router = create_router(config, bot_status, kb=kb, advice=advice, overrides=overrides)
    app.include_router(router)

    # Serve static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def root():
        """Redirect to dashboard."""
        from fastapi.responses import FileResponse
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"message": "Uma Trainer API running. See /docs for API documentation."}

    return app
