"""
All Telegram command and callback handlers.

User commands  (work in DM and group):
  /start /help /leaderboard /upcoming /stats

Prediction flow (DM only):
  /predict → inline match list → inline Home/Draw/Away → confirmation

Admin commands (DM only, admin Telegram ID):
  /addmatch  /setresult  /syncmatches  /users  /regrade
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database.db as db
from bot.keyboards import match_list_keyboard, prediction_choice_keyboard
from config import (
    ADMIN_TELEGRAM_ID,
    KNOCKOUT_STAGES,
    STAGE_LABELS,
    STAGE_PENALTIES,
    STAGE_POINTS,
    TELEGRAM_GROUP_ID,
    USER_2_TELEGRAM_ID,
    VALID_STAGES,
)

# Only these two Telegram IDs can use the bot — keeps it strictly private
ALLOWED_USER_IDS: frozenset[int] = frozenset(
    uid for uid in [ADMIN_TELEGRAM_ID, USER_2_TELEGRAM_ID] if uid
)

logger = logging.getLogger(__name__)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def is_admin(telegram_id: int) -> bool:
    return telegram_id == ADMIN_TELEGRAM_ID


def is_private(update: Update) -> bool:
    return update.effective_chat.type == "private"


async def assert_private(update: Update) -> bool:
    """Reply with a note and return False if not a private chat."""
    if not is_private(update):
        await update.message.reply_text("Please send this command to me in a private DM.")
        return False
    return True


def format_leaderboard(scores: list) -> str:
    if not scores:
        return "No scores yet — get predicting!"
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 *LEADERBOARD*\n"]
    for i, s in enumerate(scores):
        medal    = medals[i] if i < 3 else f"{i+1}."
        accuracy = (s["correct_predictions"] / s["total_graded"] * 100) if s["total_graded"] else 0
        pts_label = f"{s['total_points']:+d}" if s["total_points"] != 0 else "0"
        lines.append(
            f"{medal} *{s['name']}* — {pts_label} pts  |  {accuracy:.0f}% acc"
            f"  |  🔥{s['current_streak']}"
        )
    return "\n".join(lines)


def kickoff_dt(match) -> datetime:
    """Parse kickoff_utc from DB row into an aware UTC datetime."""
    raw = match["kickoff_utc"]
    # Handle both 'Z' suffix and '+00:00' and naive strings stored as UTC
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── User commands ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    # ── Access control — private game, only the two configured players ──────────
    if user.id not in ALLOWED_USER_IDS:
        await update.message.reply_text(
            "This is a private prediction game. You're not on the list 🔒"
        )
        return

    # ── Register / update name ───────────────────────────────────────────────────
    # Use Telegram first_name UNLESS the player has already set a custom name via /setname.
    # "Player 1" / "Player 2" are the startup placeholders — always replace those.
    existing = db.get_user_by_telegram_id(user.id)
    placeholder_names = {"Player 1", "Player 2"}
    if not existing or existing["name"] in placeholder_names:
        # First /start or placeholder still set → use Telegram name
        db.upsert_user(user.id, user.first_name, is_admin=is_admin(user.id))
        display_name = user.first_name
    else:
        # Custom name already set via /setname — don't overwrite it
        display_name = existing["name"]

    if is_private(update):
        await update.message.reply_text(
            f"👋 Hey *{display_name}*! PredictArena AI is ready.\n\n"
            "I'll DM you before every match with a one-tap prediction button — "
            "you don't need to do anything until I reach out.\n\n"
            "*/predict* — check or change your current picks\n"
            "*/upcoming* — upcoming matches and stakes\n"
            "*/leaderboard* — current standings\n"
            "*/stats* — your personal breakdown\n"
            "*/setname <name>* — change how I refer to you\n\n"
            "_Results, scores, and roasts post to the group automatically._",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text(
            "⚽ *PredictArena AI* is active! DM me to set up your predictions.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📋 *PredictArena AI*\n\n"
        "*/predict* — Submit or change your prediction _(DM only — private)_\n"
        "*/upcoming* — Upcoming matches\n"
        "*/leaderboard* — Current standings\n"
        "*/stats* — Your stats\n\n"
        "_Predictions lock at kickoff. Results & scores update automatically._"
    )
    if is_admin(update.effective_user.id):
        text += (
            "\n\n*Admin:*\n"
            "*/matches* — List all matches with internal IDs\n"
            "*/addmatch* `<api_id> <Home> <Away> <YYYY-MM-DDTHH:MM:SS> <stage>`\n"
            "  _Use underscores for spaces in team names, e.g._ `Saudi_Arabia`\n"
            "*/syncmatches* — Pull WC fixtures from football-data.org\n"
            "*/setresult* `<match_id> <home_score> <away_score>` — Manual override if auto-check fails\n"
            "*/regrade* `<match_id>` — Reverse and re-run grading\n"
            "*/users* — List registered users\n"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    matches = db.get_upcoming_matches(limit=10)
    if not matches:
        await update.message.reply_text("No upcoming matches scheduled yet.")
        return

    lines = ["📅 *Upcoming Matches*\n"]
    for m in matches:
        dt       = kickoff_dt(m)
        dt_str   = dt.strftime("%d %b  %H:%M UTC")
        stage    = STAGE_LABELS.get(m["stage"], m["stage"])
        pts      = STAGE_POINTS[m["stage"]]
        pen      = STAGE_PENALTIES[m["stage"]]
        lines.append(
            f"⚽ *{m['home_team']}* vs *{m['away_team']}*\n"
            f"   🗓 {dt_str}  |  {stage}\n"
            f"   Correct: +{pts} pts  |  Wrong: 0 pts  |  Missed: {pen} pts\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    scores = db.get_scores()
    await update.message.reply_text(format_leaderboard(scores), parse_mode=ParseMode.MARKDOWN)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Works in both DM and group — shows the requesting user's stats."""
    user = update.effective_user
    db.register_user_if_new(user.id, user.first_name, is_admin=is_admin(user.id))
    db_user = db.get_user_by_telegram_id(user.id)
    scores  = db.get_scores()
    s       = next((x for x in scores if x["telegram_id"] == user.id), None)

    if not s or s["total_graded"] == 0:
        await update.message.reply_text("No graded predictions yet — get playing!")
        return

    accuracy = s["correct_predictions"] / s["total_graded"] * 100
    await update.message.reply_text(
        f"📊 *{db_user['name']}*\n\n"
        f"🏆 Points:    *{s['total_points']:+d}*\n"
        f"✅ Correct:   {s['correct_predictions']}\n"
        f"❌ Wrong:     {s['wrong_predictions']}\n"
        f"⏭️ Missed:    {s['missed_predictions']}\n"
        f"🎯 Accuracy:  {accuracy:.1f}%\n"
        f"🔥 Streak:    {s['current_streak']}  (best: {s['best_streak']})",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Prediction flow (DM only) ──────────────────────────────────────────────────

async def cmd_predict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        await update.message.reply_text(
            "Predictions are private — DM me to submit yours! 🤫"
        )
        return

    user = update.effective_user
    db.register_user_if_new(user.id, user.first_name, is_admin=is_admin(user.id))

    matches = db.get_upcoming_matches(limit=10)
    if not matches:
        await update.message.reply_text("No upcoming matches to predict right now.")
        return

    # Show current prediction status for each match so user knows what they've already submitted
    lines = ["⚽ *Select a match — tap to predict or update your pick:*\n"]
    predictions_by_match = {}
    db_user = db.get_user_by_telegram_id(user.id)
    if db_user:
        for m in matches:
            p = db.get_user_prediction_for_match(db_user["id"], m["id"])
            if p:
                display = {"home": m["home_team"], "draw": "Draw", "away": m["away_team"]}[p["prediction"]]
                predictions_by_match[m["id"]] = display

    await update.message.reply_text(
        "⚽ *Your upcoming predictions:*\n\n" +
        "\n".join(
            f"{'✅' if m['id'] in predictions_by_match else '❓'} "
            f"{m['home_team']} vs {m['away_team']}"
            + (f"  → _{predictions_by_match[m['id']]}_" if m['id'] in predictions_by_match else "  → _not set_")
            for m in matches
        ) + "\n\nTap a match below to predict or change your pick:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=match_list_keyboard(matches),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user    = query.from_user
    db.register_user_if_new(user.id, user.first_name, is_admin=is_admin(user.id))
    db_user = db.get_user_by_telegram_id(user.id)
    if not db_user:
        await query.answer("Please send /start to the bot first.")
        return
    parts   = query.data.split(":")

    # ── pred:match:<match_id> — user selected a match ────────────────────────
    if parts[0] == "pred" and parts[1] == "match":
        match_id = int(parts[2])
        match    = db.get_match_by_id(match_id)

        if not match:
            await query.edit_message_text("Match not found.")
            return

        now = datetime.now(timezone.utc)
        if match["status"] != "scheduled" or now >= kickoff_dt(match):
            await query.edit_message_text(
                "🔒 Predictions are locked for this match — it's already kicked off."
            )
            return

        # Show current prediction if they have one
        existing = db.get_user_prediction_for_match(db_user["id"], match_id)
        stage    = STAGE_LABELS.get(match["stage"], match["stage"])
        dt_str   = kickoff_dt(match).strftime("%d %b  %H:%M UTC")

        if existing and not existing["locked"]:
            current_display = {
                "home": match["home_team"], "draw": "Draw", "away": match["away_team"]
            }[existing["prediction"]]
            header = (
                f"⚽ *{match['home_team']} vs {match['away_team']}*\n"
                f"📍 {stage}  |  🗓 {dt_str}\n\n"
                f"Your current pick: *{current_display}*\n"
                f"Change it below, or just leave it:"
            )
        else:
            header = (
                f"⚽ *{match['home_team']} vs {match['away_team']}*\n"
                f"📍 {stage}  |  🗓 {dt_str}\n\n"
                f"Choose your prediction:"
            )

        await query.edit_message_text(
            header,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=prediction_choice_keyboard(match_id, match["home_team"], match["away_team"], match["stage"]),
        )

    # ── pred:pick:<match_id>:<choice> — user chose Home/Draw/Away ────────────
    elif parts[0] == "pred" and parts[1] == "pick":
        match_id   = int(parts[2])
        prediction = parts[3]   # 'home' | 'draw' | 'away'
        match      = db.get_match_by_id(match_id)

        if not match:
            await query.edit_message_text("Match not found.")
            return

        now = datetime.now(timezone.utc)
        if match["status"] != "scheduled" or now >= kickoff_dt(match):
            await query.edit_message_text(
                "🔒 Too late — this match has kicked off. Prediction locked."
            )
            return

        # Reject "draw" for knockout stages — those matches always produce a winner
        if prediction == "draw" and match["stage"] in KNOCKOUT_STAGES:
            await query.edit_message_text(
                "⚠️ Draw isn't a valid outcome in knockout rounds — one team must win.\n\n"
                "Pick a team:",
                reply_markup=prediction_choice_keyboard(match_id, match["home_team"], match["away_team"], match["stage"]),
            )
            return

        success, status = db.upsert_prediction(db_user["id"], match_id, prediction)

        if not success:
            await query.edit_message_text("🔒 Predictions are locked for this match.")
            return

        pred_display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[prediction]
        lock_str     = kickoff_dt(match).strftime("%d %b, %H:%M UTC")

        # ── DM acknowledgment ───────────────────────────────────────────────────
        if status == "updated":
            action_line = f"✏️ Changed to *{pred_display}*"
            verb        = "updated"
        else:
            action_line = f"✅ Locked in: *{pred_display}*"
            verb        = "submitted"

        await query.edit_message_text(
            f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
            f"{action_line}\n"
            f"🔒 Prediction locks at kickoff: {lock_str}\n\n"
            f"_Change it any time before then with /predict._",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── Group notification (shows status, never reveals the actual pick) ────
        try:
            has_pred, pred_names = db.get_prediction_status(match_id)
            predicted_count = sum(1 for v in has_pred.values() if v)
            total_count     = len(has_pred)

            display_name = db_user["name"]
            if verb == "updated":
                group_msg = (
                    f"✏️ *{display_name}* updated their prediction for "
                    f"*{match['home_team']} vs {match['away_team']}*"
                )
            else:
                group_msg = (
                    f"✅ *{display_name}* has locked in a prediction for "
                    f"*{match['home_team']} vs {match['away_team']}*"
                )

            if predicted_count == total_count:
                group_msg += "\n\n🔥 Both players are locked in — predictions reveal at kickoff!"
            else:
                waiting = [pred_names[tid] for tid, done in has_pred.items() if not done]
                if waiting:
                    group_msg += f"\n⏳ Still waiting on: *{', '.join(waiting)}*"

            await context.bot.send_message(
                TELEGRAM_GROUP_ID, group_msg, parse_mode=ParseMode.MARKDOWN
            )
        except Exception as exc:
            logger.warning("Could not post prediction notification to group: %s", exc)


# ── Admin commands ─────────────────────────────────────────────────────────────

async def cmd_addmatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if not await assert_private(update):
        return

    # /addmatch <api_id> <Home_Team> <Away_Team> <YYYY-MM-DDTHH:MM:SS> <stage>
    # Team names with spaces: use underscores, they'll be converted
    args = context.args
    if not args or len(args) < 5:
        await update.message.reply_text(
            "Usage:\n`/addmatch <api_id> <Home_Team> <Away_Team> <YYYY-MM-DDTHH:MM:SS> <stage>`\n\n"
            "Use underscores for team names with spaces.\n"
            "Valid stages: " + ", ".join(VALID_STAGES),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        raw_id    = args[0]
        api_id    = None if raw_id.lower() in ("none", "null", "-") else int(raw_id)
        home      = args[1].replace("_", " ")
        away      = args[2].replace("_", " ")
        kickoff   = args[3]
        stage     = args[4].lower()

        if stage not in VALID_STAGES:
            await update.message.reply_text(f"Invalid stage. Use one of: {', '.join(VALID_STAGES)}")
            return

        db.add_match(api_id, home, away, kickoff, stage)
        await update.message.reply_text(
            f"✅ Match added!\n*{home}* vs *{away}*\n{kickoff} UTC\nStage: {stage}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


async def cmd_setresult(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Manual override — only needed if the automatic result checker fails
    (e.g. no FOOTBALL_DATA_KEY set, or match has no api_match_id).
    """
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "Usage: `/setresult <match_id> <home_score> <away_score>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        match_id   = int(args[0])
        home_score = int(args[1])
        away_score = int(args[2])

        match = db.get_match_by_id(match_id)
        if not match:
            await update.message.reply_text(f"Match ID {match_id} not found.")
            return

        if db.is_match_graded(match_id):
            await update.message.reply_text(
                "This match is already graded. Use /regrade if you need to correct it."
            )
            return

        # If the match never went 'live' (e.g. /setresult called before kickoff time),
        # post the kickoff reveal first so the group sees the full flow.
        if match["status"] == "scheduled":
            db.lock_predictions_for_match(match_id)
            predictions  = db.get_predictions_for_match(match_id)
            all_users_db = db.get_all_users()
            stage_label  = STAGE_LABELS.get(match["stage"], match["stage"])
            pred_by_uid  = {p["user_id"]: p for p in predictions}

            kick_lines = [
                f"🚀 *KICK OFF!*\n"
                f"*{match['home_team']}* vs *{match['away_team']}*  |  {stage_label}\n\n"
                f"📊 *Predictions:*"
            ]
            for u in all_users_db:
                pred = pred_by_uid.get(u["id"])
                if pred:
                    disp = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[pred["prediction"]]
                    kick_lines.append(f"• {u['name']}: *{disp}*")
                else:
                    pen = STAGE_PENALTIES[match["stage"]]
                    kick_lines.append(f"• {u['name']}: ❌ No prediction ({pen} pts penalty)")

            try:
                await context.bot.send_message(
                    TELEGRAM_GROUP_ID, "\n".join(kick_lines), parse_mode=ParseMode.MARKDOWN
                )
            except Exception as exc:
                logger.warning("Could not post kickoff reveal: %s", exc)
            db.mark_predictions_revealed(match_id)

        db.update_match_status(match_id, "finished", home_score, away_score)

        from services.scoring import grade_match
        from services.ai import commentary_for_full_time

        results = grade_match(match_id)
        if not results:
            await update.message.reply_text("Grading returned no results — are users registered?")
            return

        commentary = commentary_for_full_time(
            match["home_team"], match["away_team"], home_score, away_score, results
        )
        lines = [
            f"🏁 *FULL TIME*\n"
            f"*{match['home_team']} {home_score}–{away_score} {match['away_team']}*\n"
        ]
        for r in results:
            if r["correct"]:
                emoji, pts_str = "✅", f"+{r['points']}"
            elif r.get("missed"):
                emoji, pts_str = "❌", str(r["points"])
            else:
                emoji, pts_str = "❌", "0"
            lines.append(f"{emoji} {r['name']}: {r['prediction_display']} → *{pts_str} pts*")

        if commentary:
            lines.append(f"\n💬 _{commentary}_")

        lines.append(f"\n{format_leaderboard(db.get_scores())}")
        await context.bot.send_message(TELEGRAM_GROUP_ID, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text("✅ Done — result posted to group.")

    except Exception as exc:
        logger.exception("setresult error")
        await update.message.reply_text(f"Error: {exc}")


async def cmd_syncmatches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    await update.message.reply_text("Syncing upcoming WC fixtures from football-data.org…")
    try:
        from services.football import get_upcoming_matches as fetch_fixtures
        from config import FOOTBALL_DATA_KEY

        if not FOOTBALL_DATA_KEY:
            await update.message.reply_text(
                "FOOTBALL_DATA_KEY is not set.\n"
                "Register free at football-data.org, then add it to your .env.\n"
                "Or add matches manually with /addmatch."
            )
            return

        fixtures = fetch_fixtures()
        if not fixtures:
            await update.message.reply_text("No upcoming fixtures returned. The season may not be loaded yet.")
            return

        added = 0
        for f in fixtures:
            db.add_match(f["id"], f["home"], f["away"], f["kickoff_utc"], f["stage"])
            added += 1

        await update.message.reply_text(f"✅ Synced {added} upcoming matches.")
    except Exception as exc:
        logger.exception("syncmatches error")
        await update.message.reply_text(f"Error: {exc}")


async def cmd_regrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: reverse previous grading and re-run with the stored result."""
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/regrade <match_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    match_id = int(args[0])

    if not db.is_match_graded(match_id):
        await update.message.reply_text(f"Match {match_id} hasn't been graded yet. Nothing to regrade.")
        return

    # Reverse the previously applied score deltas, then re-grade cleanly
    db.reverse_grading_for_match(match_id)
    db.update_match_status(match_id, "finished")   # ensure status is correct

    from services.scoring import grade_match
    results = grade_match(match_id)

    if not results:
        await update.message.reply_text("Regrade returned no results — check the match has a score set.")
        return

    await update.message.reply_text(
        f"✅ Regraded match {match_id}: {len(results)} users updated.\n"
        + "\n".join(f"  {r['name']}: {r['points']:+d}" for r in results)
    )


async def cmd_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: list all matches with internal IDs — needed for /setresult and /regrade."""
    if not is_admin(update.effective_user.id):
        return

    matches = db.get_all_matches(limit=60)
    if not matches:
        await update.message.reply_text("No matches in the database yet.")
        return

    lines = ["📋 *All Matches*\n"]
    for m in matches:
        dt_str = kickoff_dt(m).strftime("%d %b %H:%M")
        score  = f" {m['home_score']}–{m['away_score']}" if m["home_score"] is not None else ""
        status = m["status"]
        lines.append(
            f"`#{m['id']}` *{m['home_team']}* vs *{m['away_team']}*{score} — _{status}_ ({dt_str})"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return

    users = db.get_all_users()
    if not users:
        await update.message.reply_text("No registered users yet.")
        return

    lines = ["👥 *Registered Users*\n"]
    for u in users:
        role = "👑 Admin" if u["is_admin"] else "👤"
        lines.append(f"{role} *{u['name']}* — TG ID: `{u['telegram_id']}`")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_setname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /setname <display name>
    Lets a player set a custom display name — overrides the Telegram first_name
    used on /start. Works in DM only (names are personal).
    """
    user = update.effective_user

    if user.id not in ALLOWED_USER_IDS:
        return

    if not is_private(update):
        await update.message.reply_text("Please DM me to change your name.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: `/setname <your name>`\nExample: `/setname Mathavi`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    new_name = " ".join(context.args).strip()
    if len(new_name) < 1 or len(new_name) > 32:
        await update.message.reply_text("Name must be between 1 and 32 characters.")
        return

    db.upsert_user(user.id, new_name, is_admin=is_admin(user.id))
    await update.message.reply_text(
        f"✅ Done! I'll call you *{new_name}* from now on.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Test commands ─────────────────────────────────────────────────────────────

_TEST_HOME = "Test United"
_TEST_AWAY = "Mock City"


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin: spin up a dummy match kicking off in 5 minutes to verify the full bot flow.
    - Immediately DMs both users with the prediction keyboard
    - Immediately posts a demo 30-min reminder to the group (marks 30/15/10 as sent)
    - The real 5-min reminder fires naturally within the next job cycle (~60s)
    - Kickoff reveal fires automatically at t+5min
    - Then run /setresult <id> 2 1, followed by /testsuccess to clean up
    """
    if not is_admin(update.effective_user.id):
        return
    if not await assert_private(update):
        return

    kickoff     = datetime.now(timezone.utc) + timedelta(minutes=5)
    kickoff_str = kickoff.strftime("%Y-%m-%d %H:%M:%S")

    db.add_match(None, _TEST_HOME, _TEST_AWAY, kickoff_str, "group")

    # Retrieve the match we just inserted
    all_matches = db.get_all_matches(limit=200)
    test_match  = next((m for m in reversed(all_matches) if m["home_team"] == _TEST_HOME), None)
    if not test_match:
        await update.message.reply_text("❌ Failed to create test match.")
        return

    match_id  = test_match["id"]
    context.application.bot_data["test_match_id"] = match_id
    db.mark_prediction_dm_sent(match_id)   # prevent job re-sending

    all_users = db.get_all_users()

    # ── Immediately DM every user with the prediction keyboard ──────────────────
    for user in all_users:
        try:
            await context.bot.send_message(
                user["telegram_id"],
                f"🧪 *TEST MATCH — kicks off in 5 minutes!*\n\n"
                f"⚽ *{_TEST_HOME} vs {_TEST_AWAY}*\n"
                f"📍 Group Stage  |  🗓 {kickoff.strftime('%d %b, %H:%M UTC')}\n"
                f"Correct: *+1 pt*  |  Missed: *-1 pt*\n\n"
                f"Tap to pick your winner 👇",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=prediction_choice_keyboard(match_id, _TEST_HOME, _TEST_AWAY, "group"),
            )
        except Exception as exc:
            logger.warning("Could not DM %s for test: %s", user["name"], exc)

    # ── Post a demo group reminder immediately to show the format ───────────────
    # Mark 30/15/10 as already sent so the job only fires the real 5-min reminder
    status_parts = [f"{u['name']} ❌" for u in all_users]
    try:
        await context.bot.send_message(
            TELEGRAM_GROUP_ID,
            f"🔔 *PREDICTION REMINDER — 30 mins to kickoff*\n\n"
            f"⚽ *{_TEST_HOME}* vs *{_TEST_AWAY}*\n"
            f"📍 Group Stage\n"
            f"💰 Correct: +1 pts  |  Wrong: 0 pts  |  Missed: -1 pts\n\n"
            f"Predictions: {' | '.join(status_parts)}\n\n"
            f"_DM me /predict to lock yours in!_",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        logger.warning("Could not post test reminder to group: %s", exc)

    for mins in [30, 15, 10]:
        db.mark_reminder_sent(match_id, mins)

    await update.message.reply_text(
        f"✅ *Test match created* (ID: `{match_id}`)\n\n"
        f"⚽ *{_TEST_HOME} vs {_TEST_AWAY}*  |  kicks off in 5 min\n\n"
        f"📋 What to expect:\n"
        f"• 5-min reminder fires in the group within ~60s\n"
        f"• Kickoff reveal + AI needle at t+5 min\n"
        f"• Then run: `/setresult {match_id} 2 1` to post the result\n"
        f"• Finally: `/testsuccess` to wipe all test data",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_deletematch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin: hard-delete any match by ID — useful for removing bad sync data.
    Reverses grading first if the match was already graded.
    Usage: /deletematch <match_id>
    """
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/deletematch <match_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    match_id = int(context.args[0])
    match    = db.get_match_by_id(match_id)
    if not match:
        await update.message.reply_text(f"Match ID {match_id} not found.")
        return

    label = f"{match['home_team']} vs {match['away_team']}"
    if db.is_match_graded(match_id):
        db.reverse_grading_for_match(match_id)

    db.delete_test_match(match_id)
    await update.message.reply_text(
        f"🗑 Deleted: *{label}* (ID `{match_id}`)",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_testsuccess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: confirm test passed and delete all associated test data."""
    if not is_admin(update.effective_user.id):
        return

    match_id = context.application.bot_data.get("test_match_id")

    if not match_id:
        # Fallback: search by team name
        all_matches = db.get_all_matches(limit=200)
        test_match  = next((m for m in reversed(all_matches) if m["home_team"] == _TEST_HOME), None)
        if not test_match:
            await update.message.reply_text("No test match found — nothing to clean up.")
            return
        match_id = test_match["id"]

    if db.is_match_graded(match_id):
        db.reverse_grading_for_match(match_id)

    db.delete_test_match(match_id)
    context.application.bot_data.pop("test_match_id", None)

    await update.message.reply_text(
        f"🧹 *Test data wiped.*\n"
        f"Match `{match_id}` and all its predictions deleted. Scores restored.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Natural language handler ───────────────────────────────────────────────────

async def _store_memory_if_notable(snippet: str) -> None:
    """
    Background task: extract one memorable fact from a conversation snippet and store it.
    Runs after each exchange — never blocks the user-facing response.
    """
    try:
        from services.ai import extract_memory
        memory = await asyncio.to_thread(extract_memory, snippet)
        if memory:
            db.add_bot_memory(memory)
            logger.debug("Stored memory: %s", memory)
    except Exception as exc:
        logger.debug("Memory extraction skipped: %s", exc)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Catch-all text handler for natural language chat.

    - DMs: responds to everything (except commands, which are handled elsewhere)
    - Groups: only responds when the bot is @mentioned or someone replies to the bot

    NOTE: For group chat to work, disable privacy mode via @BotFather:
          /setprivacy → <your bot> → Disable
    """
    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    user = message.from_user
    chat = message.chat

    # ── Group: only respond when @mentioned or replied to ──────────────────────
    if chat.type != "private":
        bot_me          = await context.bot.get_me()
        mention_tag     = f"@{bot_me.username}"
        bot_mentioned   = mention_tag in text
        replied_to_bot  = (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and message.reply_to_message.from_user.id == context.bot.id
        )
        if not bot_mentioned and not replied_to_bot:
            return
        text = text.replace(mention_tag, "").strip()

    if not text:
        return

    db.register_user_if_new(user.id, user.first_name, is_admin=is_admin(user.id))

    # ── Short-circuit: factual match/schedule queries bypass AI entirely ────────
    # AI models reliably ignore context for factual lookups — return real DB data.
    import re as _re
    _MATCH_QUERY_WORDS = {"upcoming", "fixture", "fixtures", "schedule", "matches",
                          "next match", "when is", "what match", "the match", "match on",
                          "match today", "match tomorrow", "match on"}
    _MONTH_MAP = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        "june": 6, "july": 7, "august": 8, "january": 1, "february": 2,
        "march": 3, "april": 4, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    text_lower = text.lower()
    if any(w in text_lower for w in _MATCH_QUERY_WORDS):
        # Try to extract a specific date from the query (e.g. "15th june", "june 15")
        date_filter = None
        day_match  = _re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\b', text_lower)
        month_match = next(
            ((abbr, num) for abbr, num in _MONTH_MAP.items() if abbr in text_lower), None
        )
        if day_match and month_match:
            date_filter = (int(day_match.group(1)), month_match[1])  # (day, month)

        all_upcoming = db.get_upcoming_matches(limit=60)
        if all_upcoming:
            if date_filter:
                day, month = date_filter
                filtered = [
                    m for m in all_upcoming
                    if kickoff_dt(m).month == month and kickoff_dt(m).day == day
                ]
                display = filtered if filtered else all_upcoming[:8]
                header  = (
                    f"📅 *Matches on {day_match.group(1)} {month_match[0].capitalize()}*\n"
                    if filtered else "📅 *Upcoming Matches* _(no matches found for that date)_\n"
                )
            else:
                display = all_upcoming[:8]
                header  = "📅 *Upcoming Matches*\n"

            lines = [header]
            for m in display:
                dt = kickoff_dt(m)
                lines.append(
                    f"⚽ *{m['home_team']}* vs *{m['away_team']}*\n"
                    f"   🗓 {dt.strftime('%d %b  %H:%M UTC')}  |  "
                    f"{STAGE_LABELS.get(m['stage'], m['stage'])}"
                )
            await message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
            return
        # No matches in DB — fall through to AI so it can say why

    # ── Build tournament context for the AI ────────────────────────────────────
    scores    = db.get_scores()
    upcoming  = db.get_upcoming_matches(limit=15)  # wider window so AI knows June 15+ matches
    recent    = db.get_recent_finished_matches(limit=4)
    all_users = db.get_all_users()

    player_names = " vs ".join(u["name"] for u in all_users) if all_users else "2 players"
    ctx_lines = [f"FIFA World Cup 2026 — PredictArena prediction game ({player_names})"]

    if scores:
        ctx_lines.append("Standings:")
        for s in scores:
            acc  = f"{s['correct_predictions'] / s['total_graded'] * 100:.0f}%" if s["total_graded"] else "0%"
            ctx_lines.append(
                f"  {s['name']}: {s['total_points']:+d} pts | {acc} accuracy | "
                f"streak {s['current_streak']} | {s['correct_predictions']} correct, "
                f"{s['wrong_predictions']} wrong, {s['missed_predictions']} missed"
            )
    else:
        ctx_lines.append("No matches graded yet — tournament just getting started.")

    if upcoming:
        ctx_lines.append(
            f"CONFIRMED: {len(upcoming)} upcoming matches are loaded. "
            "If asked about upcoming matches, list these:"
        )
        for m in upcoming[:4]:
            dt = kickoff_dt(m)
            ctx_lines.append(
                f"  • {m['home_team']} vs {m['away_team']} — "
                f"{dt.strftime('%d %b %H:%M UTC')} ({STAGE_LABELS.get(m['stage'], m['stage'])})"
            )
    else:
        ctx_lines.append(
            "CONFIRMED: Zero matches in the database. "
            "Do not invent or guess any fixtures. "
            "Tell the user the schedule hasn't been synced yet."
        )

    if recent:
        ctx_lines.append("Recent results:")
        for m in recent:
            if m["home_score"] is not None:
                ctx_lines.append(
                    f"  {m['home_team']} {m['home_score']}–{m['away_score']} {m['away_team']}"
                )

    tournament_context = "\n".join(ctx_lines)

    # ── Load memory + conversation history ─────────────────────────────────────
    memories     = db.get_bot_memories(limit=12)
    history_rows = db.get_chat_history(chat.id, limit=20)
    history      = [{"role": h["role"], "speaker": h["speaker"], "content": h["content"]}
                    for h in history_rows]

    # Store incoming message BEFORE generating response so history is current
    db.add_chat_message(chat.id, "user", text, speaker=user.first_name)

    # ── Generate response ───────────────────────────────────────────────────────
    from services.ai import chat_response, extract_memory
    reply = await asyncio.to_thread(
        chat_response, text, user.first_name, tournament_context, memories, history
    )

    if not reply:
        reply = "Something went wrong on my end — try again in a sec."

    db.add_chat_message(chat.id, "bot", reply, speaker="PredictArena AI")
    await message.reply_text(reply)

    # ── Background memory extraction (non-blocking, every 3rd exchange) ────────
    # Throttled to avoid burning API quota on every single message.
    count_key = f"chat_msg_count_{chat.id}"
    msg_count = context.application.bot_data.get(count_key, 0) + 1
    context.application.bot_data[count_key] = msg_count

    if msg_count % 3 == 0:
        recent_history = db.get_chat_history(chat.id, limit=6)
        if len(recent_history) >= 2:
            snippet = "\n".join(
                f"{h['speaker'] or 'Bot'}: {h['content']}" for h in recent_history
            )
            asyncio.create_task(_store_memory_if_notable(snippet))


# ── Admin: reset all data ──────────────────────────────────────────────────────

async def cmd_resetdata(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin: wipe all users, scores, predictions, and chat history.
    Matches are preserved (no need to re-sync fixtures).
    Both players are re-registered from config immediately after the wipe.
    Use this to fix duplicate user records from messy test runs.
    """
    if not is_admin(update.effective_user.id):
        return
    if not await assert_private(update):
        return

    db.reset_all_data()

    # Re-register both players from config
    from config import ADMIN_TELEGRAM_ID, ADMIN_NAME, USER_2_TELEGRAM_ID, USER_2_NAME
    db.upsert_user(ADMIN_TELEGRAM_ID, ADMIN_NAME, is_admin=True)
    if USER_2_TELEGRAM_ID:
        db.upsert_user(USER_2_TELEGRAM_ID, USER_2_NAME, is_admin=False)

    await update.message.reply_text(
        "🗑 *All data wiped and reset.*\n\n"
        f"Re-registered: *{ADMIN_NAME}* (admin) and *{USER_2_NAME}*.\n"
        "Matches are preserved — run /syncmatches if needed.\n\n"
        "_Both players should send /start to re-open DM channels._",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Registration ───────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("predict",      cmd_predict))
    app.add_handler(CommandHandler("upcoming",     cmd_upcoming))
    app.add_handler(CommandHandler("leaderboard",  cmd_leaderboard))
    app.add_handler(CommandHandler("stats",        cmd_stats))
    app.add_handler(CommandHandler("setname",      cmd_setname))

    # Admin
    app.add_handler(CommandHandler("matches",      cmd_matches))
    app.add_handler(CommandHandler("addmatch",     cmd_addmatch))
    app.add_handler(CommandHandler("setresult",    cmd_setresult))
    app.add_handler(CommandHandler("syncmatches",  cmd_syncmatches))
    app.add_handler(CommandHandler("regrade",      cmd_regrade))
    app.add_handler(CommandHandler("users",        cmd_users))
    app.add_handler(CommandHandler("test",         cmd_test))
    app.add_handler(CommandHandler("testsuccess",  cmd_testsuccess))
    app.add_handler(CommandHandler("deletematch",  cmd_deletematch))
    app.add_handler(CommandHandler("resetdata",    cmd_resetdata))

    # Inline callbacks (prediction flow)
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^pred:"))

    # Natural language — must be registered LAST so commands take priority
    # In groups: only fires on @mention or reply-to-bot (see handle_message)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
