"""
Inline keyboard builders for the prediction flow.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.flags import team_label
from config import KNOCKOUT_STAGES


def match_list_keyboard(matches: list) -> InlineKeyboardMarkup:
    """One button per upcoming match, with country flags."""
    buttons = [
        [InlineKeyboardButton(
            f"{team_label(m['home_team'])} vs {team_label(m['away_team'])}",
            callback_data=f"pred:match:{m['id']}",
        )]
        for m in matches
    ]
    return InlineKeyboardMarkup(buttons)


def prediction_choice_keyboard(
    match_id: int, home_team: str, away_team: str, stage: str = "group"
) -> InlineKeyboardMarkup:
    """
    Prediction buttons with country flags.
    Group stage:   [🇧🇷 Brazil]  [🤝 Draw]  [🇦🇷 Argentina]
    Knockout:      [🇧🇷 Brazil]  [🇦🇷 Argentina]   (no Draw)
    """
    buttons = [
        InlineKeyboardButton(team_label(home_team), callback_data=f"pred:pick:{match_id}:home"),
    ]
    if stage not in KNOCKOUT_STAGES:
        buttons.append(
            InlineKeyboardButton("🤝 Draw", callback_data=f"pred:pick:{match_id}:draw")
        )
    buttons.append(
        InlineKeyboardButton(team_label(away_team), callback_data=f"pred:pick:{match_id}:away")
    )
    return InlineKeyboardMarkup([buttons])


def home_score_keyboard(match_id: int, winner: str) -> InlineKeyboardMarkup:
    """
    Number picker (0–9) for the home team's goal tally.
    Callback: pred:hscore:<match_id>:<winner>:<home_score>
    """
    row1 = [
        InlineKeyboardButton(str(i), callback_data=f"pred:hscore:{match_id}:{winner}:{i}")
        for i in range(5)
    ]
    row2 = [
        InlineKeyboardButton(str(i), callback_data=f"pred:hscore:{match_id}:{winner}:{i}")
        for i in range(5, 10)
    ]
    return InlineKeyboardMarkup([row1, row2])


def away_score_keyboard(match_id: int, winner: str, home_score: int) -> InlineKeyboardMarkup:
    """
    Number picker (0–9) for the away team's goal tally.
    Callback: pred:ascore:<match_id>:<winner>:<home_score>:<away_score>
    """
    row1 = [
        InlineKeyboardButton(
            str(i), callback_data=f"pred:ascore:{match_id}:{winner}:{home_score}:{i}"
        )
        for i in range(5)
    ]
    row2 = [
        InlineKeyboardButton(
            str(i), callback_data=f"pred:ascore:{match_id}:{winner}:{home_score}:{i}"
        )
        for i in range(5, 10)
    ]
    return InlineKeyboardMarkup([row1, row2])
