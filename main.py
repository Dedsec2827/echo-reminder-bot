"""
main.py
-------
Єдина точка входу для локального запуску: піднімає одночасно
- Aiogram-бота (long polling) разом із фоновим планувальником нагадувань,
- FastAPI-сервер (uvicorn), який роздає Mini App і API.
"""

import asyncio
import logging
import os
import uvicorn
from dotenv import load_dotenv

# ВАЖЛИВО: Завантажуємо змінні з .env ДО імпорту бази даних!
load_dotenv()

import database as db
from api import app as fastapi_app
from bot import create_bot_and_dispatcher, setup_scheduler

API_HOST = os.getenv("API_HOST", "0.0.0.0")
# Гнучкий порт: спочатку шукає PORT (від Render), потім API_PORT (локальний)
API_PORT = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))


async def run_bot(bot, dp) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


async def run_api() -> None:
    config = uvicorn.Config(fastapi_app, host=API_HOST, port=API_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    await db.init_db()

    bot, dp = create_bot_and_dispatcher()
    setup_scheduler(bot)

    # Даємо FastAPI-застосунку доступ до того самого Bot-інстанса
    fastapi_app.state.bot = bot

    # return_exceptions=True: a crash in the bot polling loop or the API server is logged
    # instead of silently killing the other half of the process.
    results = await asyncio.gather(run_bot(bot, dp), run_api(), return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logging.getLogger("echo-main").exception("A service crashed", exc_info=result)


if __name__ == "__main__":
    asyncio.run(main())
