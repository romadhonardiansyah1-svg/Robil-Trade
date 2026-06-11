"""Async engine / session factory. One engine per process; sessions are cheap."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    if not database_url.startswith("postgresql+asyncpg://"):
        # Fail fast: the codebase assumes asyncpg (JSONB, ON CONFLICT, timescale).
        raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// driver")
    return create_async_engine(database_url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
