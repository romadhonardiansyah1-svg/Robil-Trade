"""Telegram bot (aiogram 3.x) — signal delivery + commands (PLAN §8.10).

Commands:
    /status      — live health (db/redis/providers) via HealthChecker
    /signals     — 5 most recent signals from the database
    /calibration — WR, expectancy, abstain-rate over the last 30 days
    /mute 4h     — mute notifications
    /enable_strategy <name> — re-enable a disabled strategy (DB write)

Security: bot only responds to TELEGRAM_CHAT_ID (whitelist, PLAN §14.1).

Data access (audit E1 / P3-1): commands read live data from the database. The
bot is given a database/redis/litellm URL at construction; each command opens a
short-lived async session via ``create_session_factory(_get_engine(url))`` and
closes it before replying. Dependencies (session factory, health checker) are
injectable so the handlers can be unit-tested without a live DB or network.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import structlog

from rtrade.core.config import AppConfig
from rtrade.core.errors import ConfigError
from rtrade.llm.client import LLMClient
from rtrade.llm.key_manager import KeyManager
from rtrade.llm.model_router import resolve_role_model
from rtrade.llm.pool_builder import build_scan_pool
from rtrade.monitoring.healthcheck import HealthChecker, HealthStatus, SystemHealth
from rtrade.persistence.db import _get_engine, create_session_factory
from rtrade.persistence.models import Signal
from rtrade.persistence.repositories import InstrumentRepo, SignalRepo, StrategyStateRepo
from rtrade.signals.schemas import DISCLAIMER_TEXT
from rtrade.strategies import STRATEGY_REGISTRY

logger = structlog.get_logger(__name__)

_CALIBRATION_WINDOW_DAYS = 30

# READ-ONLY ops-chat constants -------------------------------------------------
_POOL_EMPTY_TEXT = "Pool kosong — jalankan: rtrade setup wizard"
_COST_UNAVAILABLE_TEXT = "Biaya LLM: tidak tersedia (Redis?)"
_LLM_UNCONFIGURED_TEXT = "🤖 LLM belum dikonfigurasi — jalankan: rtrade setup wizard."
_ASK_SYSTEM_PROMPT = (
    "Anda asisten operasional untuk bot sinyal trading (signal-only). "
    "Jawab dalam Bahasa Indonesia HANYA berdasarkan SNAPSHOT read-only di bawah. "
    "Anda TIDAK dapat mengubah apa pun, tidak memberi nasihat keuangan."
)


@dataclass(frozen=True, slots=True)
class CalibrationStats:
    """Aggregated calibration counters over the lookback window (E1)."""

    wins: int  # TP_HIT count
    losses: int  # SL_HIT count
    outcomes: list[float]  # resolved outcome_r values (for expectancy)
    published: int  # PUBLISHED count (abstain-rate denominator)
    abstained: int  # ABSTAINED count


# --- Pure formatting helpers (no I/O — unit-tested directly) -------------------


def format_status_text(health: SystemHealth) -> str:
    """Render a SystemHealth into the /status reply."""
    icon = {
        HealthStatus.HEALTHY: "🟢",
        HealthStatus.DEGRADED: "🟡",
        HealthStatus.UNHEALTHY: "🔴",
    }
    lines = [f"{icon[health.status]} Robil Trade — Status: {health.status.value.upper()}"]
    for check in health.checks:
        detail = f" ({check.message})" if check.message else ""
        lines.append(f"{icon[check.status]} {check.name}: {check.status.value.upper()}{detail}")
    return "\n".join(lines)


def _fmt_num(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:g}"


def format_signals_text(signals: list[Signal], symbols: dict[int, str]) -> str:
    """Render recent signals into the /signals reply. Honest when empty."""
    if not signals:
        return "📊 Sinyal terakhir:\n(Belum ada sinyal tersimpan di database.)"
    lines = [f"📊 {len(signals)} sinyal terakhir:"]
    for idx, s in enumerate(signals, start=1):
        symbol = symbols.get(s.instrument_id, str(s.instrument_id))
        conf = "-" if s.confidence is None else f"{float(s.confidence):.2f}"
        bar = s.bar_ts.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"{idx}. {symbol} {s.action} [{s.status}] {s.strategy}")
        lines.append(
            f"   entry={_fmt_num(s.entry_limit)} SL={_fmt_num(s.stop_loss)} "
            f"TP={_fmt_num(s.take_profit)} conf={conf}"
        )
        lines.append(f"   {bar}")
    lines.append("")
    lines.append(DISCLAIMER_TEXT)
    return "\n".join(lines)


def format_calibration_text(stats: CalibrationStats) -> str:
    """Render calibration counters into the /calibration reply. Honest when sparse."""
    resolved = stats.wins + stats.losses
    abstain_denom = stats.published + stats.abstained
    if resolved == 0 and abstain_denom == 0:
        return f"📈 Kalibrasi ({_CALIBRATION_WINDOW_DAYS} hari terakhir):\nBelum cukup data."
    lines = [f"📈 Kalibrasi ({_CALIBRATION_WINDOW_DAYS} hari terakhir):"]
    if resolved > 0:
        win_rate = stats.wins / resolved * 100.0
        lines.append(f"Win Rate: {win_rate:.1f}% ({stats.wins}W / {stats.losses}L)")
    else:
        lines.append("Win Rate: belum cukup data")
    if stats.outcomes:
        expectancy = sum(stats.outcomes) / len(stats.outcomes)
        lines.append(f"Expectancy: {expectancy:+.3f}R (n={len(stats.outcomes)})")
    else:
        lines.append("Expectancy: belum cukup data")
    if abstain_denom > 0:
        abstain_rate = stats.abstained / abstain_denom * 100.0
        lines.append(f"Abstain Rate: {abstain_rate:.1f}% ({stats.abstained}/{abstain_denom})")
    else:
        lines.append("Abstain Rate: belum cukup data")
    return "\n".join(lines)


def format_pool_text(rows: list[tuple[str, str, str]]) -> str:
    """Render the credential pool summary. ``rows`` = (cred_id, flavor, mode).

    cred_id/flavor/mode are internal labels only — never secrets (PLAN A6).
    Honest message when the pool is empty.
    """
    if not rows:
        return _POOL_EMPTY_TEXT
    lines = [f"🔑 Credential pool ({len(rows)}):"]
    for cred_id, flavor, mode in rows:
        lines.append(f"• {cred_id} [{flavor}] mode={mode}")
    return "\n".join(lines)


def build_ops_snapshot(*, status: str, signals: str, calibration: str, pool: str, cost: str) -> str:
    """Concatenate already-formatted read-only sections under clear headers.

    The result is fed to the LLM as UNTRUSTED data context (no instructions).
    """
    return (
        f"## STATUS\n{status}\n\n"
        f"## SINYAL TERAKHIR\n{signals}\n\n"
        f"## KALIBRASI\n{calibration}\n\n"
        f"## CREDENTIAL POOL\n{pool}\n\n"
        f"## BIAYA LLM\n{cost}"
    )


# --- Async data fetchers (own their session lifecycle) -------------------------


async def fetch_recent_signals(
    session_factory: async_sessionmaker[AsyncSession], *, limit: int = 5
) -> str:
    """Load the latest signals and resolve their symbols, then format."""
    async with session_factory() as session:
        signals = await SignalRepo(session).recent(limit=limit)
        instr_repo = InstrumentRepo(session)
        symbols: dict[int, str] = {}
        for s in signals:
            if s.instrument_id not in symbols:
                inst = await instr_repo.get_by_id(s.instrument_id)
                symbols[s.instrument_id] = inst.symbol if inst is not None else str(s.instrument_id)
    return format_signals_text(signals, symbols)


async def fetch_calibration(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    window_days: int = _CALIBRATION_WINDOW_DAYS,
) -> str:
    """Compute calibration counters over the window and format."""
    start = datetime.now(UTC) - timedelta(days=window_days)
    async with session_factory() as session:
        repo = SignalRepo(session)
        resolved = await repo.resolved_outcomes_since(start)
        counts = await repo.status_counts_since(start)
    wins = sum(1 for status, _ in resolved if status == "TP_HIT")
    losses = sum(1 for status, _ in resolved if status == "SL_HIT")
    outcomes = [r for _, r in resolved if r is not None]
    stats = CalibrationStats(
        wins=wins,
        losses=losses,
        outcomes=outcomes,
        published=counts.get("PUBLISHED", 0),
        abstained=counts.get("ABSTAINED", 0),
    )
    return format_calibration_text(stats)


async def enable_strategy(
    session_factory: async_sessionmaker[AsyncSession], strategy_name: str
) -> str:
    """Validate + re-enable a strategy (DB write), returning the reply text."""
    if strategy_name not in STRATEGY_REGISTRY:
        known = ", ".join(sorted(STRATEGY_REGISTRY))
        return f"❌ Strategi tidak dikenal: {strategy_name}\nPilihan: {known}"
    async with session_factory() as session:
        await StrategyStateRepo(session).set_state(
            strategy_name, enabled=True, reason="manual re-enable via telegram"
        )
        await session.commit()
    logger.info("strategy re-enabled via telegram", strategy=strategy_name)
    return f"✅ Strategi {strategy_name} diaktifkan kembali."


async def build_status_text(checker: HealthChecker) -> str:
    """Run health checks and format; never raise — degrade on error."""
    try:
        health = await checker.run_all()
    except Exception as exc:  # resilient: a status command must never crash
        logger.error("status health check failed", error=str(exc))
        return f"🟡 Robil Trade — Status: DEGRADED\nHealth check error: {exc}"
    return format_status_text(health)


async def fetch_pool_text(cfg_loader: Callable[[], AppConfig] = AppConfig.load) -> str:
    """Build the credential pool and render its (cred_id, flavor, mode) summary.

    READ-ONLY: only labels are surfaced — never secret values. On ConfigError /
    empty pool, returns an honest "Pool kosong" hint instead of raising.
    """
    try:
        cfg = cfg_loader()
        pool = build_scan_pool(cfg)
    except ConfigError:
        return _POOL_EMPTY_TEXT
    rows = [(e.cred_id, e.flavor, e.credential.mode) for e in pool.entries]
    return format_pool_text(rows)


async def fetch_cost_text(redis_url: str) -> str:
    """Today's (UTC) LLM spend via KeyManager. Resilient: degrade if no Redis."""
    if not redis_url:
        return _COST_UNAVAILABLE_TEXT
    redis_client = None
    try:
        redis_client = aioredis.from_url(redis_url)
        cost = await KeyManager(redis_client).get_daily_cost()
        return f"💰 Biaya LLM hari ini (UTC): ${cost:.4f}"
    except Exception as exc:
        logger.error("cost fetch failed", error=str(exc))
        return _COST_UNAVAILABLE_TEXT
    finally:
        if redis_client is not None:
            with contextlib.suppress(Exception):
                await redis_client.aclose()


