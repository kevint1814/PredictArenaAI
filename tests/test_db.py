"""
Integration tests — database/db.py

Tests:
  • upsert_prediction (create / update / locked)
  • score accumulation via update_score
  • set_score_bonus / set_pens_bonus / set_et_bonus — points applied once
  • reverse_grading_for_match — full rollback including all bonuses
  • is_match_graded idempotency
  • lock_predictions_for_match
"""
import pytest
from tests.conftest import make_user, make_match
import database.db as db


# ─────────────────────────────────────────────────────────────────────────────
# upsert_prediction
# ─────────────────────────────────────────────────────────────────────────────

class TestUpsertPrediction:
    def test_create_basic(self, fresh_db):
        uid = make_user(111, "Kevin", is_admin=True)
        mid = make_match()
        ok, status = db.upsert_prediction(uid, mid, "home")
        assert ok is True
        assert status == "created"

    def test_update_existing(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match()
        db.upsert_prediction(uid, mid, "home")
        ok, status = db.upsert_prediction(uid, mid, "away")
        assert ok is True
        assert status == "updated"
        pred = db.get_user_prediction_for_match(uid, mid)
        assert pred["prediction"] == "away"

    def test_locked_prediction_rejected(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match()
        db.upsert_prediction(uid, mid, "home")
        db.lock_predictions_for_match(mid)
        ok, status = db.upsert_prediction(uid, mid, "away")
        assert ok is False
        assert status == "locked"
        # Prediction unchanged
        pred = db.get_user_prediction_for_match(uid, mid)
        assert pred["prediction"] == "home"

    def test_stores_score_prediction(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match()
        db.upsert_prediction(uid, mid, "home", home_score_pred=2, away_score_pred=1)
        pred = db.get_user_prediction_for_match(uid, mid)
        assert pred["home_score_pred"] == 2
        assert pred["away_score_pred"] == 1

    def test_stores_et_and_pens(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=1, away_score_pred=0,
                             predicted_et=1, predicted_pens=0)
        pred = db.get_user_prediction_for_match(uid, mid)
        assert pred["predicted_et"] == 1
        assert pred["predicted_pens"] == 0

    def test_et_no_means_pens_null(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=2, away_score_pred=0,
                             predicted_et=0, predicted_pens=None)
        pred = db.get_user_prediction_for_match(uid, mid)
        assert pred["predicted_et"] == 0
        assert pred["predicted_pens"] is None

    def test_update_overwrites_et_and_pens(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home", predicted_et=0, predicted_pens=None)
        db.upsert_prediction(uid, mid, "home", predicted_et=1, predicted_pens=1)
        pred = db.get_user_prediction_for_match(uid, mid)
        assert pred["predicted_et"] == 1
        assert pred["predicted_pens"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Score accumulation
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreAccumulation:
    def test_correct_adds_points_and_streak(self, fresh_db):
        uid = make_user(111, "Kevin")
        db.update_score(uid, 2, "correct")
        s = db.get_scores()[0]
        assert s["total_points"] == 2
        assert s["correct_predictions"] == 1
        assert s["current_streak"] == 1
        assert s["best_streak"] == 1

    def test_wrong_resets_streak(self, fresh_db):
        uid = make_user(111, "Kevin")
        db.update_score(uid, 2, "correct")
        db.update_score(uid, 0, "wrong")
        s = db.get_scores()[0]
        assert s["current_streak"] == 0
        assert s["wrong_predictions"] == 1

    def test_missed_applies_penalty(self, fresh_db):
        uid = make_user(111, "Kevin")
        db.update_score(uid, -1, "missed")
        s = db.get_scores()[0]
        assert s["total_points"] == -1
        assert s["missed_predictions"] == 1

    def test_best_streak_does_not_decrease(self, fresh_db):
        uid = make_user(111, "Kevin")
        db.update_score(uid, 1, "correct")
        db.update_score(uid, 1, "correct")
        db.update_score(uid, 1, "correct")
        db.update_score(uid, 0, "wrong")   # resets current
        s = db.get_scores()[0]
        assert s["best_streak"] == 3
        assert s["current_streak"] == 0

    def test_multiple_users_independent(self, fresh_db):
        u1 = make_user(111, "Kevin")
        u2 = make_user(222, "Mathavi")
        db.update_score(u1, 5, "correct")
        db.update_score(u2, -1, "missed")
        scores = {s["name"]: s for s in db.get_scores()}
        assert scores["Kevin"]["total_points"] == 5
        assert scores["Mathavi"]["total_points"] == -1


# ─────────────────────────────────────────────────────────────────────────────
# Bonus functions
# ─────────────────────────────────────────────────────────────────────────────

class TestBonusFunctions:
    def test_score_bonus_adds_to_total(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match()
        db.upsert_prediction(uid, mid, "home")
        db.update_score(uid, 1, "correct")
        db.set_score_bonus(uid, mid, 3)
        s = db.get_scores()[0]
        assert s["total_points"] == 4
        assert s["score_bonus_count"] == 1

    def test_score_bonus_zero_does_not_change_total(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match()
        db.upsert_prediction(uid, mid, "home")
        db.update_score(uid, 1, "correct")
        db.set_score_bonus(uid, mid, 0)
        s = db.get_scores()[0]
        assert s["total_points"] == 1
        assert s["score_bonus_count"] == 0

    def test_pens_bonus_adds_to_total(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home", predicted_pens=1)
        db.update_score(uid, 2, "correct")
        db.set_pens_bonus(uid, mid, 1)
        s = db.get_scores()[0]
        assert s["total_points"] == 3

    def test_pens_bonus_zero_does_not_add(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home", predicted_pens=0)
        db.update_score(uid, 2, "correct")
        db.set_pens_bonus(uid, mid, 0)
        s = db.get_scores()[0]
        assert s["total_points"] == 2

    def test_et_bonus_adds_to_total(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home", predicted_et=1)
        db.update_score(uid, 2, "correct")
        db.set_et_bonus(uid, mid, 1)
        s = db.get_scores()[0]
        assert s["total_points"] == 3

    def test_et_bonus_zero_does_not_add(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home", predicted_et=0)
        db.update_score(uid, 2, "correct")
        db.set_et_bonus(uid, mid, 0)
        s = db.get_scores()[0]
        assert s["total_points"] == 2

    def test_all_bonuses_stack(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=2, away_score_pred=1,
                             predicted_et=1, predicted_pens=0)
        db.update_score(uid, 2, "correct")     # winner pts
        db.set_score_bonus(uid, mid, 3)         # exact score
        db.set_et_bonus(uid, mid, 1)            # ET correct
        db.set_pens_bonus(uid, mid, 0)          # pens wrong
        s = db.get_scores()[0]
        assert s["total_points"] == 6           # 2+3+1+0


# ─────────────────────────────────────────────────────────────────────────────
# reverse_grading_for_match
# ─────────────────────────────────────────────────────────────────────────────

class TestReverseGrading:
    def _grade_and_get_score(self, uid, mid, winner_pts, score_bonus, et_bonus, pens_bonus):
        db.update_score(uid, winner_pts, "correct")
        db.set_prediction_points(uid, mid, winner_pts)
        db.set_score_bonus(uid, mid, score_bonus)
        db.set_et_bonus(uid, mid, et_bonus)
        db.set_pens_bonus(uid, mid, pens_bonus)
        db.mark_match_graded(mid)

    def test_reverse_clears_all_points(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=2, away_score_pred=1,
                             predicted_et=1, predicted_pens=1)
        self._grade_and_get_score(uid, mid, 2, 3, 1, 1)   # total = 7
        s_before = db.get_scores()[0]
        assert s_before["total_points"] == 7

        db.reverse_grading_for_match(mid)
        s_after = db.get_scores()[0]
        assert s_after["total_points"] == 0

    def test_reverse_clears_graded_flag(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match()
        db.upsert_prediction(uid, mid, "home")
        db.update_score(uid, 1, "correct")
        db.set_prediction_points(uid, mid, 1)
        db.mark_match_graded(mid)
        assert db.is_match_graded(mid) is True
        db.reverse_grading_for_match(mid)
        assert db.is_match_graded(mid) is False

    def test_reverse_clears_bonus_columns(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home", predicted_et=1, predicted_pens=1)
        db.update_score(uid, 2, "correct")
        db.set_prediction_points(uid, mid, 2)
        db.set_et_bonus(uid, mid, 1)
        db.set_pens_bonus(uid, mid, 1)
        db.mark_match_graded(mid)
        db.reverse_grading_for_match(mid)

        pred = db.get_user_prediction_for_match(uid, mid)
        assert pred["et_bonus_awarded"] is None
        assert pred["pens_bonus_awarded"] is None
        assert pred["points_awarded"] is None

    def test_reverse_then_regrade_correct_total(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        db.upsert_prediction(uid, mid, "home", predicted_et=1, predicted_pens=0)
        self._grade_and_get_score(uid, mid, 2, 0, 1, 0)   # 3 pts total
        db.reverse_grading_for_match(mid)
        # Re-grade with different result
        self._grade_and_get_score(uid, mid, 2, 3, 1, 1)   # 7 pts total
        s = db.get_scores()[0]
        assert s["total_points"] == 7

    def test_missed_penalty_reversed(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32")
        # No prediction — penalty applied
        db.update_score(uid, -2, "missed")
        db.mark_match_graded(mid)
        db.reverse_grading_for_match(mid)
        # Points should be back to 0 (match stage is round_of_32 → -2 penalty)
        s = db.get_scores()[0]
        assert s["total_points"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# is_match_graded idempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestGradedFlag:
    def test_not_graded_by_default(self, fresh_db):
        mid = make_match()
        assert db.is_match_graded(mid) is False

    def test_mark_graded(self, fresh_db):
        mid = make_match()
        db.mark_match_graded(mid)
        assert db.is_match_graded(mid) is True

    def test_mark_graded_twice_is_safe(self, fresh_db):
        mid = make_match()
        db.mark_match_graded(mid)
        db.mark_match_graded(mid)   # INSERT OR IGNORE — should not raise
        assert db.is_match_graded(mid) is True
