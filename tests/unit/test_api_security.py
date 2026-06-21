"""Security tests for API routes (S1+S10)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest


@pytest.fixture()
def _mock_config(monkeypatch: pytest.MonkeyPatch):
    """Patch AppConfig.load to return a minimal fake config."""
    mock_secrets = MagicMock()
    mock_secrets.api_auth_token = "correct-token"
    mock_secrets.database_url = "sqlite+aiosqlite://"
    mock_secrets.redis_url = "redis://localhost"

    mock_cfg = MagicMock()
    mock_cfg.secrets = mock_secrets

    with patch("rtrade.delivery.api.routes.AppConfig") as m:
        m.load.return_value = mock_cfg
        yield mock_cfg


@pytest.fixture()
def client(_mock_config: MagicMock) -> TestClient:
    # Reset rate-limit state
    from rtrade.delivery.api.routes import _auth_failures

    _auth_failures.clear()

    from rtrade.delivery.api.app import create_app

    app = create_app()
    return TestClient(app)


class TestAuthAllRoutes:
    """S1: Every non-health endpoint requires Bearer auth."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/signals"),
            ("GET", "/signals/abc123"),
            ("GET", "/calibration"),
            ("POST", "/scan"),
            ("GET", "/metrics"),
            ("GET", "/analytics/exits"),
            ("GET", "/analytics/excursion"),
            ("GET", "/analytics/failures"),
        ],
    )
    def test_401_without_header(self, client: TestClient, method: str, path: str) -> None:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} should be 401 without auth"

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/signals"),
            ("POST", "/scan"),
        ],
    )
    def test_403_wrong_token(self, client: TestClient, method: str, path: str) -> None:
        resp = client.request(method, path, headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 403

    def test_health_no_auth_needed(self, client: TestClient) -> None:
        with patch("rtrade.delivery.api.routes.HealthChecker") as mock_hc:
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"status": "ok"}
            mock_hc.return_value.run_all = AsyncMock(return_value=mock_result)
            resp = client.get("/health")
        assert resp.status_code == 200


class TestTimingAttack:
    """S1: token comparison uses hmac.compare_digest (constant-time)."""

    def test_compare_digest_used(self) -> None:
        # Verify the source code uses hmac.compare_digest
        import inspect

        import rtrade.delivery.api.routes as mod

        source = inspect.getsource(mod._require_bearer)
        assert "compare_digest" in source


class TestProdDocs:
    """S1: docs/openapi disabled in prod."""

    def test_prod_docs_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "prod")
        from rtrade.delivery.api.app import create_app

        app = create_app()
        assert app.docs_url is None
        assert app.openapi_url is None

    def test_dev_docs_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "dev")
        from rtrade.delivery.api.app import create_app

        app = create_app()
        assert app.docs_url == "/docs"
        assert app.openapi_url == "/openapi.json"


class TestSecurityHeaders:
    """S1: response includes security headers."""

    def test_nosniff_header(self, client: TestClient) -> None:
        with patch("rtrade.delivery.api.routes.HealthChecker") as mock_hc:
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"status": "ok"}
            mock_hc.return_value.run_all = AsyncMock(return_value=mock_result)
            resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"
        assert resp.headers.get("Cache-Control") == "no-store"


class TestTokenNotConfigured:
    """S1: 503 when API_AUTH_TOKEN is empty."""

    def test_503_empty_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from rtrade.delivery.api.routes import _auth_failures

        _auth_failures.clear()

        mock_secrets = MagicMock()
        mock_secrets.api_auth_token = ""  # Not configured
        mock_cfg = MagicMock()
        mock_cfg.secrets = mock_secrets

        with patch("rtrade.delivery.api.routes.AppConfig") as m:
            m.load.return_value = mock_cfg
            from rtrade.delivery.api.app import create_app

            app = create_app()
            tc = TestClient(app)
            resp = tc.get("/signals", headers={"Authorization": "Bearer some-token"})
            assert resp.status_code == 503

    @pytest.mark.parametrize("missing_token", ["", None])
    def test_require_bearer_503_when_token_unset(self, missing_token: str | None) -> None:
        """C1: fail closed — `_require_bearer` raises HTTPException(503) when the
        configured `api_auth_token` is empty/None (e.g. API_AUTH_TOKEN env unset).
        Asserts the unit directly, independent of the HTTP layer."""
        from fastapi import HTTPException

        from rtrade.delivery.api.routes import _auth_failures, _require_bearer

        _auth_failures.clear()

        mock_secrets = MagicMock()
        mock_secrets.api_auth_token = missing_token
        mock_cfg = MagicMock()
        mock_cfg.secrets = mock_secrets

        with pytest.raises(HTTPException) as exc_info:
            _require_bearer(
                "Bearer some-token",
                mock_cfg,
                client_ip="test-c1-unset",
            )
        assert exc_info.value.status_code == 503