async def answer_question(question: str, snapshot: str, llm_client: LLMClient, model: str) -> str:
    """Ask the LLM a question grounded ONLY in the read-only snapshot.

    The snapshot is treated as untrusted data; the system prompt states the bot
    cannot change anything and must not give financial advice.
    """
    user_prompt = snapshot + "\n\nPertanyaan: " + question
    result = await llm_client.complete(model, _ASK_SYSTEM_PROMPT, user_prompt)
    return result.content


class TelegramDelivery:
    """Telegram bot for signal delivery and commands (PLAN §8.10)."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        database_url: str = "",
        redis_url: str = "",
        litellm_url: str = "",
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        health_checker: HealthChecker | None = None,
        ask_responder: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required")

        self._bot = Bot(token=bot_token)
        self._chat_id = chat_id
        self._dp = Dispatcher()
        self._muted_until: datetime | None = None

        # DB-backed command data sources (audit E1). The session factory and
        # health checker are injectable for deterministic unit tests.
        self._database_url = database_url
        self._redis_url = redis_url
        self._litellm_url = litellm_url
        self._session_factory = session_factory
        self._health_checker = health_checker
        # READ-ONLY ops chat: injectable seam so tests bypass the real LLM/pool.
        self._ask_responder = ask_responder

        self._register_handlers()

    # --- dependency accessors --------------------------------------------------

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is not None:
            return self._session_factory
        if not self._database_url:
            raise RuntimeError("database_url not configured for TelegramDelivery")
        # _get_engine is loop-aware and process-scoped; only called inside handlers.
        return create_session_factory(_get_engine(self._database_url))

    def _get_health_checker(self) -> HealthChecker:
        if self._health_checker is not None:
            return self._health_checker
        return HealthChecker(
            db_url=self._database_url,
            redis_url=self._redis_url,
            litellm_url=self._litellm_url,
        )

    # --- handlers (thin wrappers around testable methods) ----------------------

    def _register_handlers(self) -> None:
        """Register command handlers (whitelist enforced)."""

        @self._dp.message(Command("status"))
        async def cmd_status(message: Message) -> None:
            await self._handle_status(message)

        @self._dp.message(Command("signals"))
        async def cmd_signals(message: Message) -> None:
            await self._handle_signals(message)

        @self._dp.message(Command("calibration"))
        async def cmd_calibration(message: Message) -> None:
            await self._handle_calibration(message)

        @self._dp.message(Command("mute"))
        async def cmd_mute(message: Message) -> None:
            await self._handle_mute(message)

        @self._dp.message(Command("enable_strategy"))
        async def cmd_enable_strategy(message: Message) -> None:
            await self._handle_enable_strategy(message)

        @self._dp.message(Command("pool"))
        async def cmd_pool(message: Message) -> None:
            await self._handle_pool(message)

        @self._dp.message(Command("cost"))
        async def cmd_cost(message: Message) -> None:
            await self._handle_cost(message)

        @self._dp.message(Command("ask"))
        async def cmd_ask(message: Message) -> None:
            await self._handle_ask(message)

    async def _handle_status(self, message: Message) -> None:
        if not self._is_allowed(message):
            return
        try:
            text = await build_status_text(self._get_health_checker())
        except Exception as exc:  # never raise out of a handler
            logger.error("status handler failed", error=str(exc))
            text = f"🟡 Robil Trade — Status: DEGRADED\n{exc}"
        await message.answer(text)

    async def _handle_signals(self, message: Message) -> None:
        if not self._is_allowed(message):
            return
        try:
            text = await fetch_recent_signals(self._get_session_factory(), limit=5)
        except Exception as exc:
            logger.error("signals handler failed", error=str(exc))
            text = f"⚠️ Gagal mengambil sinyal: {exc}"
        await message.answer(text)

    async def _handle_calibration(self, message: Message) -> None:
        if not self._is_allowed(message):
            return
        try:
            text = await fetch_calibration(self._get_session_factory())
        except Exception as exc:
            logger.error("calibration handler failed", error=str(exc))
            text = f"⚠️ Gagal menghitung kalibrasi: {exc}"
        await message.answer(text)

    async def _handle_mute(self, message: Message) -> None:
        if not self._is_allowed(message):
            return
        args = (message.text or "").split()
        hours = 4  # default
        if len(args) > 1:
            with contextlib.suppress(ValueError):
                hours = int(args[1].replace("h", ""))
        self._muted_until = datetime.now(UTC) + timedelta(hours=hours)
        await message.answer(f"🔇 Notifikasi di-mute selama {hours} jam.")

    async def _handle_enable_strategy(self, message: Message) -> None:
        if not self._is_allowed(message):
            return
        args = (message.text or "").split()
        if len(args) < 2:
            await message.answer("Usage: /enable_strategy <nama>")
            return
        strategy_name = args[1]
        try:
            text = await enable_strategy(self._get_session_factory(), strategy_name)
        except Exception as exc:
            logger.error("enable_strategy handler failed", error=str(exc))
            text = f"⚠️ Gagal mengaktifkan strategi: {exc}"
        await message.answer(text)

    # --- READ-ONLY ops chat handlers (whitelisted, resilient) ------------------

    async def _handle_pool(self, message: Message) -> None:
        if not self._is_allowed(message):
            return
        try:
            text = await fetch_pool_text()
        except Exception as exc:  # never raise out of a handler
            logger.error("pool handler failed", error=str(exc))
            text = f"⚠️ Gagal mengambil pool: {exc}"
        await message.answer(text)

    async def _handle_cost(self, message: Message) -> None:
        if not self._is_allowed(message):
            return
        try:
            text = await fetch_cost_text(self._redis_url)
        except Exception as exc:  # never raise out of a handler
            logger.error("cost handler failed", error=str(exc))
            text = _COST_UNAVAILABLE_TEXT
        await message.answer(text)

    async def _handle_ask(self, message: Message) -> None:
        if not self._is_allowed(message):
            return
        try:
            parts = (message.text or "").split(maxsplit=1)
            question = parts[1].strip() if len(parts) > 1 else ""
            if not question:
                await message.answer("Usage: /ask <pertanyaan>")
                return
            responder = self._ask_responder or self._build_ask_responder()
            text = await responder(question)
        except Exception as exc:  # never raise out of a handler
            logger.error("ask handler failed", error=str(exc))
            text = f"⚠️ Gagal menjawab: {exc}"
        await message.answer(text)

    def _build_ask_responder(self) -> Callable[[str], Awaitable[str]]:
        """Build the real LLM-backed responder lazily (pool + analyst model)."""

        async def responder(question: str) -> str:
            snapshot = await self._build_ops_snapshot()
            try:
                cfg = AppConfig.load()
                pool = build_scan_pool(cfg)
            except ConfigError:
                return _LLM_UNCONFIGURED_TEXT
            model = resolve_role_model(cfg, "analyst")
            client = LLMClient(credential_pool=pool)
            return await answer_question(question, snapshot, client, model)

        return responder

    async def _build_ops_snapshot(self) -> str:
        """Assemble the read-only snapshot; each section degrades independently."""

        async def _safe(make: Callable[[], Awaitable[str]], label: str) -> str:
            try:
                return await make()
            except Exception as exc:
                logger.error("ask snapshot section failed", section=label, error=str(exc))
                return f"({label} tidak tersedia: {exc})"

        status = await _safe(lambda: build_status_text(self._get_health_checker()), "status")
        signals = await _safe(
            lambda: fetch_recent_signals(self._get_session_factory(), limit=5), "signals"
        )
        calibration = await _safe(
            lambda: fetch_calibration(self._get_session_factory()), "calibration"
        )
        pool = await _safe(lambda: fetch_pool_text(), "pool")
        cost = await _safe(lambda: fetch_cost_text(self._redis_url), "cost")
        return build_ops_snapshot(
            status=status, signals=signals, calibration=calibration, pool=pool, cost=cost
        )

    def _is_allowed(self, message: Message) -> bool:
        """Whitelist check — only respond to configured chat ID (PLAN §14.1)."""
        if str(message.chat.id) != self._chat_id:
            logger.warning(
                "unauthorized message",
                chat_id=message.chat.id,
                expected=self._chat_id,
            )
            return False
        return True

    @property
    def is_muted(self) -> bool:
        if self._muted_until is None:
            return False
        return datetime.now(UTC) < self._muted_until

    async def send_signal(self, text: str) -> bool:
        """Push a signal message to the configured chat. Returns True if sent."""
        if self.is_muted:
            logger.info("signal muted, skipping Telegram delivery")
            return False
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=None,  # plain text, no markdown issues
            )
            logger.info("signal sent to Telegram")
            return True
        except Exception as exc:
            logger.error("failed to send Telegram message", error=str(exc))
            return False

    async def send_alert(self, text: str) -> None:
        """Send an alert/notification (not muted)."""
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=f"⚡ ALERT: {text}",
            )
        except Exception as exc:
            logger.error("failed to send Telegram alert", error=str(exc))

    async def start_polling(self) -> None:
        """Start the bot's polling loop (blocking)."""
        logger.info("starting Telegram bot polling")
        await self._dp.start_polling(self._bot)

    async def close(self) -> None:
        """Shut down the bot."""
        await self._bot.session.close()
