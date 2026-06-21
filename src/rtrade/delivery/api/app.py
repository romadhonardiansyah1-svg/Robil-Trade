"""FastAPI application factory (PLAN §8.10).

S1: disable docs/openapi in prod, add security headers middleware.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from rtrade.delivery.api.routes import router
from rtrade.persistence.db import shutdown_process_resources


class _SecurityHeaders(BaseHTTPMiddleware):
    """Defense-in-depth security headers (S1) — alongside Caddy."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """E1: dispose the shared loop-aware engine(s) once, on app shutdown.

    Handlers reuse a single process-scoped engine (``db._get_engine``) for the
    process lifetime; disposing it here is the *only* place a request-path
    engine is torn down (never inside a handler).
    """
    yield
    await shutdown_process_resources()


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
        lifespan=_lifespan,
    )
    app.add_middleware(_SecurityHeaders)
    app.include_router(router)
    return app


# Module-level app for uvicorn (compose: rtrade.delivery.api.app:app).
app = create_app()
