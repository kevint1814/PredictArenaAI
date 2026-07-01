"""
Unit + integration tests — services/scoring.py

Tests:
  • determine_result
  • _match_uses_score_prediction cutoff date
  • grade_match: correct / wrong / missed for every stage
  • grade_match: score bonus awarded / not awarded
  • grade_match: ET bonus
  • grade_match: pens bonus
  • grade_match: all bonuses stacked (max points scenario)
  • grade_match: ET=No means pens skipped
  • grade_match: idempotent (second call returns [])
  • grade_match: AET — winner from API used, not score comparison
"""
import pytest
from unittest.mock import patch
from tests.conftest import make_user, make_match
import database.db as db
from services.scoring import determine_result, grade_match, _match_uses_score_prediction


# ─────────────────────────────────────────────────────────────────────────────
# determine_result
# ─────────────────────────────────────────────────────────────────────────────

class TestDetermineResult:
    def test_home_win(self):          assert determine_result(2, 0) == "home"
    def test_away_win(self):          assert determine_result(0, 1) == "away"
    def test_draw(self):              assert determine_result(1, 1) == "draw"
    def test_high_score_home(self):   assert determine_result(7, 1) == "home"
    def test_zero_zero(self):         assert determine_result(0, 0) == "draw"


# ─────────────────────────────────────────────────────────────────────────────
# _match_uses_score_prediction
# ─────────────────────────────────────────────────────────────────────────────

class TestScorePredictionCutoff:
    def test_before_cutoff_returns_false(self):
        assert _match_uses_score_prediction("2026-06-12 23:59:59") is False

    def test_on_cutoff_returns_true(self):
        assert _match_uses_score_prediction("2026-06-13 00:00:00") is True

    def test_after_cutoff_returns_true(self):
        assert _match_uses_score_prediction("2026-07-01 12:00:00") is True

    def test_iso_z_format(self):
        assert _match_uses_score_prediction("2026-06-13T00:00:00Z") is True


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

FUTURE_KO  = "2099-01-01 12:00:00"    # far future → uses score prediction
EARLY_KO   = "2026-06-12 12:00:00"    # before cutoff → no score prediction


def _finish_match(mid, home_score, away_score, winner,
                  went_to_pens=False, went_to_et=False):
    db.update_match_status(
        mid, "finished",
        home_score, away_score,
        winner, went_to_pens, went_to_et,
    )


# ─────────────────────────────────────────────────────────────────────────────
# grade_match — winner points by stage
# ─────────────────────────────────────────────────────────────────────────────

class TestGradeMatchWinnerPoints:
    @pytest.mark.parametrize("stage,pts,penalty", [
        ("group",         1, -1),
        ("round_of_32",   2, -2),
        ("round_of_16",   3, -3),
        ("quarter_final", 5, -3),
        ("semi_final",    8, -3),
        ("final",        15, -3),
    ])
    def test_correct_prediction_points(self, fresh_db, stage, pts, penalty):
        uid = make_user(111, "Kevin")
        mid = make_match(stage=stage, kickoff=EARLY_KO)
        db.upsert_prediction(uid, mid, "home")
        _finish_match(mid, 1, 0, "home")
        results = grade_match(mid)
        r = results[0]
        assert r["correct"] is True
        assert r["points"] == pts

    @pytest.mark.parametrize("stage,pts,penalty", [
        ("group",         1, -1),
        ("round_of_32",   2, -2),
        ("round_of_16",   3, -3),
        ("quarter_final", 5, -3),
        ("semi_final",    8, -3),
        ("final",        15, -3),
    ])
    def test_wrong_prediction_zero_points(self, fresh_db, stage, pts, penalty):
        uid = make_user(111, "Kevin")
        mid = make_match(stage=stage, kickoff=EARLY_KO)
        db.upsert_prediction(uid, mid, "away")
        _finish_match(mid, 1, 0, "home")
        results = grade_match(mid)
        r = results[0]
        assert r["correct"] is False
        assert r["missed"] is False
        assert r["points"] == 0

    @pytest.mark.parametrize("stage,penalty", [
        ("group",         -1),
        ("round_of_32",   -2),
        ("round_of_16",   -3),
        ("quarter_final", -3),
        ("semi_final",    -3),
        ("final",         -3),
    ])
    def test_missed_prediction_penalty(self, fresh_db, stage, penalty):
        uid = make_user(111, "Kevin")
        mid = make_match(stage=stage, kickoff=EARLY_KO)
        # No prediction submitted
        _finish_match(mid, 1, 0, "home")
        results = grade_match(mid)
        r = results[0]
        assert r["missed"] is True
        assert r["points"] == penalty


# ─────────────────────────────────────────────────────────────────────────────
# grade_match — score bonus
# ─────────────────────────────────────────────────────────────────────────────

