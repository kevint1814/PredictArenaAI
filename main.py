"""
PredictArena AI — entry point.

Runs in polling mode on all environments (local dev and Render).
A tiny stdlib HTTP server runs in a background daemon thread so Render's
health probe gets a 200 on PORT — no aiohttp or webhook complexity needed.
PTB's run_polling() owns its own event loop, which is the only reliable way
to keep the APScheduler job queue firing on every tick.
"""

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram.ext import Application

from config import (
    TELEGRAM_BOT_TOKEN,
    ADMIN_TELEGRAM_ID, ADMIN_NAME, USER_2_TELEGRAM_ID, USER_2_NAME,
)
from database.db import init_db, register_user_if_new
from bot.handlers import register_handlers
from scheduler.jobs import setup_jobs

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("predicarena")


# ── Minimal health server ─────────────────────────────────────────────────────

class _HealthHandler(BaseHTTPRequestHandler):
    """Returns 200 OK for any GET — satisfies Render's health probe."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK\n")

    def log_message(self, fmt: str, *args) -> None:  # silence access logs
        pass


def _start_health_server(port: int, ready: threading.Event) -> None:
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    logger.info("Health server listening on port %d", port)
    ready.set()   # signal: socket is bound, Render can detect it now
    server.serve_forever()


# ── PTB setup ─────────────────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    # Pre-register both players. INSERT OR IGNORE means a restart never
    # overwrites a name that was already set via /start or /setname.
    register_user_if_new(ADMIN_TELEGRAM_ID, ADMIN_NAME, is_admin=True)
    if USER_2_TELEGRAM_ID:
        register_user_if_new(USER_2_TELEGRAM_ID, USER_2_NAME, is_admin=False)
    logger.info("Players pre-registered (names update when they /start)")

    setup_jobs(app)
    logger.info("PredictArena AI is live — all jobs scheduled")


def build_app() -> Application:
    return (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    init_db()

    # Bind to PORT and wait until the socket is actually listening before
    # proceeding — this ensures Render's port scanner detects it in time.
    port = int(os.getenv("PORT", "10000"))
    ready = threading.Event()
    threading.Thread(target=_start_health_server, args=(port, ready), daemon=True).start()
    ready.wait()   # block until HTTPServer.__init__ has bound the socket

    # Build bot and run in polling mode.
    # run_polling() manages its own asyncio event loop — the job queue
    # (APScheduler) integrates cleanly with this and fires on every interval.
    app = build_app()
    register_handlers(app)
    logger.info("Starting PredictArena AI in polling mode")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
