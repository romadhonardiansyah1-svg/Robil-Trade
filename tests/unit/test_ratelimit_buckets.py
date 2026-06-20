from __future__ import annotations

from rtrade.data.ratelimit import OANDA_BUCKET, market_bucket


def test_oanda_bucket_rpm() -> None:
    assert OANDA_BUCKET.name == "oanda"
    assert OANDA_BUCKET.max_tokens == 6000


def test_market_bucket_names_are_per_account() -> None:
    assert market_bucket("oanda", 1).name == "oanda_acc1"
    assert market_bucket("oanda", 2).name == "oanda_acc2"
    assert market_bucket("twelvedata", 1).name == "twelvedata_k1"


def test_market_bucket_rpm_matches_vendor() -> None:
    assert market_bucket("oanda", 1).max_tokens == 6000
    assert market_bucket("twelvedata", 1).max_tokens == 7
