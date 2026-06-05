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
