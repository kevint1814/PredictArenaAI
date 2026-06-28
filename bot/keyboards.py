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


def et_keyboard(match_id: int, winner: str, home_score: int, away_score: int) -> InlineKeyboardMarkup:
    """
    Step 4 for knockout predictions — will this go to extra time?
    Callback: pred:et:<match_id>:<winner>:<home_score>:<away_score>:<0|1>
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⚽ No",  callback_data=f"pred:et:{match_id}:{winner}:{home_score}:{away_score}:0"),
        InlineKeyboardButton("⏱ Yes", callback_data=f"pred:et:{match_id}:{winner}:{home_score}:{away_score}:1"),
    ]])


def pens_keyboard(match_id: int, winner: str, home_score: int, away_score: int, predicted_et: int) -> InlineKeyboardMarkup:
    """
    Step 5 (conditional) for knockout predictions — will it go to penalties?
    Only shown when ET answer was Yes. Carries predicted_et forward in callback.
    Callback: pred:pens:<match_id>:<winner>:<home_score>:<away_score>:<predicted_et>:<0|1>
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⚽ No",  callback_data=f"pred:pens:{match_id}:{winner}:{home_score}:{away_score}:{predicted_et}:0"),
        InlineKeyboardButton("🥅 Yes", callback_data=f"pred:pens:{match_id}:{winner}:{home_score}:{away_score}:{predicted_et}:1"),
    ]])


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
