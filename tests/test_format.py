"""
Regression tests — bot/handlers.py format_player_block()

Tests every combination of:
  • Regular time (group / knockout)
  • AET only (no pens)
  • AET + penalties
  • ET=No (pens skipped → N/A)
  • Missed prediction
  • Wrong winner + right score (score bonus independent)
  • format_leaderboard output
"""
import os
import sys
import types

# ── Minimal stubs so we can import bot.handlers without a real Telegram bot ──
os.environ.setdefault("TELEGRAM_BOT_TOKEN",  "test:token")
os.environ.setdefault("TELEGRAM_GROUP_ID",   "-100000001")
os.environ.setdefault("ADMIN_TELEGRAM_ID",   "111")
os.environ.setdefault("USER_2_TELEGRAM_ID",  "222")
os.environ.setdefault("DATABASE_PATH",       ":memory:")

# Stub telegram
tg = types.ModuleType("telegram")
class _Btn:
    def __init__(self, text, callback_data=None): self.text = text; self.callback_data = callback_data
class _Mkp:
    def __init__(self, kb): self.inline_keyboard = kb
tg.InlineKeyboardButton = _Btn
tg.InlineKeyboardMarkup = _Mkp
tg.Update = object
tg.Bot   = object
sys.modules["telegram"] = tg

for sub in ["telegram.ext", "telegram.constants"]:
    m = types.ModuleType(sub)
    m.ParseMode = types.SimpleNamespace(MARKDOWN="Markdown")
    m.Application = object
    m.CallbackQueryHandler = object
    m.CommandHandler = object
    m.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
    m.MessageHandler = object
    m.filters = types.SimpleNamespace(TEXT=None, COMMAND=None)
    sys.modules[sub] = m

import pytest
from bot.handlers import format_player_block, format_leaderboard


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _match(stage="group"):
    """Return a minimal fake match dict."""
    return {"stage": stage}


def _result(
    name="Kevin",
    prediction_display="Brazil",
    correct=True,
    missed=False,
    points=1,
    score_bonus=None,
    score_pred=None,
    et_bonus=None,
    et_pred=None,
    pens_bonus=None,
    pens_pred=None,
):
    r = dict(
        name=name,
        prediction_display=prediction_display,
        correct=correct,
        missed=missed,
        points=points,
    )
    if score_bonus is not None: r["score_bonus"] = score_bonus
    if score_pred  is not None: r["score_pred"]  = score_pred
    if et_bonus    is not None: r["et_bonus"]    = et_bonus
    if et_pred     is not None: r["et_pred"]     = et_pred
    if pens_bonus  is not None: r["pens_bonus"]  = pens_bonus
    if pens_pred   is not None: r["pens_pred"]   = pens_pred
    return r


# ─────────────────────────────────────────────────────────────────────────────
# format_player_block — group stage (no ET/pens lines)
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupStageBlock:
    def test_correct_winner_no_score(self):
        r = _result(correct=True, points=1, score_bonus=None)
        block = format_player_block(r, _match("group"))
        assert "*Kevin*" in block
        assert "Brazil = +1 pts ✅" in block
        assert "ET" not in block
        assert "Pens" not in block
        assert "Total pts from this match: *+1*" in block

    def test_wrong_winner(self):
        r = _result(correct=False, missed=False, points=0,
                    prediction_display="Argentina", score_bonus=None)
        block = format_player_block(r, _match("group"))
        assert "Argentina = 0 pts ❌" in block
        assert "Total pts from this match: *0*" in block

    def test_missed(self):
        r = _result(correct=False, missed=True, points=-1, score_bonus=None)
        block = format_player_block(r, _match("group"))
        assert "No prediction = -1 pts ❌" in block

    def test_correct_with_score_bonus(self):
        r = _result(correct=True, points=1,
                    score_bonus=3, score_pred="2–1")
        block = format_player_block(r, _match("group"))
        assert "Score — 2–1 = +3 pts ✅" in block
        assert "Total pts from this match: *+4*" in block

    def test_wrong_score_bonus(self):
        r = _result(correct=True, points=1,
                    score_bonus=0, score_pred="3–0")
        block = format_player_block(r, _match("group"))
        assert "Score — 3–0 = 0 pts ❌" in block
        assert "Total pts from this match: *+1*" in block


