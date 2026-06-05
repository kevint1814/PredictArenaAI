"""
Scoring engine — grades all predictions for a finished match.
Returns a list of result dicts used for the group announcement.
"""

import logging
from typing import Optional

import database.db as db
from config import STAGE_POINTS, STAGE_PENALTIES

logger = logging.getLogger(__name__)


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

    db.mark_match_graded(match_id)
    logger.info("Graded match %d — %d users", match_id, len(results))
    return results