class TestGradeMatchScoreBonus:
    def test_exact_score_awards_bonus(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="group", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=2, away_score_pred=1)
        _finish_match(mid, 2, 1, "home")
        results = grade_match(mid)
        r = results[0]
        assert r["score_bonus"] == 3

    def test_wrong_score_no_bonus(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="group", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=3, away_score_pred=0)
        _finish_match(mid, 2, 1, "home")
        results = grade_match(mid)
        assert results[0]["score_bonus"] == 0

    def test_no_score_entered_no_bonus(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="group", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home")   # no score numbers
        _finish_match(mid, 2, 1, "home")
        results = grade_match(mid)
        assert results[0]["score_bonus"] == 0

    def test_before_cutoff_no_score_bonus(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="group", kickoff=EARLY_KO)
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=2, away_score_pred=1)
        _finish_match(mid, 2, 1, "home")
        results = grade_match(mid)
        # score_bonus should be None (feature not active)
        assert results[0]["score_bonus"] is None

    def test_score_bonus_correct_even_when_winner_wrong(self, fresh_db):
        """Exact score means both teams' goals are right — winner bonus is separate."""
        uid = make_user(111, "Kevin")
        mid = make_match(stage="group", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "draw",
                             home_score_pred=1, away_score_pred=1)
        _finish_match(mid, 1, 1, "draw")
        results = grade_match(mid)
        r = results[0]
        assert r["correct"] is True    # draw was correct
        assert r["score_bonus"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# grade_match — ET bonus
# ─────────────────────────────────────────────────────────────────────────────

class TestGradeMatchEtBonus:
    def test_et_correct_awards_bonus(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=2, away_score_pred=1,
                             predicted_et=1, predicted_pens=0)
        _finish_match(mid, 2, 1, "home", went_to_pens=False, went_to_et=True)
        results = grade_match(mid)
        r = results[0]
        assert r["et_bonus"] == 1

    def test_et_wrong_no_bonus(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home", predicted_et=0, predicted_pens=None)
        _finish_match(mid, 2, 1, "home", went_to_pens=False, went_to_et=True)
        results = grade_match(mid)
        assert results[0]["et_bonus"] == 0

    def test_et_correct_no_bonus(self, fresh_db):
        """User said No ET, match didn't go to ET — correct, +1."""
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home", predicted_et=0, predicted_pens=None)
        _finish_match(mid, 2, 1, "home", went_to_pens=False, went_to_et=False)
        results = grade_match(mid)
        assert results[0]["et_bonus"] == 1

    def test_et_not_asked_for_group_matches(self, fresh_db):
        """Group stage: went_to_et is None → ET block doesn't run."""
        uid = make_user(111, "Kevin")
        mid = make_match(stage="group", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home")
        _finish_match(mid, 1, 0, "home", went_to_pens=None, went_to_et=None)
        results = grade_match(mid)
        assert "et_bonus" not in results[0]


# ─────────────────────────────────────────────────────────────────────────────
# grade_match — pens bonus
# ─────────────────────────────────────────────────────────────────────────────

class TestGradeMatchPensBonus:
    def test_pens_correct_awards_bonus(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=1, away_score_pred=1,
                             predicted_et=1, predicted_pens=1)
        _finish_match(mid, 1, 1, "home", went_to_pens=True, went_to_et=True)
        results = grade_match(mid)
        r = results[0]
        assert r["pens_bonus"] == 1

    def test_pens_wrong_no_bonus(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home",
                             predicted_et=1, predicted_pens=0)   # said no pens
        _finish_match(mid, 1, 1, "home", went_to_pens=True, went_to_et=True)
        results = grade_match(mid)
        assert results[0]["pens_bonus"] == 0

    def test_et_no_means_pens_null_in_result(self, fresh_db):
        """User said No ET → implicit Pens=No. Match didn't go to pens → pens bonus ✅."""
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home",
                             predicted_et=0, predicted_pens=None)
        _finish_match(mid, 2, 1, "home", went_to_pens=False, went_to_et=True)
        results = grade_match(mid)
        r = results[0]
        assert r["pens_pred"] == 0     # implicit No (ET=No → Pens=No)
        assert r["pens_bonus"] == 1    # correct — match didn't go to pens

    def test_all_bonuses_max_scenario(self, fresh_db):
        """
        Winner ✅ + Exact score ✅ + ET ✅ + Pens ✅ = 2 + 3 + 1 + 1 = 7 (round_of_32)
        """
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "home",
                             home_score_pred=1, away_score_pred=1,
                             predicted_et=1, predicted_pens=1)
        _finish_match(mid, 1, 1, "home", went_to_pens=True, went_to_et=True)
        results = grade_match(mid)
        r = results[0]
        total = r["points"] + r["score_bonus"] + r["et_bonus"] + r["pens_bonus"]
        assert total == 7   # 2 + 3 + 1 + 1

    def test_all_wrong_scenario(self, fresh_db):
        """Wrong winner, wrong score, wrong ET, wrong pens = 0 total (no negatives for bonuses)."""
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32", kickoff=FUTURE_KO)
        db.upsert_prediction(uid, mid, "away",
                             home_score_pred=3, away_score_pred=2,
                             predicted_et=0, predicted_pens=None)
        _finish_match(mid, 1, 1, "home", went_to_pens=True, went_to_et=True)
        results = grade_match(mid)
        r = results[0]
        assert r["points"] == 0
        assert r["score_bonus"] == 0
        assert r["et_bonus"] == 0
        assert r["pens_bonus"] == 0
        total = r["points"] + r["score_bonus"] + r["et_bonus"] + r["pens_bonus"]
        assert total == 0


# ─────────────────────────────────────────────────────────────────────────────
# grade_match — AET winner from API, not score comparison
# ─────────────────────────────────────────────────────────────────────────────

class TestGradeMatchAetWinner:
    def test_aet_home_win_1_1_score(self, fresh_db):
        """
        1-1 at 90 min → AET → home wins. User who picked 'home' must score points.
        If we used score comparison, 1-1 would give 'draw'. We must use stored winner.
        """
        uid = make_user(111, "Kevin")
        mid = make_match(stage="round_of_32", kickoff=EARLY_KO)
        db.upsert_prediction(uid, mid, "home")
        _finish_match(mid, 1, 1, "home", went_to_pens=False, went_to_et=True)
        results = grade_match(mid)
        r = results[0]
        assert r["correct"] is True
        assert r["points"] == 2   # round_of_32

    def test_pens_away_win_0_0_score(self, fresh_db):
        """0-0 → pens → away wins. Score-comparison would say 'draw', must use winner."""
        u1 = make_user(111, "Kevin")
        u2 = make_user(222, "Mathavi")
        mid = make_match(stage="quarter_final", kickoff=EARLY_KO)
        db.upsert_prediction(u1, mid, "away")   # correct
        db.upsert_prediction(u2, mid, "home")   # wrong
        _finish_match(mid, 0, 0, "away", went_to_pens=True, went_to_et=True)
        results = {r["name"]: r for r in grade_match(mid)}
        assert results["Kevin"]["correct"] is True
        assert results["Kevin"]["points"] == 5    # quarter_final
        assert results["Mathavi"]["correct"] is False
        assert results["Mathavi"]["points"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# grade_match — idempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestGradeMatchIdempotency:
    def test_second_grade_returns_empty(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="group", kickoff=EARLY_KO)
        db.upsert_prediction(uid, mid, "home")
        _finish_match(mid, 1, 0, "home")
        grade_match(mid)
        results2 = grade_match(mid)
        assert results2 == []

    def test_scores_not_doubled_on_double_grade(self, fresh_db):
        uid = make_user(111, "Kevin")
        mid = make_match(stage="group", kickoff=EARLY_KO)
        db.upsert_prediction(uid, mid, "home")
        _finish_match(mid, 1, 0, "home")
        grade_match(mid)
        grade_match(mid)   # should be a no-op
        s = db.get_scores()[0]
        assert s["total_points"] == 1   # group correct = 1 pt, NOT 2


# ─────────────────────────────────────────────────────────────────────────────
# grade_match — two users, mixed outcomes
# ─────────────────────────────────────────────────────────────────────────────

class TestGradeMatchTwoUsers:
    def test_one_right_one_wrong(self, fresh_db):
        u1 = make_user(111, "Kevin")
        u2 = make_user(222, "Mathavi")
        mid = make_match(stage="group", kickoff=EARLY_KO)
        db.upsert_prediction(u1, mid, "home")
        db.upsert_prediction(u2, mid, "away")
        _finish_match(mid, 2, 0, "home")
        results = {r["name"]: r for r in grade_match(mid)}
        assert results["Kevin"]["correct"] is True
        assert results["Kevin"]["points"] == 1
        assert results["Mathavi"]["correct"] is False
        assert results["Mathavi"]["points"] == 0

    def test_one_right_one_missed(self, fresh_db):
        u1 = make_user(111, "Kevin")
        u2 = make_user(222, "Mathavi")
        mid = make_match(stage="round_of_16", kickoff=EARLY_KO)
        db.upsert_prediction(u1, mid, "away")
        # u2 makes no prediction
        _finish_match(mid, 0, 1, "away")
        results = {r["name"]: r for r in grade_match(mid)}
        assert results["Kevin"]["correct"] is True
        assert results["Kevin"]["points"] == 3
        assert results["Mathavi"]["missed"] is True
        assert results["Mathavi"]["points"] == -3

    def test_leaderboard_order_after_grade(self, fresh_db):
        u1 = make_user(111, "Kevin")
        u2 = make_user(222, "Mathavi")
        mid = make_match(stage="final", kickoff=EARLY_KO)
        db.upsert_prediction(u1, mid, "home")
        db.upsert_prediction(u2, mid, "away")
        _finish_match(mid, 1, 0, "home")
        grade_match(mid)
        scores = db.get_scores()
        assert scores[0]["name"] == "Kevin"    # 15 pts — first
        assert scores[1]["name"] == "Mathavi"  # 0 pts — second
