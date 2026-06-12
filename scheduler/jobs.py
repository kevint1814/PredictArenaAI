"""
Scheduled jobs — wired into PTB's built-in job queue.

Jobs:
  job_send_prediction_prompts — every 30 min — DMs each user with prediction keyboard for upcoming matches
  job_reminders               — every 60 s  — 30/15/10/5-min pre-match group reminders
  job_match_starts            — every 60 s  — locks predictions at kickoff, reveals them in group
  job_check_results           — every 3 min — polls football-data.org for finished matches, auto-grades
"""

import asyncio
import logging
from datetime import datetime, timezone

from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

import database.db as db
from bot.handlers import format_leaderboard, kickoff_dt, match_uses_score_prediction
from bot.keyboards import prediction_choice_keyboard
from config import STAGE_LABELS, STAGE_PENALTIES, STAGE_POINTS, TELEGRAM_GROUP_ID

logger = logging.getLogger(__name__)

RESULT_POLL_INTERVAL = 180   # seconds between result checks (3 min — well within free tier limits)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def post_group(bot, text: str) -> None:
    try:
        await bot.send_message(TELEGRAM_GROUP_ID, text, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        logger.error("Failed to post to group: %s", exc)


# ── Job: prediction prompts ────────────────────────────────────────────────────

async def job_send_prediction_prompts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Runs every 30 minutes.
    For any match kicking off within the next 24 hours where the prediction DM
    hasn't been sent yet, DM every user who hasn't predicted with the match details
    and an inline Home/Draw/Away keyboard — no /predict needed.

    Robustness: if ALL user DMs fail (e.g. users haven't /start'd the bot in DM yet),
    the match is NOT marked as sent so the next cycle retries. If some succeeded and
    some failed, the failed users are stored in bot_data for retry on the next cycle.
    """
    # ── Retry previously-failed individual user DMs ───────────────────────────
    # Key is "{telegram_id}_{match_id}" so multiple matches can each have their
    # own retry entry without colliding.
    retry_map: dict = context.application.bot_data.get("pred_dm_retry", {})
    for _rkey, info in list(retry_map.items()):
        tid = info["telegram_id"]
        if info["attempts"] >= 5:
            logger.info("Giving up on retry DM for user %d match %d after 5 attempts", tid, info["match_id"])
            del retry_map[_rkey]
            continue
        # Don't retry if the match has already kicked off
        try:
            ko_str = info["kickoff_utc"].replace(" ", "T")
            if not ko_str.endswith("Z") and "+" not in ko_str:
                ko_str += "+00:00"
            ko = datetime.fromisoformat(ko_str)
            if ko.tzinfo is None:
                ko = ko.replace(tzinfo=timezone.utc)
        except Exception:
            del retry_map[_rkey]
            continue
        if ko <= datetime.now(timezone.utc):
            del retry_map[_rkey]
            continue
        # Skip if user now has a prediction
        from database.db import get_user_by_telegram_id, get_user_prediction_for_match
        db_user = get_user_by_telegram_id(tid)
        if db_user and get_user_prediction_for_match(db_user["id"], info["match_id"]):
            del retry_map[_rkey]
            continue
        try:
            await context.bot.send_message(
                tid,
                f"⚽ *{info['home_team']} vs {info['away_team']}*\n"
                f"📍 {info['stage_label']}  |  🗓 {ko.strftime('%d %b, %H:%M UTC')}\n"
                f"Correct: *+{info['pts']} pts*  |  Missed: *{info['pen']} pts*\n\n"
                f"Tap to pick your winner 👇",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=prediction_choice_keyboard(
                    info["match_id"], info["home_team"], info["away_team"], info["stage"]
                ),
            )
            logger.info("Retry DM succeeded for user %d, match %d", tid, info["match_id"])
            del retry_map[_rkey]
        except Exception as exc:
            info["attempts"] += 1
            logger.warning("Retry DM attempt %d failed for user %d: %s", info["attempts"], tid, exc)
    context.application.bot_data["pred_dm_retry"] = retry_map

    # ── Send fresh prediction DMs for new matches ─────────────────────────────
    matches = db.get_matches_needing_prediction_dm()
    if not matches:
        return

    all_users = db.get_all_users()

    for match in matches:
        ko      = kickoff_dt(match)
        dt_str  = ko.strftime("%d %b, %H:%M UTC")
        stage   = STAGE_LABELS.get(match["stage"], match["stage"])
        pts     = STAGE_POINTS[match["stage"]]
        pen     = STAGE_PENALTIES[match["stage"]]

        sent_to:   set[int] = set()   # telegram_ids successfully DM'd
        failed_to: set[int] = set()   # telegram_ids that failed

        for user in all_users:
            # Skip users who've already submitted a prediction for this match
            pred = db.get_user_prediction_for_match(user["id"], match["id"])
            if pred:
                sent_to.add(user["telegram_id"])   # already handled — counts as reached
                continue
            try:
                await context.bot.send_message(
                    user["telegram_id"],
                    f"⚽ *{match['home_team']} vs {match['away_team']}*\n"
                    f"📍 {stage}  |  🗓 {dt_str}\n"
                    f"Correct: *+{pts} pts*  |  Missed: *{pen} pts*\n\n"
                    f"Tap to pick your winner 👇",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=prediction_choice_keyboard(
                        match["id"], match["home_team"], match["away_team"], match["stage"]
                    ),
                )
                sent_to.add(user["telegram_id"])
                logger.info("Sent prediction prompt to %s for match %d", user["name"], match["id"])
            except Exception as exc:
                failed_to.add(user["telegram_id"])
                logger.warning(
                    "Could not DM user %d (%s) for match %d: %s — "
                    "make sure they've sent /start to the bot in DM",
                    user["telegram_id"], user["name"], match["id"], exc
                )

        all_reached = len(failed_to) == 0
        none_reached = len(sent_to) == 0 and len(failed_to) > 0

        if none_reached:
            # ALL DMs failed — don't mark as sent, let the next cycle retry
            logger.warning(
                "Match %d: all %d prediction DMs failed — NOT marking as sent, will retry next cycle",
                match["id"], len(failed_to)
            )
        else:
            # At least one user reached — mark match as done to prevent re-sending
            db.mark_prediction_dm_sent(match["id"])
            if not all_reached:
                # Store failed users for per-user retry
                retry = context.application.bot_data.setdefault("pred_dm_retry", {})
                for tid in failed_to:
                    _rkey = f"{tid}_{match['id']}"
                    if _rkey not in retry:
                        retry[_rkey] = {
                            "telegram_id": tid,
                            "match_id":   match["id"],
                            "home_team":  match["home_team"],
                            "away_team":  match["away_team"],
                            "stage":      match["stage"],
                            "stage_label": stage,
                            "kickoff_utc": match["kickoff_utc"],
                            "pts":        pts,
                            "pen":        pen,
                            "attempts":   1,
                        }
                        logger.info(
                            "Queued retry DM for user %d, match %d", tid, match["id"]
                        )


# ── Job: reminders ─────────────────────────────────────────────────────────────

async def job_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Send 30/15/10/5-minute pre-match reminders to the group.
    At the 30-minute mark, also personally DM any user who still hasn't predicted
    — the prediction keyboard is included so they can pick in one tap.
    """
    matches = db.get_matches_needing_reminder_check()
    now     = datetime.now(timezone.utc)

    for match in matches:
        ko             = kickoff_dt(match)
        mins_remaining = (ko - now).total_seconds() / 60

        for mins in [30, 15, 10, 5]:
            if match[f"reminder_sent_{mins}"]:
                continue
            if mins_remaining <= mins:
                has_pred, names = db.get_prediction_status(match["id"])
                status_parts = [
                    f"{names[tid]} {'✅' if predicted else '❌'}"
                    for tid, predicted in has_pred.items()
                ]
                stage = STAGE_LABELS.get(match["stage"], match["stage"])

                await post_group(
                    context.bot,
                    f"🔔 *PREDICTION REMINDER — {mins} min{'s' if mins > 1 else ''} to kickoff*\n\n"
                    f"⚽ *{match['home_team']}* vs *{match['away_team']}*\n"
                    f"📍 {stage}\n"
                    f"💰 Correct: +{STAGE_POINTS[match['stage']]} pts  |  "
                    f"Wrong: 0 pts  |  Missed: {STAGE_PENALTIES[match['stage']]} pts\n\n"
                    f"Predictions: {' | '.join(status_parts)}\n\n"
                    f"_DM me /predict to lock yours in!_"
                )

                # At 30 min — personally nudge anyone who still hasn't picked
                if mins == 30:
                    all_users = db.get_all_users()
                    for user in all_users:
                        if has_pred.get(user["telegram_id"]):
                            continue   # already predicted — leave them alone
                        try:
                            dt_str = ko.strftime("%H:%M UTC")
                            await context.bot.send_message(
                                user["telegram_id"],
                                f"⏰ *{mins} minutes to kickoff!*\n"
                                f"⚽ *{match['home_team']} vs {match['away_team']}*\n\n"
                                f"You haven't picked yet — tap below before {dt_str} or take the penalty 👇",
                                parse_mode=ParseMode.MARKDOWN,
                                reply_markup=prediction_choice_keyboard(
                                    match["id"], match["home_team"], match["away_team"], match["stage"]
                                ),
                            )
                        except Exception as exc:
                            logger.warning("Could not nudge user %d: %s", user["telegram_id"], exc)

                db.mark_reminder_sent(match["id"], mins)
                break


# ── Job: match starts ──────────────────────────────────────────────────────────

async def job_match_starts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fires when a match's kickoff time has passed.
    Handles multiple simultaneous match starts correctly — loops over all of them.
    - Locks predictions (no more changes)
    - Posts the prediction reveal to the group
    - Sets status to 'live' so job_check_results starts polling for the result
    """
    started = db.get_matches_just_started()
    if not started:
        return

    for match in started:
        logger.info("Kickoff: %s vs %s (match %d)", match["home_team"], match["away_team"], match["id"])

        db.lock_predictions_for_match(match["id"])
        db.update_match_status(match["id"], "live")

        if match["predictions_revealed"]:
            continue

        predictions = db.get_predictions_for_match(match["id"])
        all_users   = db.get_all_users()
        stage       = STAGE_LABELS.get(match["stage"], match["stage"])
        pred_by_uid = {p["user_id"]: p for p in predictions}

        uses_score = match_uses_score_prediction(match)
        lines = [
            f"🚀 *KICK OFF!*\n"
            f"*{match['home_team']}* vs *{match['away_team']}*  |  {stage}\n\n"
            f"📊 *Predictions:*"
        ]
        for user in all_users:
            pred = pred_by_uid.get(user["id"])
            if pred:
                display = {"home": match["home_team"], "draw": "Draw", "away": match["away_team"]}[pred["prediction"]]
                if uses_score and pred["home_score_pred"] is not None:
                    score_str = f" _{pred['home_score_pred']}–{pred['away_score_pred']}_"
                else:
                    score_str = ""
                lines.append(f"• {user['name']}: *{display}*{score_str}")
            else:
                pen = STAGE_PENALTIES[match["stage"]]
                lines.append(f"• {user['name']}: ❌ No prediction ({pen} pts penalty)")

        # AI needle — run in thread so it doesn't block the event loop
        from services.ai import commentary_for_kickoff
        pred_list = [
            {"name": p["name"], "prediction": p["prediction"]}
            for p in predictions
        ]
        needle = await asyncio.to_thread(
            commentary_for_kickoff, match["home_team"], match["away_team"], pred_list
        )
        if needle:
            lines.append(f"\n💬 _{needle}_")

        await post_group(context.bot, "\n".join(lines))
        db.mark_predictions_revealed(match["id"])


# ── Job: check results ─────────────────────────────────────────────────────────

async def job_check_results(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Polls football-data.org for all 'live' matches.
    When a match is confirmed finished, auto-grades predictions and posts the result + roast to group.
    Handles multiple matches finishing simultaneously.

    Runs every 3 minutes — very gentle on the free tier (10 calls/min limit).
    """
    live_matches = db.get_live_matches()
    if not live_matches:
        return

    from services.football import get_match_result
    from services.scoring import grade_match
    from services.ai import commentary_for_full_time

    for match in live_matches:
        if db.is_match_graded(match["id"]):
            db.update_match_status(match["id"], "finished")
            continue

        if match["api_match_id"] is None:
            logger.debug("Match %d has no api_match_id — skipping auto result check", match["id"])
            continue

        try:
            result = await asyncio.to_thread(get_match_result, match["api_match_id"])
            if result is None:
                continue

            if not result["finished"]:
                continue

            home_score = result["home_score"]
            away_score = result["away_score"]

            if home_score is None or away_score is None:
                continue

            logger.info("Result confirmed: %s %d–%d %s",
                        match["home_team"], home_score, away_score, match["away_team"])

            db.update_match_status(match["id"], "finished", home_score, away_score, result.get("winner"))
            results = grade_match(match["id"])

            if not results:
                continue

            # ── Build full-time announcement ──────────────────────────────
            commentary = await asyncio.to_thread(
                commentary_for_full_time,
                match["home_team"], match["away_team"],
                home_score, away_score, results,
            )

            lines = [
                f"🏁 *FULL TIME*\n"
                f"*{match['home_team']} {home_score}–{away_score} {match['away_team']}*\n"
            ]
            for r in results:
                if r["correct"]:
                    emoji   = "✅"
                    pts_str = f"+{r['points']}"
                elif r.get("missed"):
                    emoji   = "❌"
                    pts_str = str(r["points"])
                else:
                    emoji   = "❌"
                    pts_str = "0"
                line = f"{emoji} {r['name']}: {r['prediction_display']} → *{pts_str} pts*"
                # Score prediction bonus line (Jun 13+ matches only)
                if r.get("score_bonus") is not None:
                    if r["score_bonus"] > 0:
                        line += f" | ⭐ Score _{r.get('score_pred', '')}_ ✅ +{r['score_bonus']}pt"
                    elif r.get("score_pred"):
                        line += f" | Score _{r['score_pred']}_ ❌"
                lines.append(line)

            if commentary:
                lines.append(f"\n💬 _{commentary}_")

            lines.append(f"\n{format_leaderboard(db.get_scores())}")
            await post_group(context.bot, "\n".join(lines))

        except Exception as exc:
            logger.exception("Error checking result for match %d: %s", match["id"], exc)


# ── Job: auto-sync fixtures ────────────────────────────────────────────────────

async def job_auto_sync_fixtures(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Runs every 6 hours.
    Pulls upcoming WC fixtures from football-data.org and upserts them.
    Handles all rounds automatically:
      - Group stage fixtures are all known upfront
      - Knockout fixtures (R32, R16, QF, SF, Final) appear once teams qualify
    Uses INSERT OR IGNORE so existing matches are never duplicated or overwritten.
    Silently skips if FOOTBALL_DATA_KEY is not set.
    """
    from config import FOOTBALL_DATA_KEY
    if not FOOTBALL_DATA_KEY:
        return

    try:
        from services.football import get_upcoming_matches as fetch_fixtures
        fixtures = await asyncio.to_thread(fetch_fixtures)
        if not fixtures:
            return

        added = 0
        for f in fixtures:
            db.add_match(f["id"], f["home"], f["away"], f["kickoff_utc"], f["stage"])
            added += 1

        logger.info("Auto-sync: upserted %d upcoming fixtures", added)
    except Exception as exc:
        logger.warning("Auto-sync fixtures failed: %s", exc)


# ── Setup ──────────────────────────────────────────────────────────────────────

def setup_jobs(app: Application) -> None:
    jq = app.job_queue
    jq.run_repeating(job_auto_sync_fixtures,      interval=21600, first=5,   name="auto_sync")
    jq.run_repeating(job_send_prediction_prompts, interval=1800,  first=30,  name="prediction_prompts")
    jq.run_repeating(job_reminders,               interval=60,    first=10,  name="reminders")
    jq.run_repeating(job_match_starts,            interval=60,    first=20,  name="match_starts")
    jq.run_repeating(job_check_results,           interval=RESULT_POLL_INTERVAL, first=60, name="check_results")
    logger.info("Jobs scheduled — auto-sync every 6h, result polling every %ds", RESULT_POLL_INTERVAL)
