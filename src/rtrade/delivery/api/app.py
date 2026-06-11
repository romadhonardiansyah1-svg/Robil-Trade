"""FastAPI application factory (PLAN §8.10)."""

from __future__ import annotations

from fastapi import FastAPI

from rtrade.delivery.api.routes import router


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="Robil Trade API",
        description="AI-brained precision trading signal assistant (signal-only, manual execution)",
        version="0.1.0",
    )
    app.include_router(router)
    return app