# ─────────────────────────────────────────────────────────────────────────────
# format_player_block — knockout, regular time (90 min)
# ─────────────────────────────────────────────────────────────────────────────

class TestKnockoutRegularBlock:
    def test_correct_winner_et_no_pens_not_graded(self):
        """ET=No predicted, pens grading not active for this match (pre-feature) → N/A."""
        r = _result(correct=True, points=2,
                    score_bonus=3, score_pred="2–0",
                    et_bonus=1, et_pred=0,
                    pens_bonus=None)   # pens not graded — pre-feature match
        block = format_player_block(r, _match("round_of_32"))
        assert "⏱ ET — No = +1 pt ✅" in block
        assert "🥅 Pens = N/A" in block   # pens_bonus None → N/A
        assert "Total pts from this match: *+6*" in block

    def test_et_no_implicit_pens_correct(self):
        """ET=No predicted, match ended in 90 min — implicit Pens=No is ✅."""
        r = _result(correct=True, points=2,
                    score_bonus=3, score_pred="1–2",
                    et_bonus=1, et_pred=0,
                    pens_bonus=1, pens_pred=0)   # implicit No pens, graded correct
        block = format_player_block(r, _match("round_of_32"))
        assert "⏱ ET — No = +1 pt ✅" in block
        assert "🥅 Pens — No = +1 pt ✅" in block
        assert "Total pts from this match: *+7*" in block

    def test_et_no_implicit_pens_wrong(self):
        """ET=No predicted but match went to pens → ET ❌ and implicit Pens=No also ❌."""
        r = _result(correct=True, points=2,
                    score_bonus=0, score_pred="1–0",
                    et_bonus=0, et_pred=0,    # said No ET but ET happened
                    pens_bonus=0, pens_pred=0)  # implicit No pens, wrong (pens happened)
        block = format_player_block(r, _match("round_of_32"))
        assert "⏱ ET — No = 0 pts ❌" in block
        assert "🥅 Pens — No = 0 pts ❌" in block

    def test_wrong_et_prediction_regular_time(self):
        """User said Yes ET but match finished 90 min → et wrong."""
        r = _result(correct=True, points=2,
                    score_bonus=0, score_pred="2–0",
                    et_bonus=0, et_pred=1,    # said Yes but wrong
                    pens_bonus=0, pens_pred=0)
        block = format_player_block(r, _match("round_of_32"))
        assert "⏱ ET — Yes = 0 pts ❌" in block
        # Pens was answered (ET=Yes) so it shows even though ET=1
        assert "🥅 Pens — No = 0 pts ❌" in block


# ─────────────────────────────────────────────────────────────────────────────
# format_player_block — AET, no pens
# ─────────────────────────────────────────────────────────────────────────────

class TestKnockoutAetBlock:
    def test_correct_et_no_pens(self):
        r = _result(correct=True, points=2,
                    score_bonus=3, score_pred="2–1",
                    et_bonus=1, et_pred=1,
                    pens_bonus=0, pens_pred=0)   # said No pens (correct: no pens)
        block = format_player_block(r, _match("round_of_32"))
        assert "⏱ ET — Yes = +1 pt ✅" in block
        assert "🥅 Pens — No = 0 pts ❌" in block   # wrong (match did go pens? no, pens=0)
        total = 2 + 3 + 1 + 0
        assert f"Total pts from this match: *+{total}*" in block

    def test_correct_et_correct_pens_no(self):
        """ET yes correct, Pens no correct (match did NOT go to pens)."""
        r = _result(correct=True, points=3,
                    score_bonus=0, score_pred="1–0",
                    et_bonus=1, et_pred=1,
                    pens_bonus=1, pens_pred=0)   # said No pens, no pens happened ✅
        block = format_player_block(r, _match("round_of_16"))
        assert "⏱ ET — Yes = +1 pt ✅" in block
        assert "🥅 Pens — No = +1 pt ✅" in block
        assert "Total pts from this match: *+5*" in block


# ─────────────────────────────────────────────────────────────────────────────
# format_player_block — penalty shootout scenario
# ─────────────────────────────────────────────────────────────────────────────

