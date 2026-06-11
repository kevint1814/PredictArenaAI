"""
PredictArena AI — entry point.

Local dev:  python main.py          (polling mode, no WEBHOOK_URL set)
Production: set WEBHOOK_URL         (webhook mode, used on Render)
"""

import asyncio
import logging
import os

from aiohttp import web
from telegram import Update
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


async def _run_webhook(app: Application, port: int) -> None:
    """
    Webhook mode with a proper /health endpoint for UptimeRobot.

    PTB's built-in run_webhook() only serves /webhook and returns 404/403
    for everything else — UptimeRobot would think the service is down.
    Instead we run our own aiohttp server so we control all routes.
    """

    # ── aiohttp route handlers ────────────────────────────────────────────────

    async def handle_webhook(request: web.Request) -> web.Response:
        """Receive updates from Telegram and feed them into PTB."""
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret != WEBHOOK_SECRET:
            logger.warning("Webhook request with wrong secret token — ignored")
            return web.Response(status=403)
        try:
            data   = await request.json()
            update = Update.de_json(data, app.bot)
            await app.update_queue.put(update)
        except Exception as exc:
            logger.warning("Failed to parse webhook update: %s", exc)
        return web.Response(status=200)

    async def handle_health(_: web.Request) -> web.Response:
        """UptimeRobot pings this — must return 200."""
        return web.Response(text="OK")

    # ── Build the aiohttp app ─────────────────────────────────────────────────
    aio_app = web.Application()
    aio_app.router.add_get("/",        handle_health)
    aio_app.router.add_get("/health",  handle_health)
    aio_app.router.add_post("/webhook", handle_webhook)

    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)

    # ── Start PTB (job queue, dispatcher, post_init) then the web server ──────
    async with app:
        await app.start()   # starts job queue scheduler

        await app.bot.set_webhook(
            url=f"{WEBHOOK_URL.rstrip('/')}/webhook",
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
        logger.info("Webhook registered: %s/webhook", WEBHOOK_URL.rstrip("/"))

        await site.start()
        logger.info("HTTP server listening on port %d", port)

        try:
            # Run until the process is killed (Render/systemd sends SIGTERM)
            await asyncio.Event().wait()
        finally:
            logger.info("Shutting down…")
            await app.bot.delete_webhook()
            await app.stop()
            await runner.cleanup()


def main() -> None:
    init_db()

    app = build_app()
    register_handlers(app)

    if WEBHOOK_URL:
        # ── Webhook mode (Render) ─────────────────────────────────────────────
        port = int(os.getenv("PORT", "10000"))
        logger.info("Starting in webhook mode — port %d", port)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_webhook(app, port))
        except KeyboardInterrupt:
            pass
    else:
        # ── Polling mode (local development) ─────────────────────────────────
        logger.info("Starting in polling mode (local dev)")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
