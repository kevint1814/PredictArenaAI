"""
SQLite database layer — all schema definitions and CRUD operations.
Uses WAL mode for better concurrent read performance.
"""

import sqlite3
import logging
from contextlib import contextmanager
from typing import Optional

from config import DATABASE_PATH

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
    is_admin    BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS matches (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    api_match_id         INTEGER UNIQUE,
    home_team            TEXT    NOT NULL,
    away_team            TEXT    NOT NULL,
    kickoff_utc          TEXT    NOT NULL,   -- ISO-8601 UTC
    stage                TEXT    NOT NULL,
    status               TEXT    DEFAULT 'scheduled',  -- scheduled | live | finished
    home_score           INTEGER,
    away_score           INTEGER,
    winner               TEXT,               -- 'home' | 'draw' | 'away' — set on finish, accounts for AET/pens
    reminder_sent_30     BOOLEAN DEFAULT FALSE,
    reminder_sent_15     BOOLEAN DEFAULT FALSE,
    reminder_sent_10     BOOLEAN DEFAULT FALSE,
    reminder_sent_5      BOOLEAN DEFAULT FALSE,
    prediction_dm_sent   BOOLEAN DEFAULT FALSE,
    predictions_revealed BOOLEAN DEFAULT FALSE,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    match_id       INTEGER NOT NULL,
    prediction     TEXT    NOT NULL,   -- 'home' | 'draw' | 'away'
    submitted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP,
    locked         BOOLEAN DEFAULT FALSE,
    points_awarded INTEGER,             -- set when graded; NULL = not yet graded
    FOREIGN KEY (user_id)  REFERENCES users(id),
    FOREIGN KEY (match_id) REFERENCES matches(id),
    UNIQUE(user_id, match_id)
);

CREATE TABLE IF NOT EXISTS scores (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL UNIQUE,
    total_points         INTEGER DEFAULT 0,
    correct_predictions  INTEGER DEFAULT 0,
    wrong_predictions    INTEGER DEFAULT 0,
    missed_predictions   INTEGER DEFAULT 0,
    total_graded         INTEGER DEFAULT 0,
    current_streak       INTEGER DEFAULT 0,
    best_streak          INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS graded_matches (
    match_id  INTEGER PRIMARY KEY,
    graded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (match_id) REFERENCES matches(id)
);

CREATE TABLE IF NOT EXISTS chat_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    role        TEXT    NOT NULL,   -- 'user' | 'bot'
    speaker     TEXT,               -- display name for user messages
    content     TEXT    NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bot_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory      TEXT    NOT NULL,   -- a memorable fact extracted from conversation
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Safe migrations — run on every startup, ignored if already applied
_MIGRATIONS = [
    "ALTER TABLE predictions ADD COLUMN points_awarded INTEGER",
    "ALTER TABLE matches     ADD COLUMN winner TEXT",
    "ALTER TABLE matches     ADD COLUMN prediction_dm_sent BOOLEAN DEFAULT FALSE",
    # Score prediction feature (Jun 13 2026+)
    "ALTER TABLE predictions ADD COLUMN home_score_pred INTEGER",
    "ALTER TABLE predictions ADD COLUMN away_score_pred INTEGER",
    "ALTER TABLE predictions ADD COLUMN score_bonus_awarded INTEGER",
    "ALTER TABLE scores      ADD COLUMN score_bonus_count INTEGER DEFAULT 0",
]


# ── Connection ────────────────────────────────────────────────────────────────

@contextmanager
def get_connection():
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        for migration in _MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists — safe to ignore
    logger.info("Database initialized at %s", DATABASE_PATH)


# ── Users ─────────────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, name: str, is_admin: bool = False) -> None:
    """Insert or update user — always overwrites name. Use for /start and /setname."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO users (telegram_id, name, is_admin)
               VALUES (?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET name = excluded.name""",
            (telegram_id, name, is_admin),
        )
        conn.execute(
            """INSERT OR IGNORE INTO scores (user_id)
               SELECT id FROM users WHERE telegram_id = ?""",
            (telegram_id,),
        )


