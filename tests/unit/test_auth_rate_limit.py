"""Tests for S10: auth failure rate limiting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def test_rate_limit_after_many_failures() -> None:
    """After 10 failed auth attempts → 429 rate limit."""
    from rtrade.delivery.api.routes import _auth_failures

    _auth_failures.clear()

    mock_secrets = MagicMock()
    mock_secrets.api_auth_token = "correct"
    mock_secrets.database_url = "sqlite+aiosqlite://"
    mock_secrets.redis_url = ""
    mock_cfg = MagicMock()
    mock_cfg.secrets = mock_secrets

    with patch("rtrade.delivery.api.routes.AppConfig") as m:
        m.load.return_value = mock_cfg
        from rtrade.delivery.api.app import create_app

        app = create_app()
        client = TestClient(app)

        # 10 failed attempts
        for _ in range(10):
            resp = client.get("/signals", headers={"Authorization": "Bearer wrong"})
            assert resp.status_code == 403

        # 11th should be rate-limited
        resp = client.get("/signals", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 429

        # Even correct token is blocked after rate limit
        resp = client.get("/signals", headers={"Authorization": "Bearer correct"})
        assert resp.status_code == 429

    _auth_failures.clear()
