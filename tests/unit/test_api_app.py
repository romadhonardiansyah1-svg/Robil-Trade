"""D1: Test that the module-level app is importable and has correct title."""

from rtrade.delivery.api.app import app


def test_app_title() -> None:
    assert app.title == "Robil Trade API"


def test_app_has_routes() -> None:
    paths = [r.path for r in app.routes]
    assert "/health" in paths