def register_user_if_new(telegram_id: int, fallback_name: str, is_admin: bool = False) -> None:
    """
    Register user only if they don't already exist.
    Used by post_init so a bot restart never overwrites a name the user set themselves.
    """
    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO users (telegram_id, name, is_admin)
               VALUES (?, ?, ?)""",
            (telegram_id, fallback_name, is_admin),
        )
        conn.execute(
            """INSERT OR IGNORE INTO scores (user_id)
               SELECT id FROM users WHERE telegram_id = ?""",
            (telegram_id,),
        )


def get_user_by_telegram_id(telegram_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()


def get_all_users() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM users ORDER BY id").fetchall()


# ── Matches ───────────────────────────────────────────────────────────────────

def add_match(
    api_match_id: Optional[int],
    home_team: str,
    away_team: str,
    kickoff_utc: str,
    stage: str,
) -> None:
    # Normalize to 'YYYY-MM-DD HH:MM:SS' so SQLite datetime() comparisons work.
    # Handles: '2026-06-11T17:00:00Z', '2026-06-11T17:00:00+00:00', '2026-06-11T17:00:00'
    kickoff_norm = kickoff_utc.replace("T", " ").replace("Z", "")
    if len(kickoff_norm) > 19:
        kickoff_norm = kickoff_norm[:19]  # strip any +00:00 suffix
    kickoff_norm = kickoff_norm.strip()

    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO matches
               (api_match_id, home_team, away_team, kickoff_utc, stage)
               VALUES (?, ?, ?, ?, ?)""",
            (api_match_id, home_team, away_team, kickoff_norm, stage),
        )


def get_match_by_id(match_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()


def get_match_by_api_id(api_match_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM matches WHERE api_match_id = ?", (api_match_id,)
        ).fetchone()


def update_match_stage(match_id: int, stage: str) -> None:
    """Update the stage of a match — used by /fixstages to correct wrong API stage mapping."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE matches SET stage = ? WHERE id = ?", (stage, match_id)
        )


def get_upcoming_matches(limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM matches
               WHERE status = 'scheduled' AND datetime(kickoff_utc) > datetime('now')
               ORDER BY kickoff_utc ASC LIMIT ?""",
            (limit,),
        ).fetchall()


def get_all_matches(limit: int = 20) -> list[sqlite3.Row]:
    """All matches ordered by kickoff — used for /matches admin command."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM matches ORDER BY kickoff_utc ASC LIMIT ?", (limit,)
        ).fetchall()


def get_matches_needing_reminder_check() -> list[sqlite3.Row]:
    """Scheduled matches kicking off within the next 35 minutes."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM matches
               WHERE status = 'scheduled'
               AND datetime(kickoff_utc) > datetime('now')
               AND datetime(kickoff_utc) <= datetime('now', '+35 minutes')""",
        ).fetchall()


def get_matches_just_started() -> list[sqlite3.Row]:
    """
    Any scheduled match whose kickoff time has passed.
    No upper time bound — catches matches missed during a bot restart.
    """
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM matches
               WHERE status = 'scheduled'
               AND datetime(kickoff_utc) <= datetime('now')""",
        ).fetchall()


def get_live_matches() -> list[sqlite3.Row]:
    """Matches that have kicked off but haven't been graded yet."""
    with get_connection() as conn:
        return conn.execute("SELECT * FROM matches WHERE status = 'live'").fetchall()


def update_match_status(
    match_id: int,
    status: str,
    home_score: Optional[int] = None,
    away_score: Optional[int] = None,
    winner: Optional[str] = None,
) -> None:
    with get_connection() as conn:
        if home_score is not None and away_score is not None:
            conn.execute(
                "UPDATE matches SET status=?, home_score=?, away_score=?, winner=? WHERE id=?",
                (status, home_score, away_score, winner, match_id),
            )
        else:
            conn.execute("UPDATE matches SET status=? WHERE id=?", (status, match_id))


def mark_reminder_sent(match_id: int, minutes: int) -> None:
    col = f"reminder_sent_{minutes}"
    with get_connection() as conn:
        conn.execute(f"UPDATE matches SET {col}=TRUE WHERE id=?", (match_id,))


def mark_predictions_revealed(match_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE matches SET predictions_revealed=TRUE WHERE id=?", (match_id,)
        )


