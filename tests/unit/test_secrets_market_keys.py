from __future__ import annotations

from rtrade.core.config import Secrets


def _secrets(**over: str) -> Secrets:
    # _env_file=None: ignore the on-disk .env so the test is deterministic.
    return Secrets(_env_file=None, **over)  # type: ignore[call-arg]


def test_oanda_keys_pair_token_and_account_in_order() -> None:
    s = _secrets(
        oanda_token_1="t1",
        oanda_account_1="a1",
        oanda_token_2="t2",
        oanda_account_2="a2",
    )
    assert s.market_keys_for("oanda") == [("t1", "a1"), ("t2", "a2")]


def test_oanda_skips_empty_slots() -> None:
    s = _secrets(oanda_token_1="t1", oanda_account_1="a1", oanda_token_3="t3", oanda_account_3="a3")
    assert s.market_keys_for("oanda") == [("t1", "a1"), ("t3", "a3")]


def test_twelvedata_includes_legacy_key_first() -> None:
    s = _secrets(twelvedata_api_key="legacy", twelvedata_api_key_2="k2")
    assert s.market_keys_for("twelvedata") == [("legacy", None), ("k2", None)]


def test_unknown_provider_returns_empty() -> None:
    assert _secrets().market_keys_for("nope") == []


def test_oanda_env_default_is_practice() -> None:
    assert _secrets().oanda_env == "practice"
