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
from bot.keyboards import (
    match_list_keyboard,
    prediction_choice_keyboard,
    home_score_keyboard,
    away_score_keyboard,
    et_keyboard,
    pens_keyboard,
)
from config import (
    ADMIN_TELEGRAM_ID,
    ET_PREDICTION_FROM,
    KNOCKOUT_STAGES,
    SCORE_PREDICTION_FROM,
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


def format_player_block(r: dict, match) -> str:
    """
    Builds one player's result block for the full-time announcement.

    Format:
        Name
        ---
        Winner = +X pts ✅ / 0 pts ❌
        Score — H–A = +X pts ✅ / 0 pts ❌
        ⏱ ET — Yes/No = +X pt ✅ / 0 pts ❌   (knockout only)
        🥅 Pens — Yes/No = +X pt ✅ / 0 pts ❌  (ET=Yes only)  OR  🥅 Pens = N/A
        ---
        Total pts from this match: +X
    """
    from config import KNOCKOUT_STAGES
    lines = [f"*{r['name']}*", "---"]

    # ── Winner
    if r.get("missed"):
        lines.append(f"No prediction = {r['points']} pts ❌")
    elif r["correct"]:
        lines.append(f"{r['prediction_display']} = +{r['points']} pts ✅")
    else:
        lines.append(f"{r['prediction_display']} = 0 pts ❌")

    # ── Score bonus
    if r.get("score_bonus") is not None:
        sp = r.get("score_pred", "")
        if r["score_bonus"] > 0:
            lines.append(f"Score — {sp} = +{r['score_bonus']} pts ✅")
        else:
            lines.append(f"Score — {sp} = 0 pts ❌")

    # ── ET + Pens (knockout matches only)
    if match["stage"] in KNOCKOUT_STAGES:
        et_bonus = r.get("et_bonus")
        et_pred  = r.get("et_pred")   # 0, 1, or None (no prediction)

        if et_bonus is not None:
            et_val = "Yes" if et_pred == 1 else "No"
            if et_bonus > 0:
                lines.append(f"⏱ ET — {et_val} = +{et_bonus} pt ✅")
            else:
                lines.append(f"⏱ ET — {et_val} = 0 pts ❌")
        else:
            lines.append("⏱ ET = N/A")

        # Pens line
        pens_bonus = r.get("pens_bonus")
        pens_pred  = r.get("pens_pred")   # 0, 1, or None

        if et_pred is None:
            # No ET prediction at all (missed) — pens is always N/A
            lines.append("🥅 Pens = N/A")
        elif et_pred == 0:
            # ET=No predicted — implicit Pens=No; show result only if pens was graded
            if pens_bonus is not None and pens_pred is not None:
                if pens_bonus > 0:
                    lines.append(f"🥅 Pens — No = +{pens_bonus} pt ✅")
                else:
                    lines.append(f"🥅 Pens — No = 0 pts ❌")
            else:
                lines.append("🥅 Pens = N/A")
        else:
            # ET=Yes predicted — show explicit pens answer
            if pens_bonus is not None and pens_pred is not None:
                pens_val = "Yes" if pens_pred == 1 else "No"
                if pens_bonus > 0:
                    lines.append(f"🥅 Pens — {pens_val} = +{pens_bonus} pt ✅")
                else:
                    lines.append(f"🥅 Pens — {pens_val} = 0 pts ❌")
            else:
                lines.append("🥅 Pens = N/A")

    lines.append("---")

    total = (r["points"]
             + (r.get("score_bonus") or 0)
             + (r.get("et_bonus") or 0)
             + (r.get("pens_bonus") or 0))
    total_str = f"+{total}" if total > 0 else str(total)
    lines.append(f"Total pts from this match: *{total_str}*")
    return "\n".join(lines)


def format_leaderboard(scores: list) -> str:
    if not scores:
        return "No scores yet — get predicting!"
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 *LEADERBOARD*\n"]
    for i, s in enumerate(scores):
        medal      = medals[i] if i < 3 else f"{i+1}."
        accuracy   = (s["correct_predictions"] / s["total_graded"] * 100) if s["total_graded"] else 0
        pts_label  = f"{s['total_points']:+d}" if s["total_points"] != 0 else "0"
        bonus_count = s["score_bonus_count"] if "score_bonus_count" in s.keys() else 0
        bonus_str  = f"  |  ⭐{bonus_count}" if bonus_count > 0 else ""
        lines.append(
            f"{medal} *{s['name']}* — {pts_label} pts  |  {accuracy:.0f}% acc"
            f"  |  🔥{s['current_streak']}{bonus_str}"
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


def match_uses_score_prediction(match) -> bool:
    """
    Returns True if this match is eligible for the score prediction feature.
    Only applies to matches kicking off on or after SCORE_PREDICTION_FROM (Jun 13 UTC).
    """
    cutoff = datetime.fromisoformat(SCORE_PREDICTION_FROM.replace("Z", "+00:00"))
    return kickoff_dt(match) >= cutoff


def match_uses_et_prediction(match) -> bool:
    """
    Returns True if this knockout match gets the ET + pens bonus questions.
    Only applies to matches kicking off on or after ET_PREDICTION_FROM.
    Set ET_PREDICTION_FROM in .env to the kickoff UTC of the first match you want it live for.
    """
    cutoff = datetime.fromisoformat(ET_PREDICTION_FROM.replace("Z", "+00:00"))
    return kickoff_dt(match) >= cutoff


def score_pred_str(pred, home_team: str, away_team: str) -> str:
    """
    Format a stored score prediction as 'Home N–M Away'.
    Returns empty string if no score prediction was entered.
    """
    if pred is None:
        return ""
    h = pred["home_score_pred"]
    a = pred["away_score_pred"]
    if h is None or a is None:
        return ""
    return f"{home_team} {h}–{a} {away_team}"


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
            "*/fixstages* — Re-fetch & correct match stages (use after round transitions)\n"
            "*/setresult* `<match_id> <home_score> <away_score> [home|away] [pens|aet|reg]` — Set result; add winner for level scores; add pens/aet/reg to fix ET flags\n"
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
    text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    # Store in history so follow-up questions ("what time is that in IST?") have context
    db.add_chat_message(update.effective_chat.id, "bot", text, speaker="Arena")


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    scores = db.get_scores()
    text   = format_leaderboard(scores)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    # Store in history so follow-up questions ("what do u think abt this?") have context
    db.add_chat_message(update.effective_chat.id, "bot", text, speaker="Arena")


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

    accuracy    = s["correct_predictions"] / s["total_graded"] * 100
    bonus_count = s["score_bonus_count"] if "score_bonus_count" in s.keys() else 0
    bonus_line  = f"⭐ Score bonuses: {bonus_count}\n" if bonus_count > 0 else ""
    text = (
        f"📊 *{db_user['name']}*\n\n"
        f"🏆 Points:    *{s['total_points']:+d}*\n"
        f"✅ Correct:   {s['correct_predictions']}\n"
        f"❌ Wrong:     {s['wrong_predictions']}\n"
        f"⏭️ Missed:    {s['missed_predictions']}\n"
        f"🎯 Accuracy:  {accuracy:.1f}%\n"
        f"🔥 Streak:    {s['current_streak']}  (best: {s['best_streak']})\n"
        f"{bonus_line}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    # Store in history so follow-up questions have context
    db.add_chat_message(update.effective_chat.id, "bot", text, speaker="Arena")


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
    predictions_by_match = {}
    db_user = db.get_user_by_telegram_id(user.id)
    if db_user:
        for m in matches:
            p = db.get_user_prediction_for_match(db_user["id"], m["id"])
            if p:
                winner_disp = {"home": m["home_team"], "draw": "Draw", "away": m["away_team"]}[p["prediction"]]
                if match_uses_score_prediction(m) and p["home_score_pred"] is not None:
                    predictions_by_match[m["id"]] = f"{winner_disp} ({p['home_score_pred']}–{p['away_score_pred']})"
                else:
                    predictions_by_match[m["id"]] = winner_disp

    status_lines = []
    for m in matches:
        has_pred = m["id"] in predictions_by_match
        icon = "✅" if has_pred else "❓"
        pred_info = f"  → _{predictions_by_match[m['id']]}_" if has_pred else "  → _not set_"
        # Show ET + pens predictions for knockout matches that use the feature
        if has_pred and m["stage"] in KNOCKOUT_STAGES and match_uses_et_prediction(m) and db_user:
            p = db.get_user_prediction_for_match(db_user["id"], m["id"])
            if p and p["predicted_et"] is not None:
                et_label = "ET: Yes ⏱" if p["predicted_et"] == 1 else "ET: No ⚽"
                pred_info += f"  |  _{et_label}_"
            if p and p["predicted_pens"] is not None:
                pens_label = "Pens: Yes 🥅" if p["predicted_pens"] == 1 else "Pens: No ⚽"
                pred_info += f"  |  _{pens_label}_"
        status_lines.append(f"{icon} {m['home_team']} vs {m['away_team']}{pred_info}")

    await update.message.reply_text(
        "⚽ *Your upcoming predictions:*\n\n" +
        "\n".join(status_lines) +
        "\n\nTap a match below to predict or change your pick:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=match_list_keyboard(matches),
    )


async def _notify_group_prediction(context, match, db_user, verb: str, match_id: int) -> None:
    """Post a status-only (no pick revealed) group notification after a prediction is saved."""
    try:
        has_pred, pred_names = db.get_prediction_status(match_id)
        predicted_count = sum(1 for v in has_pred.values() if v)
        total_count     = len(has_pred)
        display_name    = db_user["name"]

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

        await context.bot.send_message(TELEGRAM_GROUP_ID, group_msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        logger.warning("Could not post prediction notification to group: %s", exc)


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
        uses_score = match_uses_score_prediction(match)

        if existing and not existing["locked"]:
            current_display = {
                "home": match["home_team"], "draw": "Draw", "away": match["away_team"]
            }[existing["prediction"]]
            score_line = ""
            if uses_score and existing["home_score_pred"] is not None:
                score_line = (
                    f"\nScore: *{match['home_team']} {existing['home_score_pred']}–"
                    f"{existing['away_score_pred']} {match['away_team']}*"
                )
            header = (
                f"⚽ *{match['home_team']} vs {match['away_team']}*\n"
                f"📍 {stage}  |  🗓 {dt_str}\n\n"
                f"Your current pick: *{current_display}*{score_line}\n"
                f"Change it below, or just leave it:"
            )
        else:
            score_hint = "\n_You'll also predict the exact score for a bonus point!_" if uses_score else ""
            header = (
                f"⚽ *{match['home_team']} vs {match['away_team']}*\n"
                f"📍 {stage}  |  🗓 {dt_str}\n\n"
                f"Choose your prediction:{score_hint}"
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

        # For Jun 13+ matches: show home score picker instead of saving immediately
        if match_uses_score_prediction(match):
            winner_display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[prediction]
            await query.edit_message_text(
                f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
                f"Winner: *{winner_display}* ✅\n\n"
                f"How many goals does *{match['home_team']}* score?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=home_score_keyboard(match_id, prediction),
            )
            return

        # Pre-Jun-13 match — save winner directly (old flow, no score step)
        success, status = db.upsert_prediction(db_user["id"], match_id, prediction)

        if not success:
            await query.edit_message_text("🔒 Predictions are locked for this match.")
            return

        pred_display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[prediction]
        lock_str     = kickoff_dt(match).strftime("%d %b, %H:%M UTC")

        action_line = f"✏️ Changed to *{pred_display}*" if status == "updated" else f"✅ Locked in: *{pred_display}*"
        verb        = status  # 'updated' or 'created'

        await query.edit_message_text(
            f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
            f"{action_line}\n"
            f"🔒 Prediction locks at kickoff: {lock_str}\n\n"
            f"_Change it any time before then with /predict._",
            parse_mode=ParseMode.MARKDOWN,
        )
        await _notify_group_prediction(context, match, db_user, verb, match_id)

    # ── pred:hscore:<match_id>:<winner>:<home_score> — picked home team goals ──
    elif parts[0] == "pred" and parts[1] == "hscore":
        match_id   = int(parts[2])
        winner     = parts[3]
        home_score = int(parts[4])
        match      = db.get_match_by_id(match_id)

        if not match:
            await query.edit_message_text("Match not found.")
            return

        now = datetime.now(timezone.utc)
        if match["status"] != "scheduled" or now >= kickoff_dt(match):
            await query.edit_message_text("🔒 Too late — this match has kicked off.")
            return

        winner_display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[winner]
        await query.edit_message_text(
            f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
            f"Winner: *{winner_display}* ✅\n"
            f"{match['home_team']}: *{home_score}* ✅\n\n"
            f"How many goals does *{match['away_team']}* score?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=away_score_keyboard(match_id, winner, home_score),
        )

    # ── pred:ascore:<match_id>:<winner>:<home>:<away> — picked away team goals ─
    elif parts[0] == "pred" and parts[1] == "ascore":
        match_id        = int(parts[2])
        winner          = parts[3]
        home_score_pred = int(parts[4])
        away_score_pred = int(parts[5])
        match           = db.get_match_by_id(match_id)

        if not match:
            await query.edit_message_text("Match not found.")
            return

        now = datetime.now(timezone.utc)
        if match["status"] != "scheduled" or now >= kickoff_dt(match):
            await query.edit_message_text("🔒 Too late — this match has kicked off.")
            return

        # Knockout matches on/after ET_PREDICTION_FROM: ask ET first
        if match["stage"] in KNOCKOUT_STAGES and match_uses_et_prediction(match):
            winner_display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[winner]
            await query.edit_message_text(
                f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
                f"Winner: *{winner_display}* ✅\n"
                f"Score: *{match['home_team']} {home_score_pred}–{away_score_pred} {match['away_team']}* ✅\n\n"
                f"Will this match go to *extra time*? ⏱\n"
                f"_(+1 bonus if correct, 0 if wrong)_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=et_keyboard(match_id, winner, home_score_pred, away_score_pred),
            )
            return

        # Group stage or pre-ET-feature knockout — save directly
        success, status = db.upsert_prediction(
            db_user["id"], match_id, winner,
            home_score_pred=home_score_pred,
            away_score_pred=away_score_pred,
        )

        if not success:
            await query.edit_message_text("🔒 Predictions are locked for this match.")
            return

        winner_display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[winner]
        lock_str       = kickoff_dt(match).strftime("%d %b, %H:%M UTC")
        verb           = status  # 'updated' or 'created'

        if verb == "updated":
            action_line = f"✏️ Updated: *{winner_display}*"
        else:
            action_line = f"✅ Locked in: *{winner_display}*"

        await query.edit_message_text(
            f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
            f"{action_line}\n"
            f"Score prediction: *{match['home_team']} {home_score_pred}–{away_score_pred} {match['away_team']}*\n"
            f"🔒 Locks at kickoff: {lock_str}\n\n"
            f"_Change any time before kickoff with /predict._",
            parse_mode=ParseMode.MARKDOWN,
        )
        await _notify_group_prediction(context, match, db_user, verb, match_id)


    # ── pred:et:<match_id>:<winner>:<home>:<away>:<0|1> — ET yes/no ─────────────
    elif parts[0] == "pred" and parts[1] == "et":
        match_id        = int(parts[2])
        winner          = parts[3]
        home_score_pred = int(parts[4])
        away_score_pred = int(parts[5])
        predicted_et    = int(parts[6])   # 1 = yes, 0 = no
        match           = db.get_match_by_id(match_id)

        if not match:
            await query.edit_message_text("Match not found.")
            return

        now = datetime.now(timezone.utc)
        if match["status"] != "scheduled" or now >= kickoff_dt(match):
            await query.edit_message_text("🔒 Too late — this match has kicked off.")
            return

        if predicted_et == 1:
            # Yes ET → ask about pens next
            winner_display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[winner]
            await query.edit_message_text(
                f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
                f"Winner: *{winner_display}* ✅\n"
                f"Score: *{match['home_team']} {home_score_pred}–{away_score_pred} {match['away_team']}* ✅\n"
                f"Extra time: *Yes ⏱* ✅\n\n"
                f"Will it go all the way to *penalties*? 🥅\n"
                f"_(+1 bonus if correct, 0 if wrong)_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=pens_keyboard(match_id, winner, home_score_pred, away_score_pred, predicted_et),
            )
            return

        # No ET → save directly (pens is impossible, so predicted_pens = None)
        success, status = db.upsert_prediction(
            db_user["id"], match_id, winner,
            home_score_pred=home_score_pred,
            away_score_pred=away_score_pred,
            predicted_et=0,
            predicted_pens=None,
        )

        if not success:
            await query.edit_message_text("🔒 Predictions are locked for this match.")
            return

        winner_display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[winner]
        lock_str       = kickoff_dt(match).strftime("%d %b, %H:%M UTC")
        verb           = status
        action_line    = f"✏️ Updated: *{winner_display}*" if verb == "updated" else f"✅ Locked in: *{winner_display}*"

        await query.edit_message_text(
            f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
            f"{action_line}\n"
            f"Score: *{match['home_team']} {home_score_pred}–{away_score_pred} {match['away_team']}*\n"
            f"Extra time: *No ⚽*\n"
            f"🔒 Locks at kickoff: {lock_str}\n\n"
            f"_Change any time before kickoff with /predict._",
            parse_mode=ParseMode.MARKDOWN,
        )
        await _notify_group_prediction(context, match, db_user, verb, match_id)

    # ── pred:pens:<match_id>:<winner>:<home>:<away>:<predicted_et>:<0|1> ────────
    elif parts[0] == "pred" and parts[1] == "pens":
        match_id        = int(parts[2])
        winner          = parts[3]
        home_score_pred = int(parts[4])
        away_score_pred = int(parts[5])
        predicted_et    = int(parts[6])
        predicted_pens  = int(parts[7])
        match           = db.get_match_by_id(match_id)

        if not match:
            await query.edit_message_text("Match not found.")
            return

        now = datetime.now(timezone.utc)
        if match["status"] != "scheduled" or now >= kickoff_dt(match):
            await query.edit_message_text("🔒 Too late — this match has kicked off.")
            return

        success, status = db.upsert_prediction(
            db_user["id"], match_id, winner,
            home_score_pred=home_score_pred,
            away_score_pred=away_score_pred,
            predicted_et=predicted_et,
            predicted_pens=predicted_pens,
        )

        if not success:
            await query.edit_message_text("🔒 Predictions are locked for this match.")
            return

        winner_display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[winner]
        pens_display   = "Yes 🥅" if predicted_pens else "No ⚽"
        lock_str       = kickoff_dt(match).strftime("%d %b, %H:%M UTC")
        verb           = status
        action_line    = f"✏️ Updated: *{winner_display}*" if verb == "updated" else f"✅ Locked in: *{winner_display}*"

        await query.edit_message_text(
            f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
            f"{action_line}\n"
            f"Score: *{match['home_team']} {home_score_pred}–{away_score_pred} {match['away_team']}*\n"
            f"Extra time: *Yes ⏱*\n"
            f"Penalties: *{pens_display}*\n"
            f"🔒 Locks at kickoff: {lock_str}\n\n"
            f"_Change any time before kickoff with /predict._",
            parse_mode=ParseMode.MARKDOWN,
        )
        await _notify_group_prediction(context, match, db_user, verb, match_id)


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
            "Usage: `/setresult <match_id> <home_score> <away_score> [home|away]`\n\n"
            "_The optional 4th argument is required for knockout matches that go to AET/pens "
            "(when the 90-min score is level — e.g._ `/setresult 5318 1 1 home`_)._",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        match_id   = int(args[0])
        home_score = int(args[1])
        away_score = int(args[2])
        # Optional args after the score: [home|away] [pens|aet|reg]
        # Order doesn't matter — parsed by value, not position.
        winner_override = None
        duration_flag   = None   # 'pens' | 'aet' | 'reg'
        for _arg in args[3:]:
            if _arg.lower() in ("home", "away"):
                winner_override = _arg.lower()
            elif _arg.lower() in ("pens", "aet", "reg"):
                duration_flag = _arg.lower()
        if winner_override and winner_override not in ("home", "away"):
            await update.message.reply_text("Winner must be `home` or `away`.", parse_mode=ParseMode.MARKDOWN)
            return

        match = db.get_match_by_id(match_id)
        if not match:
            await update.message.reply_text(f"Match ID {match_id} not found.")
            return

        if db.is_match_graded(match_id):
            # Auto-reverse so /setresult can be used directly to correct a wrong score
            db.reverse_grading_for_match(match_id)

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
                    pens_str = ""
                    if match["stage"] in KNOCKOUT_STAGES and pred["predicted_pens"] is not None:
                        pens_str = f" | Pens: {'Yes 🥅' if pred['predicted_pens'] == 1 else 'No ⚽'}"
                    kick_lines.append(f"• {u['name']}: *{disp}*{pens_str}")
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

        # Determine winner to store — needed so grading handles AET/pens correctly.
        # For knockout draws (e.g. 1-1), admin must pass the 4th arg: home or away.
        if winner_override:
            stored_winner = winner_override
        elif home_score > away_score:
            stored_winner = "home"
        elif away_score > home_score:
            stored_winner = "away"
        else:
            stored_winner = None  # non-knockout draw — valid (e.g. group stage)

        # Warn if knockout match ends level but no winner specified
        if stored_winner is None and match["stage"] in KNOCKOUT_STAGES:
            await update.message.reply_text(
                f"⚠️ This is a knockout match and the score is level ({home_score}–{away_score}).\n\n"
                f"Who won on AET/pens?\n"
                f"`/setresult {match_id} {home_score} {away_score} home` — {match['home_team']}\n"
                f"`/setresult {match_id} {home_score} {away_score} away` — {match['away_team']}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Determine went_to_et / went_to_pens to store.
        # Explicit flag wins; then infer from level score + winner; then preserve DB value.
        if duration_flag == "pens":
            went_to_pens_upd: Optional[bool] = True
            went_to_et_upd:   Optional[bool] = True
        elif duration_flag == "aet":
            went_to_pens_upd = False
            went_to_et_upd   = True
        elif duration_flag == "reg":
            went_to_pens_upd = False
            went_to_et_upd   = False
        elif winner_override is not None and home_score == away_score:
            # Level score + explicit winner → definitely pens
            went_to_pens_upd = True
            went_to_et_upd   = True
        else:
            # No flag, no inference — preserve whatever is already in the DB
            # so correcting a score doesn't accidentally wipe ET/pens flags.
            went_to_pens_upd = match["went_to_pens"]
            went_to_et_upd   = match["went_to_et"]

        db.update_match_status(
            match_id, "finished", home_score, away_score, stored_winner,
            went_to_pens=went_to_pens_upd,
            went_to_et=went_to_et_upd,
        )

        from services.scoring import grade_match
        from services.ai import commentary_for_full_time

        results = grade_match(match_id)
        if not results:
            await update.message.reply_text("Grading returned no results — are users registered?")
            return

        commentary = commentary_for_full_time(
            match["home_team"], match["away_team"], home_score, away_score, results
        )
        if went_to_pens_upd:
            suffix = " _(Pens)_"
        elif went_to_et_upd:
            suffix = " _(AET)_"
        else:
            suffix = ""
        lines = [f"🏁 *FULL TIME*\n*{match['home_team']} {home_score}–{away_score} {match['away_team']}*{suffix}\n"]
        for r in results:
            lines.append(format_player_block(r, match))

        if commentary:
            lines.append(f"\n💬 _{commentary}_")

        lines.append(f"\n{format_leaderboard(db.get_scores())}")
        await context.bot.send_message(TELEGRAM_GROUP_ID, "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)
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


async def cmd_fixstages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin: re-fetch all WC matches from football-data.org and correct any wrong stage values.
    Needed because the API uses LAST_32/LAST_16 which were previously unmapped (fell through
    to "group"), causing R32 matches to show a Draw button.
    """
    if not is_admin(update.effective_user.id):
        return

    await update.message.reply_text("Fetching all WC match stages from football-data.org…")
    try:
        from services.football import get_all_wc_matches

        all_fixtures = get_all_wc_matches()
        if not all_fixtures:
            await update.message.reply_text("No matches returned from API — check FOOTBALL_DATA_KEY.")
            return

        fixed   = 0
        skipped = 0
        for f in all_fixtures:
            existing = db.get_match_by_api_id(f["id"])
            if not existing:
                skipped += 1
                continue
            if existing["stage"] != f["stage"]:
                db.update_match_stage(existing["id"], f["stage"])
                fixed += 1

        await update.message.reply_text(
            f"✅ Stage fix complete.\n"
            f"  Fixed: {fixed} matches\n"
            f"  Skipped (not in DB): {skipped} matches\n\n"
            f"Run /matches to verify — R32 matches should now show `round_of_32`."
        )
    except Exception as exc:
        logger.exception("fixstages error")
        await update.message.reply_text(f"Error: {exc}")


async def cmd_regrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: reverse previous grading and re-run with the stored result.
    Usage: /regrade <match_id> [post]
    Add 'post' to also send the corrected full-time announcement to the group.
    """
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: `/regrade <match_id> [post]`\n"
            "_Add_ `post` _to also send the corrected result to the group._",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    match_id   = int(args[0])
    should_post = len(args) > 1 and args[1].lower() == "post"

    if not db.is_match_graded(match_id):
        await update.message.reply_text(f"Match {match_id} hasn't been graded yet. Nothing to regrade.")
        return

    match = db.get_match_by_id(match_id)

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

    if should_post:
        from services.ai import commentary_for_full_time
        home_score = match["home_score"]
        away_score = match["away_score"]
        if match["went_to_pens"]:
            suffix = " _(Pens)_"
        elif match["went_to_et"]:
            suffix = " _(AET)_"
        elif match["winner"] and home_score == away_score:
            suffix = " _(Pens)_"
        else:
            suffix = ""
        commentary = commentary_for_full_time(
            match["home_team"], match["away_team"], home_score, away_score, results
        )
        lines = [f"🏁 *FULL TIME*\n*{match['home_team']} {home_score}–{away_score} {match['away_team']}*{suffix}\n"]
        for r in results:
            lines.append(format_player_block(r, match))
        if commentary:
            lines.append(f"\n💬 _{commentary}_")
        lines.append(f"\n{format_leaderboard(db.get_scores())}")
        await context.bot.send_message(
            TELEGRAM_GROUP_ID, "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )


async def cmd_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: list all matches with internal IDs — needed for /setresult and /regrade."""
    if not is_admin(update.effective_user.id):
        return

    matches = db.get_all_matches(limit=200)
    if not matches:
        await update.message.reply_text("No matches in the database yet.")
        return

    rows = []
    for m in matches:
        dt_str = kickoff_dt(m).strftime("%d %b %H:%M")
        score     = f" {m['home_score']}–{m['away_score']}" if m["home_score"] is not None else ""
        status    = m["status"]
        stage_lbl = STAGE_LABELS.get(m["stage"], m["stage"])
        rows.append(
            f"`#{m['id']}` *{m['home_team']}* vs *{m['away_team']}*{score} — _{status}_ ({dt_str}) [{stage_lbl}]"
        )

    # Split into chunks that fit within Telegram's 4096-char message limit
    chunk, chunk_len = ["📋 *All Matches*\n"], 18
    for row in rows:
        row_len = len(row) + 1  # +1 for newline
        if chunk_len + row_len > 4000:
            await update.message.reply_text("\n".join(chunk), parse_mode=ParseMode.MARKDOWN)
            chunk, chunk_len = [], 0
        chunk.append(row)
        chunk_len += row_len
    if chunk:
        await update.message.reply_text("\n".join(chunk), parse_mode=ParseMode.MARKDOWN)


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


async def cmd_kickoff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin: manually trigger the kickoff reveal for a match that's already started.
    Usage: /kickoff <match_id>
    Use when the bot missed the kickoff moment (restart, Render sleep, etc.).
    Locks predictions, sets status to live, posts the prediction reveal to the group.
    """
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /kickoff <match_id>")
        return

    match_id = int(context.args[0])
    match = db.get_match_by_id(match_id)
    if not match:
        await update.message.reply_text(f"❌ Match {match_id} not found.")
        return
    if match["predictions_revealed"]:
        await update.message.reply_text(
            "⚠️ Kickoff reveal already sent for this match.\n"
            "If you still need to post it again, clear predictions_revealed in the DB first."
        )
        return

    # Lock and move to live
    db.lock_predictions_for_match(match_id)
    db.update_match_status(match_id, "live")

    predictions = db.get_predictions_for_match(match_id)
    all_users   = db.get_all_users()
    stage_label = STAGE_LABELS.get(match["stage"], match["stage"])
    pred_by_uid = {p["user_id"]: p for p in predictions}
    uses_score  = match_uses_score_prediction(match)

    lines = [
        f"🚀 *KICK OFF!*\n"
        f"*{match['home_team']}* vs *{match['away_team']}*  |  {stage_label}\n\n"
        f"📊 *Predictions:*"
    ]
    for user in all_users:
        pred = pred_by_uid.get(user["id"])
        if pred:
            display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[pred["prediction"]]
            score_str = ""
            if uses_score and pred["home_score_pred"] is not None:
                score_str = f" _{pred['home_score_pred']}–{pred['away_score_pred']}_"
            bonus_parts = []
            if match["stage"] in KNOCKOUT_STAGES:
                if pred["predicted_et"] is not None:
                    bonus_parts.append(f"ET: {'Yes ⏱' if pred['predicted_et'] == 1 else 'No ⚽'}")
                if pred["predicted_pens"] is not None:
                    bonus_parts.append(f"Pens: {'Yes 🥅' if pred['predicted_pens'] == 1 else 'No ⚽'}")
            bonus_str = (" | " + " | ".join(bonus_parts)) if bonus_parts else ""
            lines.append(f"• {user['name']}: *{display}*{score_str}{bonus_str}")
        else:
            pen = STAGE_PENALTIES[match["stage"]]
            lines.append(f"• {user['name']}: ❌ No prediction ({pen} pts penalty)")

    import asyncio
    from services.ai import commentary_for_kickoff
    pred_list = [{"name": p["name"], "prediction": p["prediction"]} for p in predictions]
    needle = await asyncio.to_thread(commentary_for_kickoff, match["home_team"], match["away_team"], pred_list)
    if needle:
        lines.append(f"\n💬 _{needle}_")

    await context.bot.send_message(TELEGRAM_GROUP_ID, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    db.mark_predictions_revealed(match_id)
    await update.message.reply_text(f"✅ Kickoff reveal posted for match {match_id}.")


async def cmd_forcedm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin: immediately send prediction DMs for all upcoming matches in the next 48 hours,
    regardless of the prediction_dm_sent flag.
    Useful when the automatic 24h DM failed (e.g. user hadn't /start'd the bot in DM yet).
    """
    if not is_admin(update.effective_user.id):
        return
    if not await assert_private(update):
        return

    # Wider window than the automatic job so admin can force-send for any upcoming match
    matches = db.get_upcoming_matches(limit=20)
    if not matches:
        await update.message.reply_text("No upcoming matches in the database.")
        return

    all_users = db.get_all_users()
    total_sent = 0
    total_skip = 0
    lines = ["📨 *Force-sending prediction DMs…*\n"]

    for match in matches:
        ko     = kickoff_dt(match)
        dt_str = ko.strftime("%d %b, %H:%M UTC")
        stage  = STAGE_LABELS.get(match["stage"], match["stage"])
        from config import STAGE_POINTS, STAGE_PENALTIES
        pts    = STAGE_POINTS[match["stage"]]
        pen    = STAGE_PENALTIES[match["stage"]]
        sent_names = []
        skip_names = []

        for user in all_users:
            pred = db.get_user_prediction_for_match(user["id"], match["id"])
            if pred:
                skip_names.append(user["name"])
                continue
            try:
                from bot.keyboards import prediction_choice_keyboard as _pkb
                await context.bot.send_message(
                    user["telegram_id"],
                    f"⚽ *{match['home_team']} vs {match['away_team']}*\n"
                    f"📍 {stage}  |  🗓 {dt_str}\n"
                    f"Correct: *+{pts} pts*  |  Missed: *{pen} pts*\n\n"
                    f"Tap to pick your winner 👇",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=_pkb(match["id"], match["home_team"], match["away_team"], match["stage"]),
                )
                sent_names.append(user["name"])
                total_sent += 1
            except Exception as exc:
                skip_names.append(f"{user['name']} (failed: {exc})")
                total_skip += 1

        # Always mark as sent once we've actively tried (prevents auto-job re-firing)
        db.mark_prediction_dm_sent(match["id"])

        result_parts = []
        if sent_names:
            result_parts.append(f"✅ DM'd: {', '.join(sent_names)}")
        if skip_names:
            result_parts.append(f"⏭ Skipped: {', '.join(skip_names)}")
        lines.append(
            f"⚽ *{match['home_team']} vs {match['away_team']}* ({dt_str})\n"
            + "  " + "  |  ".join(result_parts)
        )

    lines.append(f"\n_Total: {total_sent} sent, {total_skip} skipped/failed._")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


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

    # Private game — only the two configured players can chat with Arena
    if user.id not in ALLOWED_USER_IDS:
        return

    # ── Group: only respond when @mentioned, replied to, or called by name ───────
    _WAKE_WORDS = {"arena"}   # custom name — add more here if needed
    if chat.type != "private":
        bot_me          = await context.bot.get_me()
        mention_tag     = f"@{bot_me.username}"
        bot_mentioned   = mention_tag in text
        replied_to_bot  = (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and message.reply_to_message.from_user.id == context.bot.id
        )
        name_triggered  = any(
            text.lower().startswith(w) or f" {w}" in text.lower() or f",{w}" in text.lower()
            for w in _WAKE_WORDS
        )
        if not bot_mentioned and not replied_to_bot and not name_triggered:
            return
        # Strip @mention and wake word from text so AI sees a clean message
        text = text.replace(mention_tag, "")
        for w in _WAKE_WORDS:
            import re as _re2
            text = _re2.sub(rf'\b{w}\b', '', text, flags=_re2.IGNORECASE)
        text = text.strip(" ,!?")

    if not text:
        return

    db.register_user_if_new(user.id, user.first_name, is_admin=is_admin(user.id))

    # Use the DB display name (respects /setname) so Arena always gets "Kevin"/"Mathavi"
    # rather than whatever Telegram has as first_name.
    _db_user_chat = db.get_user_by_telegram_id(user.id)
    speaker_name  = _db_user_chat["name"] if _db_user_chat else user.first_name

    # ── Short-circuit: factual match/schedule queries bypass AI entirely ────────
    # AI models reliably ignore context for factual lookups — return real DB data.
    import re as _re
    _MATCH_QUERY_WORDS = {"upcoming", "fixture", "fixtures", "schedule", "matches",
                          "next match", "when is", "what match", "the match", "match on",
                          "match today", "match tomorrow", "first match", "first game",
                          "next game", "when does", "kick off", "kickoff", "kick-off",
                          "start time", "starting time", "games on",
                          "what time does", "when do they play"}
    _MONTH_MAP = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        "june": 6, "july": 7, "august": 8, "january": 1, "february": 2,
        "march": 3, "april": 4, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    text_lower = text.lower()
    if any(w in text_lower for w in _MATCH_QUERY_WORDS):
        # Extract a specific date — month must be adjacent to day number to avoid
        # false positives from everyday words like "may" ("I may watch the match").
        date_filter = None
        _day_str: str | None = None
        _mon_str: str | None = None
        _mp = (
            r"january|february|march|april|may|june|july|august"
            r"|september|october|november|december"
            r"|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
        )
        _d_dmy = _re.search(
            rf'\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/]+(?:of\s+)?({_mp})\b',
            text_lower,
        )
        _d_mdy = _re.search(
            rf'\b({_mp})[\s\-/]+(\d{{1,2}})(?:st|nd|rd|th)?\b',
            text_lower,
        )
        if _d_dmy:
            _day_str, _mon_str = _d_dmy.group(1), _d_dmy.group(2)
        elif _d_mdy:
            _mon_str, _day_str = _d_mdy.group(1), _d_mdy.group(2)
        if _day_str and _mon_str:
            _mn = _MONTH_MAP.get(_mon_str[:3])
            if _mn:
                date_filter = (int(_day_str), _mn)

        all_upcoming = db.get_upcoming_matches(limit=60)
        if all_upcoming:
            # ── Team name filter ──────────────────────────────────────────────
            # Build map of team names in the DB (lowercase → original casing)
            known_teams: dict[str, str] = {}
            for _m in all_upcoming:
                known_teams[_m["home_team"].lower()] = _m["home_team"]
                known_teams[_m["away_team"].lower()] = _m["away_team"]

            # Find a team name from the DB mentioned in the query (longest match wins)
            team_filter_lower: str | None = None
            team_filter_orig:  str | None = None
            for _tl, _to in sorted(known_teams.items(), key=lambda x: -len(x[0])):
                if _tl in text_lower:
                    team_filter_lower = _tl
                    team_filter_orig  = _to
                    break

            # Detect if query asks about a specific entity NOT in the DB (e.g. "croatia")
            # — if so, fall through to AI so it can research the answer.
            _STOP_WORDS = {
                "the", "a", "an", "it", "its", "they", "we", "our", "their",
                "this", "that", "first", "next", "any", "each", "every", "my"
            }
            _team_patterns = [
                r"when does (\w+) play",
                r"does (\w+) play",
                r"(\w+)'s (?:next|first|upcoming)?\s*match",
                r"(\w+)'s (?:next|first|upcoming)?\s*game",
            ]
            specific_word = None
            for _pat in _team_patterns:
                _pm = _re.search(_pat, text_lower)
                if _pm:
                    candidate = _pm.group(1)
                    if candidate not in _STOP_WORDS:
                        specific_word = candidate
                    break

            # If a specific team word found AND it's not in the DB → let AI research it
            if specific_word and not any(specific_word in _tl for _tl in known_teams):
                pass  # fall through to AI
            else:
                # ── Apply team / date filters ──────────────────────────────────
                if team_filter_lower:
                    team_matches = [
                        m for m in all_upcoming
                        if team_filter_lower in m["home_team"].lower()
                        or team_filter_lower in m["away_team"].lower()
                    ]
                    display = team_matches[:8] if team_matches else all_upcoming[:8]
                    header  = (
                        f"📅 *{team_filter_orig}'s upcoming matches*\n"
                        if team_matches else "📅 *Upcoming Matches*\n"
                    )
                    # Layer date filter on top of team filter
                    if date_filter and team_matches:
                        day, month = date_filter
                        by_date = [
                            m for m in team_matches
                            if kickoff_dt(m).month == month and kickoff_dt(m).day == day
                        ]
                        if by_date:
                            display = by_date
                elif date_filter:
                    day, month = date_filter
                    filtered = [
                        m for m in all_upcoming
                        if kickoff_dt(m).month == month and kickoff_dt(m).day == day
                    ]
                    display = filtered if filtered else all_upcoming[:8]
                    header  = (
                        f"📅 *Matches on {_day_str} {_mon_str.capitalize()}*\n"
                        if filtered else "📅 *Upcoming Matches* _(no matches found for that date)_\n"
                    )
                else:
                    display = all_upcoming[:8]
                    header  = "📅 *Upcoming Matches*\n"

                # ── Timezone detection — word-boundary match for short codes ──
                # Prevents "la" hitting "play", "croatia", "la liga", etc.
                from services.research import _TZ_MAP as _tz_map
                from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

                detected_tz    = None
                detected_label = None
                for _city, _tz in sorted(_tz_map.items(), key=lambda x: -len(x[0])):
                    if len(_city) <= 4:
                        # Short codes must be whole words, not substrings
                        if _re.search(rf'\b{_re.escape(_city)}\b', text_lower):
                            detected_tz    = _tz
                            detected_label = _city.title()
                            break
                    elif _city in text_lower:
                        detected_tz    = _tz
                        detected_label = _city.title()
                        break

                lines = [header]
                for m in display:
                    dt = kickoff_dt(m)
                    if detected_tz:
                        try:
                            dt_local   = dt.astimezone(ZoneInfo(detected_tz))
                            time_str   = (
                                f"{dt_local.strftime('%d %b  %H:%M')} {detected_label} "
                                f"({dt.strftime('%H:%M')} UTC)"
                            )
                        except (ZoneInfoNotFoundError, Exception):
                            time_str = dt.strftime('%d %b  %H:%M UTC')
                    else:
                        time_str = dt.strftime('%d %b  %H:%M UTC')
                    lines.append(
                        f"⚽ *{m['home_team']}* vs *{m['away_team']}*\n"
                        f"   🗓 {time_str}  |  "
                        f"{STAGE_LABELS.get(m['stage'], m['stage'])}"
                    )
                # Store the exchange in history so follow-up questions (e.g. "what
                # time is that in KL?") have context about what was just shown.
                db.add_chat_message(chat.id, "user", text, speaker=speaker_name)
                db.add_chat_message(chat.id, "bot", "\n".join(lines), speaker="Arena")
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

    live_matches = db.get_live_matches()
    if live_matches:
        ctx_lines.append("Currently LIVE (in progress right now):")
        for m in live_matches:
            ctx_lines.append(f"  • {m['home_team']} vs {m['away_team']} — LIVE (exact score unknown, search for it)")

    if recent:
        ctx_lines.append("Recent results:")
        for m in recent:
            if m["home_score"] is not None:
                ctx_lines.append(
                    f"  {m['home_team']} {m['home_score']}–{m['away_score']} {m['away_team']}"
                )

    tournament_context = "\n".join(ctx_lines)

    # ── Load memory + conversation history ─────────────────────────────────────
    memories     = db.get_bot_memories(limit=20)
    history_rows = db.get_chat_history(chat.id, limit=30)
    history      = [{"role": h["role"], "speaker": h["speaker"], "content": h["content"]}
                    for h in history_rows]

    # Store incoming message BEFORE generating response so history is current
    db.add_chat_message(chat.id, "user", text, speaker=speaker_name)

    # ── Research: fetch real data for factual queries ───────────────────────────
    from services.research import detect_research_intent, get_city_time, web_search
    import re as _re_r
    research_data: str | None = None
    intent = detect_research_intent(text)

    if intent == "time":
        research_data = await asyncio.to_thread(get_city_time, text)
        logger.info("Research: time lookup for %r → %s", text[:60], research_data or "no match")

    elif intent == "search":
        search_q = _re_r.sub(r'\barena\b', '', text, flags=_re_r.IGNORECASE).strip(" ,!?")

        # Live score query — call football-data.org directly (authoritative, beats web search)
        _SCORE_WORDS = {"score", "result", "winning", "goals", "happening", "how many"}
        _live_score_handled = False
        if any(w in text.lower() for w in _SCORE_WORDS):
            _live_now = db.get_live_matches()
            if _live_now:
                from services.football import get_live_score as _get_live_score
                _score_lines = []
                for m in _live_now:
                    _api_id = m.get("api_match_id")
                    if not _api_id:
                        _score_lines.append(
                            f"{m['home_team']} vs {m['away_team']} — LIVE (no API ID)"
                        )
                        continue
                    _s = await asyncio.to_thread(_get_live_score, _api_id)
                    if _s:
                        _score_lines.append(
                            f"{m['home_team']} {_s['home_score']}–{_s['away_score']} {m['away_team']}"
                        )
                    else:
                        _score_lines.append(
                            f"{m['home_team']} vs {m['away_team']} — LIVE (fetch failed)"
                        )
                if _score_lines:
                    research_data = (
                        "Current live scores (football-data.org, ~1–2 min delay):\n"
                        + "\n".join(_score_lines)
                    )
                    logger.info("Live score fetch: %s", research_data)
                    _live_score_handled = True

        if not _live_score_handled:
            # Vague follow-up? ("who are there?" / "and the squad?" etc.)
            # Enrich with context from the most recent substantial user message in history.
            if len(search_q.split()) < 7:
                for h in reversed(history_rows[-8:]):
                    if (h["role"] == "user"
                            and h["content"].strip() != text.strip()
                            and len(h["content"].split()) > 4):
                        prev = _re_r.sub(r'\barena\b', '', h["content"], flags=_re_r.IGNORECASE).strip()
                        search_q = f"{prev} — {search_q}"
                        break

            logger.info("Research: Tavily search → %r", search_q[:120])
            research_data = await asyncio.to_thread(web_search, search_q)
            logger.info("Research: result length = %d chars",
                        len(research_data) if research_data else 0)

    # ── Generate response ───────────────────────────────────────────────────────
    from services.ai import chat_response, extract_memory
    reply = await asyncio.to_thread(
        chat_response, text, speaker_name, tournament_context, memories, history,
        research_data=research_data,
    )

    if not reply:
        reply = "Something went wrong on my end — try again in a sec."

    db.add_chat_message(chat.id, "bot", reply, speaker="Arena")
    await message.reply_text(reply)

    # ── Background memory extraction — runs on every exchange ──────────────────
    recent_history = db.get_chat_history(chat.id, limit=8)
    if len(recent_history) >= 2:
        snippet = "\n".join(
            f"{h['speaker'] or 'Arena'}: {h['content']}" for h in recent_history
        )
        asyncio.create_task(_store_memory_if_notable(snippet))


# ── Admin: reset all data ──────────────────────────────────────────────────────

async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin: post a custom message to the group as Arena.
    Usage: /send <text>
    Supports Markdown. The message is sent exactly as typed (after the command).
    """
    if not is_admin(update.effective_user.id):
        return
    # Strip the command word (/send or /send@botname) and preserve everything
    # after it exactly — newlines, spacing, all of it.
    import re
    raw = update.message.text or ""
    text = re.sub(r"^/send(@\S+)?", "", raw, flags=re.IGNORECASE).strip()
    if not text:
        await update.message.reply_text(
            "Usage: /send <message>\nExample:\n/send 🏆 Final predictions close in 10 minutes!"
        )
        return
    await context.bot.send_message(TELEGRAM_GROUP_ID, text)
    await update.message.reply_text("✅ Posted to the group.")


async def cmd_clearmemory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: wipe all of Arena's stored memories (useful when wrong facts get saved)."""
    if not is_admin(update.effective_user.id):
        return
    count = db.clear_bot_memories()
    await update.message.reply_text(f"🧹 Cleared {count} memories. Arena starts fresh.")


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
    app.add_handler(CommandHandler("fixstages",    cmd_fixstages))
    app.add_handler(CommandHandler("regrade",      cmd_regrade))
    app.add_handler(CommandHandler("users",        cmd_users))
    app.add_handler(CommandHandler("kickoff",      cmd_kickoff))
    app.add_handler(CommandHandler("forcedm",      cmd_forcedm))
    app.add_handler(CommandHandler("test",         cmd_test))
    app.add_handler(CommandHandler("testsuccess",  cmd_testsuccess))
    app.add_handler(CommandHandler("deletematch",  cmd_deletematch))
    app.add_handler(CommandHandler("send",         cmd_send))
    app.add_handler(CommandHandler("clearmemory",  cmd_clearmemory))
    app.add_handler(CommandHandler("resetdata",    cmd_resetdata))

    # Inline callbacks (prediction flow)
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^pred:"))

    # Natural language — must be registered LAST so commands take priority
    # In groups: only fires on @mention or reply-to-bot (see handle_message)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
