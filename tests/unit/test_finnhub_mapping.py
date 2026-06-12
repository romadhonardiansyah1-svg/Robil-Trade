"""T1: Finnhub country→currency mapping tests."""

from datetime import UTC, datetime, timedelta

from rtrade.data.finnhub_calendar import _to_currency
from rtrade.risk.news_filter import check_news_blackout


class TestToCurrency:
    def test_country_codes_map_to_currency(self) -> None:
        assert _to_currency("US") == "USD"
        assert _to_currency("GB") == "GBP"
        assert _to_currency("DE") == "EUR"
        assert _to_currency("JP") == "JPY"

    def test_unknown_code_passthrough(self) -> None:
        assert _to_currency("usd ") == "USD"
        assert _to_currency("XX") == "XX"


class TestBlackoutMappedCurrency:
    def test_blackout_matches_mapped_currency(self) -> None:
        now = datetime.now(UTC)
        event = {
            "event": "Nonfarm Payrolls",
            "currency": "USD",
            "impact": "high",
            "event_time": now + timedelta(minutes=10),
        }
        blocked, reason = check_news_blackout([event], ["USD"], now)
        assert blocked is True
        assert reason is not None
