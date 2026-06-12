"""FastAPI application factory (PLAN §8.10).

S1: disable docs/openapi in prod, add security headers middleware.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from rtrade.delivery.api.routes import router


class _SecurityHeaders(BaseHTTPMiddleware):
    """Defense-in-depth security headers (S1) — alongside Caddy."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    from rtrade.core.logging_setup import configure_logging

    configure_logging()
    is_prod = os.environ.get("ENV", "dev") == "prod"
    app = FastAPI(
        title="Robil Trade API",
        description="AI-brained precision trading signal assistant (signal-only, manual execution)",
        version="0.1.0",
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.add_middleware(_SecurityHeaders)
    app.include_router(router)
    return app


# Module-level app for uvicorn (compose: rtrade.delivery.api.app:app).
app = create_app()
