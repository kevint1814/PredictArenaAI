"""
Shared fixtures and test DB setup.
Uses an in-memory SQLite database so tests never touch predicarena.db.
"""
import os
import sys

# ── Ensure project root is on sys.path ───────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Patch env vars before any project import touches them ────────────────────
os.environ.setdefault("TELEGRAM_BOT_TOKEN",  "test:token")
os.environ.setdefault("TELEGRAM_GROUP_ID",   "-100000001")
os.environ.setdefault("ADMIN_TELEGRAM_ID",   "111")
os.environ.setdefault("USER_2_TELEGRAM_ID",  "222")
os.environ.setdefault("DATABASE_PATH",       ":memory:")

import pytest
import sqlite3
import database.db as db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """
    Each test gets a brand-new in-memory database.
    We monkeypatch get_connection so all db calls hit it transparently.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    # Also patch the module-level DATABASE_PATH used inside db.py
    import config
    monkeypatch.setattr(config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(db, "DATABASE_PATH", db_path, raising=False)

    # Re-init schema
    import importlib
    importlib.reload(db)
    db.init_db()
    yield db_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_user(telegram_id: int, name: str, is_admin: bool = False) -> int:
    """Register a user and return their DB id."""
    db.upsert_user(telegram_id, name, is_admin)
    row = db.get_user_by_telegram_id(telegram_id)
    return row["id"]


def make_match(
    home="Brazil",
    away="Argentina",
    stage="group",
    kickoff="2099-01-01 12:00:00",
    api_id=None,
) -> int:
    db.add_match(api_id, home, away, kickoff, stage)
    import sqlite3 as _sq
    import config
    conn = _sq.connect(config.DATABASE_PATH)
    conn.row_factory = _sq.Row
    row = conn.execute(
        "SELECT id FROM matches WHERE home_team=? AND away_team=? ORDER BY id DESC LIMIT 1",
        (home, away),
    ).fetchone()
    conn.close()
    return row["id"]