def get_matches_needing_prediction_dm() -> list[sqlite3.Row]:
    """Scheduled matches kicking off within the next 24 hours that haven't had a prediction DM sent."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM matches
               WHERE status = 'scheduled'
               AND datetime(kickoff_utc) > datetime('now', '+5 minutes')
               AND datetime(kickoff_utc) <= datetime('now', '+24 hours')
               AND prediction_dm_sent = FALSE""",
        ).fetchall()


def mark_prediction_dm_sent(match_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE matches SET prediction_dm_sent=TRUE WHERE id=?", (match_id,)
        )


# ── Predictions ───────────────────────────────────────────────────────────────

def upsert_prediction(
    user_id: int,
    match_id: int,
    prediction: str,
    home_score_pred: Optional[int] = None,
    away_score_pred: Optional[int] = None,
) -> tuple[bool, str]:
    """Insert or update a prediction. Returns (success, 'created'|'updated'|'locked')."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id, locked FROM predictions WHERE user_id=? AND match_id=?",
            (user_id, match_id),
        ).fetchone()

        if existing:
            if existing["locked"]:
                return False, "locked"
            conn.execute(
                """UPDATE predictions
                   SET prediction=?, home_score_pred=?, away_score_pred=?,
                       updated_at=datetime('now')
                   WHERE user_id=? AND match_id=?""",
                (prediction, home_score_pred, away_score_pred, user_id, match_id),
            )
            return True, "updated"
        else:
            conn.execute(
                """INSERT INTO predictions
                   (user_id, match_id, prediction, home_score_pred, away_score_pred)
                   VALUES (?,?,?,?,?)""",
                (user_id, match_id, prediction, home_score_pred, away_score_pred),
            )
            return True, "created"


def lock_predictions_for_match(match_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE predictions SET locked=TRUE WHERE match_id=?", (match_id,))


def set_prediction_points(user_id: int, match_id: int, points: int) -> None:
    """Record the points awarded for this prediction (enables clean regrade)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE predictions SET points_awarded=? WHERE user_id=? AND match_id=?",
            (points, user_id, match_id),
        )


