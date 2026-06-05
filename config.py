import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_GROUP_ID: int = int(os.environ["TELEGRAM_GROUP_ID"])
ADMIN_TELEGRAM_ID: int = int(os.environ["ADMIN_TELEGRAM_ID"])
USER_2_TELEGRAM_ID: int = int(os.environ["USER_2_TELEGRAM_ID"])

# Names are optional — players set their own name when they send /start or /setname.
# These are only used as fallback placeholders until they do.
ADMIN_NAME: str = os.getenv("ADMIN_NAME", "Player 1")
USER_2_NAME: str = os.getenv("USER_2_NAME", "Player 2")

# ── APIs ──────────────────────────────────────────────────────────────────────
# football-data.org — free tier, no daily cap, register at football-data.org
FOOTBALL_DATA_KEY: str = os.getenv("FOOTBALL_DATA_KEY", "")

# AI provider — set at least one of these.
# Priority: OPENAI > GROK > GEMINI
# OpenAI:  gpt-4o-mini by default (cheap + fast)
# Grok:    grok-3-mini-fast via xAI OpenAI-compatible API
# Gemini:  gemini-2.0-flash (free tier has strict limits)
OPENAI_API_KEY: str  = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GROK_API_KEY: str    = os.getenv("GROK_API_KEY", "")
GROK_MODEL: str      = os.getenv("GROK_MODEL", "grok-3-mini-fast")
GEMINI_API_KEY: str  = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str    = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "predicarena.db")

# ── Webhook (production) ──────────────────────────────────────────────────────
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "predicarena_secret")

# ── World Cup 2026 ────────────────────────────────────────────────────────────
WC_COMPETITION_CODE: str = "WC"   # football-data.org competition code
WC_SEASON: int = 2026

# ── Scoring ───────────────────────────────────────────────────────────────────
# Points awarded for a CORRECT prediction at each stage
STAGE_POINTS: dict[str, int] = {
    "group":        1,
    "round_of_32":  2,
    "round_of_16":  3,
    "quarter_final": 5,
    "semi_final":   8,
    "final":        15,
}

# Points deducted for a WRONG or MISSED prediction (negative values)
# QF/SF/Final are capped at -3
STAGE_PENALTIES: dict[str, int] = {
    "group":        -1,
    "round_of_32":  -2,
    "round_of_16":  -3,
    "quarter_final": -3,
    "semi_final":   -3,
    "final":        -3,
}

STAGE_LABELS: dict[str, str] = {
    "group":        "Group Stage",
    "round_of_32":  "Round of 32",
    "round_of_16":  "Round of 16",
    "quarter_final": "Quarter-Final",
    "semi_final":   "Semi-Final",
    "final":        "Final",
}

VALID_STAGES = list(STAGE_POINTS.keys())

# Knockout stages where a draw is impossible — must have a winner (AET/pens if needed)
KNOCKOUT_STAGES = {"round_of_32", "round_of_16", "quarter_final", "semi_final", "final"}
