from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from rtrade.core.config import Secrets
from rtrade.core.errors import ConfigError
from rtrade.data.composite_market import CompositeMarketDataProvider
from rtrade.pipeline.scan import _make_market_provider


@pytest.fixture(autouse=True)
def _clear_market_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # litellm (imported via rtrade.pipeline.scan) calls load_dotenv() at import
    # time, leaking the on-disk .env into os.environ. Clear market slots so these
    # unit tests are deterministic regardless of the developer's local .env.
    for name in (
        "TWELVEDATA_API_KEY",
        "TWELVEDATA_API_KEY_2",
        "TWELVEDATA_API_KEY_3",
        "OANDA_TOKEN_1",
        "OANDA_TOKEN_2",
        "OANDA_TOKEN_3",
        "OANDA_ACCOUNT_1",
        "OANDA_ACCOUNT_2",
        "OANDA_ACCOUNT_3",
        "OANDA_ENV",
    ):
        monkeypatch.delenv(name, raising=False)


def _cfg(**over: Any) -> SimpleNamespace:
    return SimpleNamespace(secrets=Secrets(_env_file=None, **over))  # type: ignore[call-arg]


def _inst(provider: str) -> SimpleNamespace:
    return SimpleNamespace(provider=provider, provider_symbol="XAU_USD", symbol="XAUUSD")


def _close(prov: Any) -> None:
    asyncio.run(prov.close())


def test_oanda_builds_composite_two_legs() -> None:
    cfg = _cfg(
        oanda_token_1="t1",
        oanda_account_1="a1",
        oanda_token_2="t2",
        oanda_account_2="a2",
    )
    prov = _make_market_provider(_inst("oanda"), cfg, None)
    try:
        assert isinstance(prov, CompositeMarketDataProvider)
        assert list(prov.health_snapshot().keys()) == ["oanda_1", "oanda_2"]
    finally:
        _close(prov)


def test_oanda_appends_twelvedata_fallback_legs() -> None:
    cfg = _cfg(oanda_token_1="t1", oanda_account_1="a1", twelvedata_api_key="td1")
    prov = _make_market_provider(_inst("oanda"), cfg, None)
    try:
        assert list(prov.health_snapshot().keys()) == ["oanda_1", "twelvedata_1"]
    finally:
        _close(prov)


def test_oanda_no_credentials_raises() -> None:
    with pytest.raises(ConfigError):
        _make_market_provider(_inst("oanda"), _cfg(), None)


def test_twelvedata_only_builds_composite() -> None:
    cfg = _cfg(twelvedata_api_key="td1", twelvedata_api_key_2="td2")
    prov = _make_market_provider(_inst("twelvedata"), cfg, None)
    try:
        assert list(prov.health_snapshot().keys()) == ["twelvedata_1", "twelvedata_2"]
    finally:
        _close(prov)


def test_unsupported_provider_raises() -> None:
    with pytest.raises(ConfigError):
        _make_market_provider(_inst("bogus"), _cfg(), None)
