"""F7: Telegram bot CLI entrypoint — runs the bot in polling mode.

Usage:
    uv run python -m rtrade.cli.bot
"""

from __future__ import annotations

import asyncio

import structlog

from rtrade.core.config import AppConfig
from rtrade.delivery.telegram_bot import TelegramDelivery

logger = structlog.get_logger(__name__)


async def main() -> None:
    """Start the Telegram bot in polling mode."""
    cfg = AppConfig.load()
    if not cfg.secrets.telegram_bot_token or not cfg.secrets.telegram_chat_id:
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        return
    bot = TelegramDelivery(
        cfg.secrets.telegram_bot_token,
        cfg.secrets.telegram_chat_id,
    )
    logger.info("starting Telegram bot polling")
    try:
        await bot.start_polling()
    except KeyboardInterrupt:
        logger.info("bot stopped by user")
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
