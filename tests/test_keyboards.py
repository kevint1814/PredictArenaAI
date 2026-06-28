"""
Unit tests — bot/keyboards.py
Tests callback data formats, payload integrity, and Draw suppression in knockouts.
No Telegram network calls needed — just inspect the button objects.
"""
import pytest
from unittest.mock import MagicMock, patch


# Stub out telegram imports so we don't need a running bot
import sys
from unittest.mock import MagicMock as MM

# Minimal stubs for telegram classes
class FakeButton:
    def __init__(self, text, callback_data=None):
        self.text = text
        self.callback_data = callback_data

class FakeMarkup:
    def __init__(self, keyboard):
        self.inline_keyboard = keyboard  # list of list of FakeButton

sys.modules.setdefault("telegram", MM())
sys.modules["telegram"].InlineKeyboardButton = FakeButton
sys.modules["telegram"].InlineKeyboardMarkup = FakeMarkup
# Ensure sub-module stubs don't clash
import types
tg_mod = types.ModuleType("telegram")
tg_mod.InlineKeyboardButton = FakeButton
tg_mod.InlineKeyboardMarkup = FakeMarkup
sys.modules["telegram"] = tg_mod

import bot.keyboards as kb


# ─────────────────────────────────────────────────────────────────────────────
# prediction_choice_keyboard — Draw visibility
# ─────────────────────────────────────────────────────────────────────────────

class TestPredictionChoiceKeyboard:
    def _buttons(self, stage):
        markup = kb.prediction_choice_keyboard(1, "Brazil", "Argentina", stage)
        return markup.inline_keyboard[0]   # single row

    def test_group_stage_has_draw(self):
        buttons = self._buttons("group")
        texts = [b.text for b in buttons]
        assert any("Draw" in t for t in texts)
        assert len(buttons) == 3

    def test_round_of_32_no_draw(self):
        buttons = self._buttons("round_of_32")
        texts = [b.text for b in buttons]
        assert not any("Draw" in t for t in texts)
        assert len(buttons) == 2

    def test_round_of_16_no_draw(self):
        buttons = self._buttons("round_of_16")
        assert len(buttons) == 2

    def test_quarter_final_no_draw(self):
        buttons = self._buttons("quarter_final")
        assert len(buttons) == 2

    def test_semi_final_no_draw(self):
        buttons = self._buttons("semi_final")
        assert len(buttons) == 2

    def test_final_no_draw(self):
        buttons = self._buttons("final")
        assert len(buttons) == 2

    def test_home_callback_format(self):
        buttons = self._buttons("group")
        home_btn = next(b for b in buttons if "Brazil" in b.text)
        assert home_btn.callback_data == "pred:pick:1:home"

    def test_draw_callback_format(self):
        buttons = self._buttons("group")
        draw_btn = next(b for b in buttons if "Draw" in b.text)
        assert draw_btn.callback_data == "pred:pick:1:draw"

    def test_away_callback_format(self):
        buttons = self._buttons("group")
        away_btn = next(b for b in buttons if "Argentina" in b.text)
        assert away_btn.callback_data == "pred:pick:1:away"


# ─────────────────────────────────────────────────────────────────────────────
# home_score_keyboard / away_score_keyboard
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreKeyboards:
    def test_home_score_has_10_buttons(self):
        markup = kb.home_score_keyboard(5, "home")
        all_buttons = [b for row in markup.inline_keyboard for b in row]
        assert len(all_buttons) == 10

    def test_home_score_range_0_to_9(self):
        markup = kb.home_score_keyboard(5, "home")
        values = [int(b.text) for row in markup.inline_keyboard for b in row]
        assert sorted(values) == list(range(10))

    def test_home_score_callback_format(self):
        markup = kb.home_score_keyboard(5, "home")
        btn = markup.inline_keyboard[0][0]   # first button = 0
        assert btn.callback_data == "pred:hscore:5:home:0"

    def test_away_score_carries_home_score(self):
        markup = kb.away_score_keyboard(5, "away", 2)
        btn = markup.inline_keyboard[0][0]
        assert btn.callback_data == "pred:ascore:5:away:2:0"

    def test_away_score_has_10_buttons(self):
        markup = kb.away_score_keyboard(5, "away", 2)
        all_buttons = [b for row in markup.inline_keyboard for b in row]
        assert len(all_buttons) == 10


# ─────────────────────────────────────────────────────────────────────────────
# et_keyboard
# ─────────────────────────────────────────────────────────────────────────────

class TestEtKeyboard:
    def test_has_two_buttons(self):
        markup = kb.et_keyboard(7, "home", 1, 0)
        buttons = markup.inline_keyboard[0]
        assert len(buttons) == 2

    def test_no_callback_data(self):
        markup = kb.et_keyboard(7, "home", 1, 0)
        buttons = markup.inline_keyboard[0]
        no_btn = next(b for b in buttons if "No" in b.text)
        assert no_btn.callback_data == "pred:et:7:home:1:0:0"

    def test_yes_callback_data(self):
        markup = kb.et_keyboard(7, "home", 1, 0)
        buttons = markup.inline_keyboard[0]
        yes_btn = next(b for b in buttons if "Yes" in b.text)
        assert yes_btn.callback_data == "pred:et:7:home:1:0:1"


# ─────────────────────────────────────────────────────────────────────────────
# pens_keyboard — carries predicted_et in payload
# ─────────────────────────────────────────────────────────────────────────────

class TestPensKeyboard:
    def test_has_two_buttons(self):
        markup = kb.pens_keyboard(7, "home", 2, 1, predicted_et=1)
        buttons = markup.inline_keyboard[0]
        assert len(buttons) == 2

    def test_no_callback_carries_et(self):
        markup = kb.pens_keyboard(7, "home", 2, 1, predicted_et=1)
        buttons = markup.inline_keyboard[0]
        no_btn = next(b for b in buttons if "No" in b.text)
        assert no_btn.callback_data == "pred:pens:7:home:2:1:1:0"

    def test_yes_callback_carries_et(self):
        markup = kb.pens_keyboard(7, "home", 2, 1, predicted_et=1)
        buttons = markup.inline_keyboard[0]
        yes_btn = next(b for b in buttons if "Yes" in b.text)
        assert yes_btn.callback_data == "pred:pens:7:home:2:1:1:1"

    def test_callback_parts_count(self):
        """pred:pens has exactly 8 colon-separated parts."""
        markup = kb.pens_keyboard(10, "away", 0, 0, predicted_et=1)
        btn = markup.inline_keyboard[0][0]
        parts = btn.callback_data.split(":")
        assert len(parts) == 8

    def test_et_keyboard_callback_parts_count(self):
        """pred:et has exactly 7 colon-separated parts."""
        markup = kb.et_keyboard(10, "away", 0, 0)
        btn = markup.inline_keyboard[0][0]
        parts = btn.callback_data.split(":")
        assert len(parts) == 7
