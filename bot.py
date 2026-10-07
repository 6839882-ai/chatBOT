"""
bot.py — точка входа: сборка зависимостей, регистрация роутеров, polling, graceful shutdown.

Запуск:  python bot.py
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from config import settings
from database.db import close_db, init_db
from handlers import chat, common, subscription, support
from services.cryptopay import CryptoPayClient
from services.openrouter import OpenRouterClient

logger = logging.getLogger("atlas")


async def on_error(event: ErrorEvent) -> bool:
    """Глобальный перехватчик: одна упавшая обработка не должна убивать бота."""
    logger.error("Необработанная ошибка в апдейте: %s", event.exception, exc_info=event.exception)
    return True


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    await init_db(settings.db_path)

    ai = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        model=settings.openrouter_model,
        system_prompt=settings.system_prompt,
        max_tokens=settings.ai_max_tokens,
        temperature=settings.ai_temperature,
        timeout=settings.ai_timeout,
        context_messages=settings.context_messages,
    )
    cryptopay = CryptoPayClient(settings.cryptopay_token, settings.cryptopay_base_url)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Сервисы попадают в хендлеры по имени аргумента (ai, cryptopay) — через workflow_data
    dp = Dispatcher(storage=MemoryStorage(), ai=ai, cryptopay=cryptopay)
    dp.errors.register(on_error)

    # Порядок важен: сначала меню/команды, затем сценарии, в самом конце — свободный чат
    dp.include_routers(common.router, support.router, subscription.router, chat.router)

    try:
        me = await bot.get_me()
        logger.info("Бот запущен: @%s", me.username)
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="help", description="Справка"),
                BotCommand(command="cancel", description="Отменить действие"),
            ]
        )
        await bot.delete_webhook(drop_pending_updates=True)
        # start_polling сам перехватывает SIGINT/SIGTERM и завершает цикл штатно
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Останавливаюсь: закрываю соединения…")
        await cryptopay.close()
        await ai.close()
        await close_db()
        await bot.session.close()
        logger.info("Остановлен корректно")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