def set_score_bonus(user_id: int, match_id: int, bonus_pts: int) -> None:
    """
    Record and apply a score-prediction bonus.
    bonus_pts = 0 → no bonus (score was wrong or not entered), still recorded so regrade works.
    bonus_pts > 0 → add to total_points and increment score_bonus_count.
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE predictions SET score_bonus_awarded=? WHERE user_id=? AND match_id=?",
            (bonus_pts, user_id, match_id),
        )
        if bonus_pts > 0:
            conn.execute(
                """UPDATE scores
                   SET total_points     = total_points + ?,
                       score_bonus_count = score_bonus_count + 1
                   WHERE user_id=?""",
                (bonus_pts, user_id),
            )


def get_predictions_for_match(match_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """SELECT p.*, u.name, u.telegram_id
               FROM predictions p
               JOIN users u ON p.user_id = u.id
               WHERE p.match_id = ?""",
            (match_id,),
        ).fetchall()


def get_prediction_status(match_id: int) -> tuple[dict[int, bool], dict[int, str]]:
    """Returns {telegram_id: has_predicted}, {telegram_id: name}."""
    with get_connection() as conn:
        users = conn.execute("SELECT id, telegram_id, name FROM users").fetchall()
        preds = conn.execute(
            "SELECT user_id FROM predictions WHERE match_id=?", (match_id,)
        ).fetchall()
        predicted_ids = {p["user_id"] for p in preds}
        has_predicted = {u["telegram_id"]: u["id"] in predicted_ids for u in users}
        names         = {u["telegram_id"]: u["name"] for u in users}
        return has_predicted, names


def get_user_prediction_for_match(user_id: int, match_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM predictions WHERE user_id=? AND match_id=?",
            (user_id, match_id),
        ).fetchone()


# ── Scores ────────────────────────────────────────────────────────────────────

def get_scores() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """SELECT s.*, u.name, u.telegram_id
               FROM scores s JOIN users u ON s.user_id = u.id
               ORDER BY s.total_points DESC""",
        ).fetchall()


def update_score(user_id: int, points_delta: int, outcome: str) -> None:
    """outcome: 'correct' | 'wrong' | 'missed'"""
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO scores (user_id) VALUES (?)", (user_id,))
        s = conn.execute("SELECT * FROM scores WHERE user_id=?", (user_id,)).fetchone()

        new_total   = s["total_points"] + points_delta
        new_graded  = s["total_graded"] + 1
        new_correct = s["correct_predictions"]
        new_wrong   = s["wrong_predictions"]
        new_missed  = s["missed_predictions"]
        new_streak  = s["current_streak"]
        new_best    = s["best_streak"]

        if outcome == "correct":
            new_correct += 1
            new_streak  += 1
            new_best     = max(new_best, new_streak)
        elif outcome == "wrong":
            new_wrong  += 1
            new_streak  = 0
        else:
            new_missed += 1
            new_streak  = 0

        conn.execute(
            """UPDATE scores
               SET total_points=?, correct_predictions=?, wrong_predictions=?,
                   missed_predictions=?, total_graded=?, current_streak=?, best_streak=?
               WHERE user_id=?""",
            (new_total, new_correct, new_wrong, new_missed,
             new_graded, new_streak, new_best, user_id),
        )


def reverse_grading_for_match(match_id: int) -> None:
    """
    Undo the score impact of a previously graded match.
    Uses the stored points_awarded on each prediction row — no double-counting.
    Called by /regrade before re-applying fresh scores.
    """
    with get_connection() as conn:
        users     = conn.execute("SELECT * FROM users").fetchall()
        preds     = conn.execute(
            "SELECT * FROM predictions WHERE match_id=? AND points_awarded IS NOT NULL",
            (match_id,)
        ).fetchall()
        pred_map  = {p["user_id"]: p for p in preds}

        for user in users:
            uid  = user["id"]
            pred = pred_map.get(uid)
            pts  = pred["points_awarded"] if pred else None

            if pts is None:
                # User had no recorded prediction award — they were missed (penalty was applied)
                # We need to reverse the penalty. Get the stage from the match.
                match = conn.execute("SELECT stage FROM matches WHERE id=?", (match_id,)).fetchone()
                if not match:
                    continue
                from config import STAGE_PENALTIES
                pts = STAGE_PENALTIES[match["stage"]]  # the penalty that was applied

            # Reverse: subtract what was previously added
            conn.execute(
                "UPDATE scores SET total_points = total_points - ?, total_graded = total_graded - 1 WHERE user_id=?",
                (pts, uid),
            )
            # Also reverse outcome counters
            if pred and pred["points_awarded"] is not None:
                if pred["points_awarded"] > 0:
                    conn.execute(
                        """UPDATE scores
                           SET correct_predictions = correct_predictions - 1,
                               current_streak = MAX(0, current_streak - 1)
                           WHERE user_id=?""",
                        (uid,),
                    )
                else:
                    conn.execute(
                        "UPDATE scores SET wrong_predictions = wrong_predictions - 1 WHERE user_id=?",
                        (uid,),
                    )
            else:
                conn.execute(
                    "UPDATE scores SET missed_predictions = missed_predictions - 1 WHERE user_id=?",
                    (uid,),
                )

            # Clear stored points_awarded so regrade can write fresh ones
            conn.execute(
                "UPDATE predictions SET points_awarded=NULL WHERE match_id=? AND user_id=?",
                (match_id, uid),
            )

        # ── Also reverse score bonus awards ────────────────────────────────────
        bonus_rows = conn.execute(
            "SELECT user_id, score_bonus_awarded FROM predictions "
            "WHERE match_id=? AND score_bonus_awarded IS NOT NULL",
            (match_id,),
        ).fetchall()
        for row in bonus_rows:
            bonus = row["score_bonus_awarded"]
            if bonus and bonus > 0:
                conn.execute(
                    """UPDATE scores
                       SET total_points      = total_points - ?,
                           score_bonus_count = MAX(0, score_bonus_count - 1)
                       WHERE user_id=?""",
                    (bonus, row["user_id"]),
                )
        conn.execute(
            "UPDATE predictions SET score_bonus_awarded=NULL WHERE match_id=?",
            (match_id,),
        )

        conn.execute("DELETE FROM graded_matches WHERE match_id=?", (match_id,))


def delete_test_match(match_id: int) -> None:
    """
    Hard-delete a test match and all its predictions.
    Call reverse_grading_for_match() before this if the match was graded.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM predictions    WHERE match_id=?", (match_id,))
        conn.execute("DELETE FROM graded_matches WHERE match_id=?", (match_id,))
        conn.execute("DELETE FROM matches        WHERE id=?",       (match_id,))


