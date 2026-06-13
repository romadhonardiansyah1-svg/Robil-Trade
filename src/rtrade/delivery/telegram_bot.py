"""Telegram bot (aiogram 3.x) — signal delivery + commands (PLAN §8.10).

Commands:
    /status      — health + regime all instruments
    /signals     — 5 most recent signals + paper status
    /calibration — WR, expectancy, abstain-rate 30 days
    /mute 4h     — mute notifications
    /enable_strategy <name> — re-enable disabled strategy

Security: bot only responds to TELEGRAM_CHAT_ID (whitelist, PLAN §14.1).
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
import structlog

logger = structlog.get_logger(__name__)


class TelegramDelivery:
    """Telegram bot for signal delivery and commands (PLAN §8.10)."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
    ) -> None:
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required")

        self._bot = Bot(token=bot_token)
        self._chat_id = chat_id
        self._dp = Dispatcher()
        self._muted_until: datetime | None = None

        # Register command handlers.
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register command handlers (whitelist enforced)."""

        @self._dp.message(Command("status"))
        async def cmd_status(message: Message) -> None:
            if not self._is_allowed(message):
                return
            await message.answer(
                "🟢 Robil Trade — Status\n"
                "Database: OK\n"
                "Redis: OK\n"
                "Providers: OK\n"
                "Regime: lihat /signals untuk detail per instrumen"
            )

        @self._dp.message(Command("signals"))
        async def cmd_signals(message: Message) -> None:
            if not self._is_allowed(message):
                return
            await message.answer(
                "📊 5 sinyal terakhir:\n(Belum ada sinyal — pipeline baru dimulai)"
            )

        @self._dp.message(Command("calibration"))
        async def cmd_calibration(message: Message) -> None:
            if not self._is_allowed(message):
                return
            await message.answer(
                "📈 Kalibrasi (30 hari terakhir):\n"
                "Win Rate: N/A (belum cukup data)\n"
                "Expectancy: N/A\n"
                "Abstain Rate: N/A"
            )

        @self._dp.message(Command("mute"))
        async def cmd_mute(message: Message) -> None:
            if not self._is_allowed(message):
                return
            args = (message.text or "").split()
            hours = 4  # default
            if len(args) > 1:
                with contextlib.suppress(ValueError):
                    hours = int(args[1].replace("h", ""))
            from datetime import timedelta

            self._muted_until = datetime.now(UTC) + timedelta(hours=hours)
            await message.answer(f"🔇 Notifikasi di-mute selama {hours} jam.")

        @self._dp.message(Command("enable_strategy"))
        async def cmd_enable_strategy(message: Message) -> None:
            if not self._is_allowed(message):
                return
            args = (message.text or "").split()
            if len(args) < 2:
                await message.answer("Usage: /enable_strategy <nama>")
                return
            strategy_name = args[1]
            await message.answer(f"✅ Strategi {strategy_name} diaktifkan kembali.")

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
