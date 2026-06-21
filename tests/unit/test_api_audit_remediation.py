"""Audit remediation tests for API routes (E1, C4, C5).

E1 — handlers must reuse the shared loop-aware engine/session factory and must
     NOT dispose the engine per request (connection-churn / pool-exhaustion fix).
C4 — public /health leaks no internals (only {"status": ...}); detailed health +
     calendar telemetry lives behind bearer auth (/health/detail).
C5 — client IP for the rate-limit key is taken from the trusted proxy hop (not a
     client-spoofable leftmost X-Forwarded-For), and the _auth_failures map is
     bounded (expired keys evicted + max-key cap).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from rtrade.monitoring.healthcheck import HealthStatus


@pytest.fixture()
def _mock_config():
    mock_secrets = MagicMock()
    mock_secrets.api_auth_token = "correct-token"
    mock_secrets.database_url = "postgresql+asyncpg://user:pw@localhost/db"
    mock_secrets.redis_url = "redis://localhost"

    mock_cfg = MagicMock()
    mock_cfg.secrets = mock_secrets

    with patch("rtrade.delivery.api.routes.AppConfig") as m:
        m.load.return_value = mock_cfg
        yield mock_cfg


@pytest.fixture()
def client(_mock_config: MagicMock) -> TestClient:
    from rtrade.delivery.api.routes import _auth_failures

    _auth_failures.clear()
    from rtrade.delivery.api.app import create_app

    return TestClient(create_app())


def _fake_session_cm() -> MagicMock:
    fake_session = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_cm.__aexit__ = AsyncMock(return_value=False)
    return fake_cm


# --------------------------------------------------------------------------- E1


class TestE1SharedEngine:
    def test_handlers_reuse_shared_engine_and_never_dispose(self, client: TestClient) -> None:
        """Two requests reuse a single shared engine (via _get_engine) and the
        engine is NEVER disposed inside a request handler."""
        from rtrade.delivery.api import routes

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        fake_factory = MagicMock(return_value=_fake_session_cm())

        repo = MagicMock()
        repo.recent = AsyncMock(return_value=[])

        with (
            patch.object(routes, "_get_engine", return_value=mock_engine) as get_engine,
            patch.object(routes, "create_session_factory", return_value=fake_factory),
            patch.object(routes, "SignalRepo", return_value=repo),
        ):
            r1 = client.get("/signals", headers={"Authorization": "Bearer correct-token"})
            r2 = client.get("/signals", headers={"Authorization": "Bearer correct-token"})

        assert r1.status_code == 200
        assert r2.status_code == 200
        # Shared loop-aware accessor used (not per-request create_engine).
        assert get_engine.call_count >= 1
        # Shared engine must never be disposed inside a request handler.
        mock_engine.dispose.assert_not_called()


# --------------------------------------------------------------------------- C4


class TestC4HealthSplit:
    def test_public_health_returns_only_status_no_internals(self, client: TestClient) -> None:
        with patch("rtrade.delivery.api.routes.HealthChecker") as mock_hc:
            mock_result = MagicMock()
            mock_result.status = HealthStatus.HEALTHY
            mock_result.to_dict.return_value = {
                "status": "healthy",
                "checks": [
                    {"name": "database", "details": {"version": "PostgreSQL 16.2 on x86_64"}},
                    {"name": "redis", "details": {"used_memory_human": "1.2M"}},
                ],
            }
            mock_hc.return_value.run_all = AsyncMock(return_value=mock_result)
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"status"}
        assert body["status"] in {"ok", "degraded"}
        text = resp.text
        for leak in ("version", "PostgreSQL", "used_memory", "calendar_sources", "last_error"):
            assert leak not in text, f"public /health leaked internal field: {leak}"

    def test_public_health_degraded_when_not_healthy(self, client: TestClient) -> None:
        with patch("rtrade.delivery.api.routes.HealthChecker") as mock_hc:
            mock_result = MagicMock()
            mock_result.status = HealthStatus.UNHEALTHY
            mock_result.to_dict.return_value = {"status": "unhealthy"}
            mock_hc.return_value.run_all = AsyncMock(return_value=mock_result)
            resp = client.get("/health")

        assert resp.status_code == 200
        assert resp.json() == {"status": "degraded"}

    def test_health_detail_requires_bearer(self, client: TestClient) -> None:
        resp = client.get("/health/detail")
        assert resp.status_code == 401

    def test_health_detail_returns_full_telemetry_with_bearer(self, client: TestClient) -> None:
        with patch("rtrade.delivery.api.routes.HealthChecker") as mock_hc:
            mock_result = MagicMock()
            mock_result.status = HealthStatus.HEALTHY
            mock_result.to_dict.return_value = {
                "status": "healthy",
                "checks": [{"name": "database", "details": {"version": "PostgreSQL 16.2"}}],
            }
            mock_hc.return_value.run_all = AsyncMock(return_value=mock_result)
            resp = client.get("/health/detail", headers={"Authorization": "Bearer correct-token"})

        assert resp.status_code == 200
        body = resp.json()
        assert "checks" in body
        assert "calendar_sources" in body


# --------------------------------------------------------------------------- C5


class TestC5ClientIpTrust:
    def test_client_ip_uses_trusted_rightmost_hop(self) -> None:
        from rtrade.delivery.api.routes import _client_ip

        req = MagicMock()
        req.headers = {"x-forwarded-for": "1.1.1.1, 2.2.2.2"}
        req.client = MagicMock()
        req.client.host = "10.0.0.9"
        # 2.2.2.2 is the hop appended by the trusted proxy; 1.1.1.1 is client-set.
        assert _client_ip(req) == "2.2.2.2"

    def test_spoofed_leftmost_xff_cannot_change_ratelimit_key(self) -> None:
        from rtrade.delivery.api.routes import _client_ip

        req_a = MagicMock()
        req_a.headers = {"x-forwarded-for": "9.9.9.9, 2.2.2.2"}
        req_a.client = MagicMock()
        req_a.client.host = "10.0.0.9"

        req_b = MagicMock()
        req_b.headers = {"x-forwarded-for": "8.8.8.8, 2.2.2.2"}
        req_b.client = MagicMock()
        req_b.client.host = "10.0.0.9"

        # Attacker rotating the leftmost (client-controlled) value must NOT yield
        # a different rate-limit key.
        assert _client_ip(req_a) == _client_ip(req_b)


class TestC5BoundedAuthMap:
    def test_auth_failures_map_is_bounded(self) -> None:
        from rtrade.delivery.api.routes import (
            _AUTH_FAIL_MAX_KEYS,
            _auth_failures,
            _require_bearer,
        )

        _auth_failures.clear()
        cfg = MagicMock()
        cfg.secrets = MagicMock()
        cfg.secrets.api_auth_token = "correct"

        for i in range(_AUTH_FAIL_MAX_KEYS + 100):
            try:
                _require_bearer(None, cfg, client_ip=f"ip-{i}")
            except HTTPException:
                pass

        assert len(_auth_failures) <= _AUTH_FAIL_MAX_KEYS
        _auth_failures.clear()

    def test_auth_failures_evicts_expired_keys(self) -> None:
        from rtrade.delivery.api.routes import _auth_failures, _require_bearer

        _auth_failures.clear()
        _auth_failures["stale-ip"] = [time.time() - 3600.0]  # outside the window
        cfg = MagicMock()
        cfg.secrets = MagicMock()
        cfg.secrets.api_auth_token = "correct"

        try:
            _require_bearer(None, cfg, client_ip="fresh-ip")
        except HTTPException:
            pass

        assert "stale-ip" not in _auth_failures
        _auth_failures.clear()
