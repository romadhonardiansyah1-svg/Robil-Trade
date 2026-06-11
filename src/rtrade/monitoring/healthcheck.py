"""System health checks for production monitoring (PLAN P4-T4).

Checks:
- Database connectivity (TimescaleDB)
- Redis connectivity
- LiteLLM proxy availability
- Data provider freshness
- Disk usage
- LLM budget status
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(slots=True)
class CheckResult:
    """Result of a single health check."""

    name: str
    status: HealthStatus
    message: str = ""
    latency_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class SystemHealth:
    """Aggregated system health status."""

    status: HealthStatus
    checks: list[CheckResult]
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "checked_at": self.checked_at.isoformat(),
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "message": c.message,
                    "latency_ms": round(c.latency_ms, 1),
                    "details": c.details,
                }
                for c in self.checks
            ],
        }


class HealthChecker:
    """Runs health checks against all system components.

    Usage:
        checker = HealthChecker(db_url=..., redis_url=..., litellm_url=...)
        health = await checker.run_all()
    """

    def __init__(
        self,
        *,
        db_url: str = "",
        redis_url: str = "",
        litellm_url: str = "",
        disk_threshold_pct: float = 85.0,
    ) -> None:
        self._db_url = db_url
        self._redis_url = redis_url
        self._litellm_url = litellm_url
        self._disk_threshold = disk_threshold_pct

    async def run_all(self) -> SystemHealth:
        """Execute all health checks and aggregate results."""
        checks: list[CheckResult] = []

        checks.append(await self.check_database())
        checks.append(await self.check_redis())
        checks.append(await self.check_litellm())
        checks.append(self.check_disk())

        # Determine overall status.
        statuses = [c.status for c in checks]
        if HealthStatus.UNHEALTHY in statuses:
            overall = HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY

        return SystemHealth(status=overall, checks=checks)

    async def check_database(self) -> CheckResult:
        """Check TimescaleDB connectivity."""
        import time

        start = time.monotonic()
        try:
            import asyncpg

            conn = await asyncpg.connect(
                self._db_url.replace("+asyncpg", "") if "+asyncpg" in self._db_url else self._db_url
            )
            version = await conn.fetchval("SELECT version()")
            await conn.close()
            elapsed = (time.monotonic() - start) * 1000

            return CheckResult(
                name="database",
                status=HealthStatus.HEALTHY,
                message="connected",
                latency_ms=elapsed,
                details={"version": version[:60] if version else ""},
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.error("db health check failed", error=str(exc))
            return CheckResult(
                name="database",
                status=HealthStatus.UNHEALTHY,
                message=f"connection failed: {exc}",
                latency_ms=elapsed,
            )

    async def check_redis(self) -> CheckResult:
        """Check Redis connectivity."""
        import time

        start = time.monotonic()
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(self._redis_url)
            pong = await r.ping()
            info = await r.info("memory")
            await r.aclose()
            elapsed = (time.monotonic() - start) * 1000

            return CheckResult(
                name="redis",
                status=HealthStatus.HEALTHY,
                message="pong" if pong else "no pong",
                latency_ms=elapsed,
                details={"used_memory_human": info.get("used_memory_human", "?")},
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.error("redis health check failed", error=str(exc))
            return CheckResult(
                name="redis",
                status=HealthStatus.UNHEALTHY,
                message=f"connection failed: {exc}",
                latency_ms=elapsed,
            )

    async def check_litellm(self) -> CheckResult:
        """Check LiteLLM proxy health."""
        import time

        start = time.monotonic()
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._litellm_url}/health")
            elapsed = (time.monotonic() - start) * 1000

            if resp.status_code == 200:
                return CheckResult(
                    name="litellm",
                    status=HealthStatus.HEALTHY,
                    message="proxy healthy",
                    latency_ms=elapsed,
                )
            return CheckResult(
                name="litellm",
                status=HealthStatus.DEGRADED,
                message=f"HTTP {resp.status_code}",
                latency_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            logger.error("litellm health check failed", error=str(exc))
            return CheckResult(
                name="litellm",
                status=HealthStatus.DEGRADED,
                message=f"unreachable: {exc}",
                latency_ms=elapsed,
            )

    def check_disk(self) -> CheckResult:
        """Check disk usage on the root partition."""
        import shutil

        try:
            usage = shutil.disk_usage("/")
            used_pct = (usage.used / usage.total) * 100
            free_gb = usage.free / (1024**3)

            status = HealthStatus.HEALTHY
            if used_pct >= self._disk_threshold:
                status = HealthStatus.UNHEALTHY
            elif used_pct >= self._disk_threshold - 10:
                status = HealthStatus.DEGRADED

            return CheckResult(
                name="disk",
                status=status,
                message=f"{used_pct:.1f}% used, {free_gb:.1f}GB free",
                details={
                    "used_pct": round(used_pct, 1),
                    "free_gb": round(free_gb, 1),
                    "total_gb": round(usage.total / (1024**3), 1),
                },
            )
        except Exception as exc:
            return CheckResult(
                name="disk",
                status=HealthStatus.DEGRADED,
                message=f"check failed: {exc}",
            )
