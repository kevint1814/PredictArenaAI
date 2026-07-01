"""
Scoring engine — grades all predictions for a finished match.
Returns a list of result dicts used for the group announcement.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import database.db as db
from config import ET_PREDICTION_BONUS, PENS_PREDICTION_BONUS, SCORE_PREDICTION_BONUS, SCORE_PREDICTION_FROM, STAGE_POINTS, STAGE_PENALTIES

logger = logging.getLogger(__name__)


def _match_uses_score_prediction(kickoff_utc: str) -> bool:
    """Returns True if kickoff is on or after SCORE_PREDICTION_FROM (Jun 13 UTC)."""
    cutoff = datetime.fromisoformat(SCORE_PREDICTION_FROM.replace("Z", "+00:00"))
    # DB stores kickoff_utc as 'YYYY-MM-DD HH:MM:SS' (UTC, no tz suffix)
    try:
        ko = datetime.strptime(kickoff_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        ko = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)
    return ko >= cutoff


def determine_result(home_score: int, away_score: int) -> str:
    """Return 'home', 'draw', or 'away'."""
    if home_score > away_score:
        return "home"
    if away_score > home_score:
        return "away"
    return "draw"


def prediction_display(prediction: str, home_team: str, away_team: str) -> str:
    return {"home": home_team, "draw": "Draw", "away": away_team}.get(prediction, prediction)


def grade_match(match_id: int) -> list[dict]:
    """
    Score all users for match_id.
    Idempotent — returns [] if already graded.

    Each returned dict:
        name              str
        prediction_display str   — e.g. "Brazil" or "Draw" or "No prediction"
        correct           bool
        missed            bool
        points            int    — delta applied (positive or negative)
        user_id           int
    """
    if db.is_match_graded(match_id):
        logger.info("Match %d already graded — skipping", match_id)
        return []

    match = db.get_match_by_id(match_id)
    if not match or match["home_score"] is None:
        logger.warning("Match %d has no final score yet", match_id)
        return []

    stage          = match["stage"]
    correct_pts    = STAGE_POINTS[stage]
    penalty_pts    = STAGE_PENALTIES[stage]   # already negative

    # Use the stored winner field when available (accounts for AET/penalties in knockouts).
    # Fall back to score comparison for matches set via /setresult without a winner field.
    if match["winner"]:
        actual_result = match["winner"]
    else:
        actual_result = determine_result(match["home_score"], match["away_score"])

    predictions    = db.get_predictions_for_match(match_id)
    pred_by_uid    = {p["user_id"]: p for p in predictions}
    all_users      = db.get_all_users()
    results        = []

    for user in all_users:
        uid  = user["id"]
        pred = pred_by_uid.get(uid)

        if pred is None:
            # Missed — apply penalty (no prediction row to update, just scores)
            db.update_score(uid, penalty_pts, outcome="missed")
            results.append({
                "name":               user["name"],
                "prediction_display": "No prediction ❌",
                "correct":            False,
                "missed":             True,
                "points":             penalty_pts,
                "user_id":            uid,
            })
        elif pred["prediction"] == actual_result:
            # Correct
            db.update_score(uid, correct_pts, outcome="correct")
            db.set_prediction_points(uid, match_id, correct_pts)
            results.append({
                "name":               user["name"],
                "prediction_display": prediction_display(pred["prediction"], match["home_team"], match["away_team"]),
                "correct":            True,
                "missed":             False,
                "points":             correct_pts,
                "user_id":            uid,
            })
        else:
            # Wrong — no penalty, just 0 points; streak resets
            db.update_score(uid, 0, outcome="wrong")
            db.set_prediction_points(uid, match_id, 0)
            results.append({
                "name":               user["name"],
                "prediction_display": prediction_display(pred["prediction"], match["home_team"], match["away_team"]),
                "correct":            False,
                "missed":             False,
                "points":             0,
                "user_id":            uid,
            })

    # ── Score prediction bonus (Jun 13+ matches only) ─────────────────────────
    uses_score_pred = _match_uses_score_prediction(match["kickoff_utc"])
    for result in results:
        uid  = result["user_id"]
        pred = pred_by_uid.get(uid)

        if not uses_score_pred:
            # Feature not active for this match — mark as N/A
            result["score_bonus"] = None
            result["score_pred"]  = None
            continue

        if pred is None or pred["home_score_pred"] is None or pred["away_score_pred"] is None:
            # User missed their prediction entirely, or didn't enter a score
            db.set_score_bonus(uid, match_id, 0)
            result["score_bonus"] = 0
            result["score_pred"]  = None
            continue

        if (int(pred["home_score_pred"]) == match["home_score"] and
                int(pred["away_score_pred"]) == match["away_score"]):
            # Exact score match — award bonus
            db.set_score_bonus(uid, match_id, SCORE_PREDICTION_BONUS)
            result["score_bonus"] = SCORE_PREDICTION_BONUS
            logger.info(
                "Score bonus +%d: %s predicted %d–%d correctly for match %d",
                SCORE_PREDICTION_BONUS, result["name"],
                pred["home_score_pred"], pred["away_score_pred"], match_id,
            )
        else:
            db.set_score_bonus(uid, match_id, 0)
            result["score_bonus"] = 0

        result["score_pred"] = f"{pred['home_score_pred']}–{pred['away_score_pred']}"

    # ── Extra time prediction bonus (knockout matches only) ───────────────────
    if match["went_to_et"] is not None:
        actual_et = 1 if match["went_to_et"] else 0
        for result in results:
            uid  = result["user_id"]
            pred = pred_by_uid.get(uid)
            if pred is None or pred["predicted_et"] is None:
                db.set_et_bonus(uid, match_id, 0)
                result["et_bonus"] = 0
                result["et_pred"]  = None
            elif pred["predicted_et"] == actual_et:
                db.set_et_bonus(uid, match_id, ET_PREDICTION_BONUS)
                result["et_bonus"] = ET_PREDICTION_BONUS
                result["et_pred"]  = pred["predicted_et"]
            else:
                db.set_et_bonus(uid, match_id, 0)
                result["et_bonus"] = 0
                result["et_pred"]  = pred["predicted_et"]

    # ── Penalty prediction bonus (knockout matches only) ───────────────────────
    # went_to_pens is NULL for non-knockout / pre-feature matches — skip silently
    if match["went_to_pens"] is not None:
        actual_pens = 1 if match["went_to_pens"] else 0
        for result in results:
            uid  = result["user_id"]
            pred = pred_by_uid.get(uid)

            # Determine effective pens prediction:
            # - Explicit: predicted_pens was answered (ET=Yes path)
            # - Implicit: predicted_et == 0 (ET=No) → Pens=No is implicit,
            #   since penalties are impossible without extra time
            effective_pens = None
            if pred is not None:
                if pred["predicted_pens"] is not None:
                    effective_pens = pred["predicted_pens"]
                elif pred["predicted_et"] == 0:
                    effective_pens = 0   # ET=No → implicit Pens=No

            if pred is None or effective_pens is None:
                db.set_pens_bonus(uid, match_id, 0)
                result["pens_bonus"] = 0
                result["pens_pred"]  = None
            elif effective_pens == actual_pens:
                db.set_pens_bonus(uid, match_id, PENS_PREDICTION_BONUS)
                result["pens_bonus"] = PENS_PREDICTION_BONUS
                result["pens_pred"]  = effective_pens
            else:
                db.set_pens_bonus(uid, match_id, 0)
                result["pens_bonus"] = 0
                result["pens_pred"]  = effective_pens

    db.mark_match_graded(match_id)
    logger.info("Graded match %d — %d users", match_id, len(results))
    return results
