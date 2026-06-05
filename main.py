"""
PredictArena AI — entry point.

Local dev:  python main.py          (polling mode, no WEBHOOK_URL set)
Production: set WEBHOOK_URL         (webhook mode, used on Render)
"""

import asyncio
import logging
import os

from telegram.ext import Application

from config import (
    TELEGRAM_BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET,
    ADMIN_TELEGRAM_ID, ADMIN_NAME, USER_2_TELEGRAM_ID, USER_2_NAME,
)
from database.db import init_db, register_user_if_new
from bot.handlers import register_handlers
from scheduler.jobs import setup_jobs

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    level=logging.INFO,
)
# Quieten noisy libs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("predicarena")


async def post_init(app: Application) -> None:
    # Register both players with placeholder names if they haven't joined yet.
    # Uses INSERT OR IGNORE so a bot restart never overwrites a name set via /start or /setname.
    register_user_if_new(ADMIN_TELEGRAM_ID, ADMIN_NAME, is_admin=True)
    if USER_2_TELEGRAM_ID:
        register_user_if_new(USER_2_TELEGRAM_ID, USER_2_NAME, is_admin=False)
    logger.info("Players pre-registered (names update when they /start)")

    setup_jobs(app)
    logger.info("PredictArena AI is live")


def build_app() -> Application:
    return (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )


def main() -> None:
    init_db()

    app = build_app()
    register_handlers(app)

    if WEBHOOK_URL:
        # ── Webhook mode (Render / VPS) ───────────────────────────────────────
        port = int(os.getenv("PORT", "10000"))
        logger.info("Starting in webhook mode — port %d", port)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            secret_token=WEBHOOK_SECRET,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/webhook",
        )
    else:
        # ── Polling mode (local development) ─────────────────────────────────
        logger.info("Starting in polling mode (local dev)")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    # Python 3.12+ no longer auto-creates an event loop — set one explicitly
    # before PTB's run_polling/run_webhook takes over.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