def mark_match_graded(match_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO graded_matches (match_id) VALUES (?)", (match_id,)
        )


def is_match_graded(match_id: int) -> bool:
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM graded_matches WHERE match_id=?", (match_id,)
        ).fetchone() is not None


def get_recent_finished_matches(limit: int = 5) -> list[sqlite3.Row]:
    """Recently finished matches — used for AI tournament context."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM matches WHERE status = 'finished'
               ORDER BY kickoff_utc DESC LIMIT ?""",
            (limit,),
        ).fetchall()


# ── Chat history (natural language memory) ─────────────────────────────────────

def add_chat_message(chat_id: int, role: str, content: str, speaker: str = None) -> None:
    """
    Store a message in conversation history.
    Automatically trims to the last 40 messages per chat to avoid unbounded growth.
    """
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_history (chat_id, role, speaker, content) VALUES (?, ?, ?, ?)",
            (chat_id, role, speaker, content),
        )
        # Keep only the last 80 rows per chat_id
        conn.execute(
            """DELETE FROM chat_history
               WHERE chat_id = ? AND id NOT IN (
                   SELECT id FROM chat_history WHERE chat_id = ?
                   ORDER BY id DESC LIMIT 80
               )""",
            (chat_id, chat_id),
        )


def get_chat_history(chat_id: int, limit: int = 20) -> list[sqlite3.Row]:
    """Fetch the most recent messages for a chat in chronological order."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT role, speaker, content FROM chat_history
               WHERE chat_id = ? ORDER BY id DESC LIMIT ?""",
            (chat_id, limit),
        ).fetchall()
        return list(reversed(rows))   # oldest-first for the AI prompt


# ── Bot memory (long-term facts about players) ────────────────────────────────

def add_bot_memory(memory: str) -> None:
    """Store a memorable fact extracted from conversation (dedup by content)."""
    with get_connection() as conn:
        # Only store if we don't already have something very similar
        existing = conn.execute(
            "SELECT id FROM bot_memory WHERE memory = ?", (memory,)
        ).fetchone()
        if not existing:
            conn.execute("INSERT INTO bot_memory (memory) VALUES (?)", (memory,))
            # Keep only the last 100 memories
            conn.execute(
                "DELETE FROM bot_memory WHERE id NOT IN "
                "(SELECT id FROM bot_memory ORDER BY id DESC LIMIT 100)"
            )


def get_bot_memories(limit: int = 15) -> list[str]:
    """Get the most recent memorable facts for context."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT memory FROM bot_memory ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [r["memory"] for r in rows]


def clear_bot_memories() -> int:
    """Wipe all stored memories. Returns number of rows deleted."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM bot_memory")
        return cur.rowcount


# ── Full data reset (admin use) ────────────────────────────────────────────────

def reset_all_data() -> None:
    """
    Wipe all user/score/prediction/history data. Matches are preserved.
    The caller is responsible for re-registering users afterwards.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM chat_history")
        conn.execute("DELETE FROM bot_memory")
        conn.execute("DELETE FROM graded_matches")
        conn.execute("DELETE FROM predictions")
        conn.execute("DELETE FROM scores")
        conn.execute("DELETE FROM users")
        # Reset all per-match flags so reminders/DMs fire again after re-registration.
        # Do this for every match regardless of status — predictions are wiped so users
        # need fresh DMs even for matches that were already notified.
        conn.execute(
            "UPDATE matches SET "
            "reminder_sent_30=FALSE, reminder_sent_15=FALSE, reminder_sent_10=FALSE, "
            "reminder_sent_5=FALSE, prediction_dm_sent=FALSE, predictions_revealed=FALSE"
        )
        # Only change status/scores for matches that are no longer scheduled
        conn.execute(
            "UPDATE matches SET status='scheduled', home_score=NULL, away_score=NULL, winner=NULL "
            "WHERE status != 'scheduled'"
        )
