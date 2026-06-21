"""Unit tests for READ-ONLY ops-chat Telegram commands (/pool, /cost, /ask).

Deterministic, no network, no live DB / Redis / LLM:
- ``build_scan_pool`` is monkeypatched with fake pool entries.
- ``redis.asyncio`` + ``KeyManager`` are monkeypatched for /cost.
- The LLM-answering path is injected via the ``ask_responder`` constructor seam.
- aiogram Message is a tiny stub with an async ``.answer`` capturing replies.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from rtrade.delivery import telegram_bot as tb
from rtrade.delivery.telegram_bot import TelegramDelivery

BOT_TOKEN = "123456:test-token"
CHAT_ID = "999"


# --- test doubles --------------------------------------------------------------


class _FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _FakeMessage:
    def __init__(self, text: str, chat_id: int) -> None:
        self.text = text
        self.chat = _FakeChat(chat_id)
        self.replies: list[str] = []

    async def answer(self, text: str, **_: Any) -> None:
        self.replies.append(text)


def _make_bot(**kwargs: Any) -> TelegramDelivery:
    return TelegramDelivery(BOT_TOKEN, CHAT_ID, **kwargs)


# --- /pool : pure formatter ----------------------------------------------------


def test_format_pool_text_renders_rows() -> None:
    rows = [("gemini_key_1", "gemini", "api_key"), ("codex_oauth__a", "openai", "oauth2")]
    text = tb.format_pool_text(rows)
    assert "gemini_key_1" in text
    assert "gemini" in text
    assert "api_key" in text
    assert "codex_oauth__a" in text
    assert "openai" in text
    assert "oauth2" in text


def test_format_pool_text_empty_is_honest() -> None:
    text = tb.format_pool_text([])
    assert "Pool kosong" in text
    assert "rtrade setup wizard" in text


# --- /pool : async fetcher -----------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_pool_text_lists_cred_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_entries = [
        SimpleNamespace(
            cred_id="gemini_key_1", flavor="gemini", credential=SimpleNamespace(mode="api_key")
        ),
        SimpleNamespace(
            cred_id="codex_oauth__acc",
            flavor="openai",
            credential=SimpleNamespace(mode="oauth2"),
        ),
    ]
    monkeypatch.setattr(
        tb, "build_scan_pool", lambda cfg, **_: SimpleNamespace(entries=fake_entries)
    )
    text = await tb.fetch_pool_text(cfg_loader=lambda: object())
    assert "gemini_key_1" in text
    assert "codex_oauth__acc" in text


@pytest.mark.asyncio
async def test_fetch_pool_text_config_error_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_cfg: Any, **_: Any) -> Any:
        raise tb.ConfigError("no credentials")

    monkeypatch.setattr(tb, "build_scan_pool", _raise)
    text = await tb.fetch_pool_text(cfg_loader=lambda: object())
    assert "Pool kosong" in text
    assert "rtrade setup wizard" in text


# --- /cost : async fetcher -----------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_cost_text_formats_daily_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = {"value": False}

    class _FakeRedis:
        async def aclose(self) -> None:
            closed["value"] = True

    class _FakeKeyManager:
        def __init__(self, _client: Any) -> None:
            pass

        async def get_daily_cost(self) -> float:
            return 1.2345

    monkeypatch.setattr(tb.aioredis, "from_url", lambda _url: _FakeRedis())
    monkeypatch.setattr(tb, "KeyManager", _FakeKeyManager)

    text = await tb.fetch_cost_text("redis://localhost:6379/0")
    assert "$1.2345" in text
    assert "UTC" in text
    assert closed["value"] is True  # client closed after use


@pytest.mark.asyncio
async def test_fetch_cost_text_unavailable_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_url: str) -> Any:
        raise RuntimeError("no redis")

    monkeypatch.setattr(tb.aioredis, "from_url", _boom)
    text = await tb.fetch_cost_text("redis://localhost:6379/0")
    assert "tidak tersedia" in text


# --- /ask : pure snapshot helper ----------------------------------------------


def test_build_ops_snapshot_has_all_sections() -> None:
    snap = tb.build_ops_snapshot(
        status="STATUS_BODY",
        signals="SIGNALS_BODY",
        calibration="CALIB_BODY",
        pool="POOL_BODY",
        cost="COST_BODY",
    )
    for header in ("STATUS", "SINYAL", "KALIBRASI", "POOL", "BIAYA"):
        assert header in snap
    for body in ("STATUS_BODY", "SIGNALS_BODY", "CALIB_BODY", "POOL_BODY", "COST_BODY"):
        assert body in snap


# --- /ask : LLM answer helper --------------------------------------------------


@pytest.mark.asyncio
async def test_answer_question_calls_llm_and_returns_content() -> None:
    class _FakeLLM:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        async def complete(self, model: str, system_prompt: str, user_prompt: str, **_: Any) -> Any:
            self.calls.append((model, system_prompt, user_prompt))
            return SimpleNamespace(content="jawaban kanonik")

    llm = _FakeLLM()
    out = await tb.answer_question("kenapa abstain?", "SNAPSHOT_DATA", llm, "gemini/flash")
    assert out == "jawaban kanonik"
    model, system_prompt, user_prompt = llm.calls[0]
    assert model == "gemini/flash"
    # system prompt forbids changes (read-only guardrail)
    assert "TIDAK dapat mengubah" in system_prompt
    # question + snapshot are present in the user prompt
    assert "kenapa abstain?" in user_prompt
    assert "SNAPSHOT_DATA" in user_prompt


# --- /ask : handler ------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_handler_uses_injected_responder() -> None:
    async def responder(question: str) -> str:
        return f"jawaban: {question}"

    bot = _make_bot(ask_responder=responder)
    msg = _FakeMessage("/ask apa status terkini?", int(CHAT_ID))
    await bot._handle_ask(msg)  # type: ignore[arg-type]
    assert msg.replies == ["jawaban: apa status terkini?"]


@pytest.mark.asyncio
async def test_ask_handler_usage_hint_when_empty() -> None:
    async def responder(question: str) -> str:  # pragma: no cover - must not be called
        return "should not be called"

    bot = _make_bot(ask_responder=responder)
    msg = _FakeMessage("/ask", int(CHAT_ID))
    await bot._handle_ask(msg)  # type: ignore[arg-type]
    assert len(msg.replies) == 1
    assert "Usage" in msg.replies[0]


@pytest.mark.asyncio
async def test_ask_handler_blocks_unauthorized_chat() -> None:
    called = {"value": False}

    async def responder(question: str) -> str:
        called["value"] = True
        return "leaked"

    bot = _make_bot(ask_responder=responder)
    msg = _FakeMessage("/ask hello", 12345)  # not the whitelisted CHAT_ID
    await bot._handle_ask(msg)  # type: ignore[arg-type]
    assert msg.replies == []
    assert called["value"] is False


# --- /pool & /cost handlers : whitelist guard ----------------------------------


@pytest.mark.asyncio
async def test_pool_cost_handlers_block_unauthorized() -> None:
    bot = _make_bot()
    msg = _FakeMessage("/pool", 12345)
    await bot._handle_pool(msg)  # type: ignore[arg-type]
    await bot._handle_cost(msg)  # type: ignore[arg-type]
    assert msg.replies == []