class TestKnockoutPensBlock:
    def test_all_correct(self):
        """Kevin: picked right winner, exact score, ET=Yes, Pens=Yes → max points."""
        r = _result(name="Kevin", prediction_display="Germany",
                    correct=True, points=2,
                    score_bonus=3, score_pred="1–1",
                    et_bonus=1, et_pred=1,
                    pens_bonus=1, pens_pred=1)
        block = format_player_block(r, _match("round_of_32"))
        assert "*Kevin*" in block
        assert "Germany = +2 pts ✅" in block
        assert "Score — 1–1 = +3 pts ✅" in block
        assert "⏱ ET — Yes = +1 pt ✅" in block
        assert "🥅 Pens — Yes = +1 pt ✅" in block
        assert "Total pts from this match: *+7*" in block

    def test_all_wrong(self):
        """Mathavi: wrong winner, wrong score, ET=No (wrong), Pens N/A."""
        r = _result(name="Mathavi", prediction_display="Paraguay",
                    correct=False, missed=False, points=0,
                    score_bonus=0, score_pred="0–1",
                    et_bonus=0, et_pred=0,
                    pens_bonus=None)   # pens_bonus not set (et_pred=0 → N/A)
        block = format_player_block(r, _match("round_of_32"))
        assert "*Mathavi*" in block
        assert "Paraguay = 0 pts ❌" in block
        assert "Score — 0–1 = 0 pts ❌" in block
        assert "⏱ ET — No = 0 pts ❌" in block
        assert "🥅 Pens = N/A" in block
        assert "Total pts from this match: *0*" in block

    def test_et_yes_pens_wrong(self):
        """Got ET right, Pens wrong."""
        r = _result(correct=True, points=5,
                    score_bonus=0, score_pred="2–2",
                    et_bonus=1, et_pred=1,
                    pens_bonus=0, pens_pred=0)   # said No pens, match went pens
        block = format_player_block(r, _match("quarter_final"))
        assert "⏱ ET — Yes = +1 pt ✅" in block
        assert "🥅 Pens — No = 0 pts ❌" in block
        assert "Total pts from this match: *+6*" in block


# ─────────────────────────────────────────────────────────────────────────────
# format_player_block — structural checks
# ─────────────────────────────────────────────────────────────────────────────

class TestBlockStructure:
    def test_starts_with_bold_name(self):
        r = _result(name="Kevin", correct=True, points=1)
        block = format_player_block(r, _match())
        assert block.startswith("*Kevin*")

    def test_has_two_dividers(self):
        r = _result(correct=True, points=1)
        block = format_player_block(r, _match())
        assert block.count("---") >= 2

    def test_total_line_always_present(self):
        r = _result(correct=False, missed=True, points=-1)
        block = format_player_block(r, _match())
        assert "Total pts from this match:" in block

    def test_positive_total_has_plus(self):
        r = _result(correct=True, points=5, score_bonus=3, score_pred="2–1")
        block = format_player_block(r, _match())
        assert "Total pts from this match: *+8*" in block

    def test_zero_total_no_plus(self):
        r = _result(correct=False, missed=False, points=0)
        block = format_player_block(r, _match())
        assert "Total pts from this match: *0*" in block

    def test_negative_total_no_plus(self):
        r = _result(correct=False, missed=True, points=-3)
        block = format_player_block(r, _match("semi_final"))
        assert "Total pts from this match: *-3*" in block


# ─────────────────────────────────────────────────────────────────────────────
# format_leaderboard
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatLeaderboard:
    def _fake_score(self, name, pts):
        class Row(dict):
            def __getitem__(self, k): return super().__getitem__(k)
        return Row(
            name=name,
            total_points=pts,
            correct_predictions=0,
            wrong_predictions=0,
            missed_predictions=0,
            total_graded=0,
            current_streak=0,
            best_streak=0,
            score_bonus_count=0,
        )

    def test_empty_leaderboard(self):
        out = format_leaderboard([])
        assert "No scores" in out

    def test_single_player(self):
        out = format_leaderboard([self._fake_score("Kevin", 10)])
        assert "Kevin" in out
        assert "10" in out

    def test_ordering_preserved(self):
        """Leaderboard receives pre-sorted scores from DB; format preserves order."""
        scores = [self._fake_score("Kevin", 15), self._fake_score("Mathavi", 7)]
        out = format_leaderboard(scores)
        kevin_pos  = out.index("Kevin")
        math_pos   = out.index("Mathavi")
        assert kevin_pos < math_pos
