"""Tests for secret redaction in logging (S2)."""

from __future__ import annotations

from rtrade.core.logging_redact import redact_processor


class TestRedactProcessor:
    def test_sensitive_key_redacted(self) -> None:
        result = redact_processor(None, "", {"api_key": "AIzaSECRET123", "msg": "hello"})
        assert result["api_key"] == "***REDACTED***"
        assert result["msg"] == "hello"

    def test_bearer_in_value_redacted(self) -> None:
        result = redact_processor(None, "", {"msg": "got Bearer abcdef123.xyz from server"})
        assert "abcdef123" not in result["msg"]
        assert "Bearer ***" in result["msg"]

    def test_apikey_in_url_redacted(self) -> None:
        result = redact_processor(None, "", {"msg": "url?apikey=ABC123&x=1"})
        assert "ABC123" not in result["msg"]
        assert "apikey=***" in result["msg"]
        assert "x=1" in result["msg"]

    def test_sk_key_redacted(self) -> None:
        result = redact_processor(None, "", {"msg": "key is sk-ant-api01-xxxxxxxxxxxx here"})
        assert "sk-ant-api01" not in result["msg"]
        assert "***" in result["msg"]

    def test_google_api_key_redacted(self) -> None:
        result = redact_processor(None, "", {"msg": "using AIzaSyD1234567890abcdefg"})
        assert "AIzaSyD1234567890" not in result["msg"]

    def test_normal_text_unchanged(self) -> None:
        result = redact_processor(None, "", {"msg": "Nonfarm Payrolls data released", "count": 42})
        assert result["msg"] == "Nonfarm Payrolls data released"
        assert result["count"] == 42

    def test_multiple_sensitive_keys(self) -> None:
        result = redact_processor(
            None,
            "",
            {
                "token": "secret-val",
                "refresh_token": "refresh-val",
                "password": "pass123",
                "event": "normal",
            },
        )
        assert result["token"] == "***REDACTED***"
        assert result["refresh_token"] == "***REDACTED***"
        assert result["password"] == "***REDACTED***"
        assert result["event"] == "normal"


class TestRecursiveRedaction:
    def test_nested_dict_secret_redacted(self) -> None:
        result = redact_processor(
            None,
            "",
            {
                "outer": {
                    "api_key": "SECRET",
                    "url": "https://x?token=ABC&api_key=DEF",
                }
            },
        )
        blob = repr(result)
        assert "SECRET" not in blob
        assert "ABC" not in blob
        assert "DEF" not in blob

    def test_nested_list_secret_redacted(self) -> None:
        result = redact_processor(
            None,
            "",
            {"items": [{"access_token": "TOKVAL"}, "https://x?refresh_token=RTOK"]},
        )
        blob = repr(result)
        assert "TOKVAL" not in blob
        assert "RTOK" not in blob

    def test_non_sensitive_nested_values_preserved(self) -> None:
        result = redact_processor(
            None,
            "",
            {"outer": {"event": "Nonfarm Payrolls", "count": 42, "nested": {"k": "v"}}},
        )
        assert result["outer"]["event"] == "Nonfarm Payrolls"
        assert result["outer"]["count"] == 42
        assert result["outer"]["nested"]["k"] == "v"

    def test_url_token_variants_redacted(self) -> None:
        result = redact_processor(
            None,
            "",
            {
                "msg": (
                    "https://x?token=AAA&api_key=BBB&apikey=CCC"
                    "&access_token=DDD&refresh_token=EEE&keep=ok"
                )
            },
        )
        msg = result["msg"]
        for leaked in ("AAA", "BBB", "CCC", "DDD", "EEE"):
            assert leaked not in msg
        assert "keep=ok" in msg
