"""
football-data.org v4 client — free tier, no daily cap.

Register at https://www.football-data.org/client/register
Copy your API token into .env as FOOTBALL_DATA_KEY.

Free tier covers the FIFA World Cup (competition code: WC).

Used only for:
  • Syncing upcoming match fixtures (/syncmatches)
  • Optional: fetching a specific match result (/fetchresult)

All live monitoring has been removed — results are entered manually
via /setresult after each match.
"""

import logging
from typing import Optional

import requests

from config import FOOTBALL_DATA_KEY, WC_COMPETITION_CODE, WC_SEASON

logger = logging.getLogger(__name__)

_BASE    = "https://api.football-data.org/v4"
_HEADERS = {"X-Auth-Token": FOOTBALL_DATA_KEY}

# Map football-data.org stage strings → our internal stage keys
# football-data.org uses LAST_32 / LAST_16 (not ROUND_OF_32 / ROUND_OF_16)
_STAGE_MAP = {
    "GROUP_STAGE":    "group",
    "LAST_32":        "round_of_32",   # API value for Round of 32
    "LAST_16":        "round_of_16",   # API value for Round of 16
    "QUARTER_FINALS": "quarter_final",
    "SEMI_FINALS":    "semi_final",
    "FINAL":          "final",
    "THIRD_PLACE":    "semi_final",    # treat 3rd-place playoff same as semi points
}

# Map football-data.org winner values → our internal winner keys
_WINNER_MAP = {
    "HOME_TEAM": "home",
    "AWAY_TEAM": "away",
    "DRAW":      "draw",
}


def _get(path: str, params: dict = None) -> dict:
    resp = requests.get(f"{_BASE}/{path}", headers=_HEADERS, params=params or {}, timeout=10)

    # Respect rate-limit headers as requested by football-data.org
    # Free tier: 10 calls/minute. Headers tell us exactly what's left.
    available = resp.headers.get("X-Requests-Available-Minute")
    reset_in  = resp.headers.get("X-RequestCounter-Reset")
    if available is not None:
        available = int(available)
        logger.debug("football-data.org: %d requests remaining this minute", available)
        if available == 0:
            wait = int(reset_in) + 1 if reset_in else 61
            logger.warning(
                "Rate limit reached — sleeping %ds before continuing", wait
            )
            import time
            time.sleep(wait)

    resp.raise_for_status()
    return resp.json()


def get_upcoming_matches(competition: str = WC_COMPETITION_CODE, season: int = WC_SEASON) -> list[dict]:
    """
    Return all scheduled/timed WC matches from football-data.org.
    Each dict: {id, home, away, kickoff_utc (ISO string), stage}
    """
    if not FOOTBALL_DATA_KEY:
        logger.warning("FOOTBALL_DATA_KEY not set — cannot sync fixtures")
        return []

    data    = _get(f"competitions/{competition}/matches", {"season": season, "status": "SCHEDULED,TIMED"})
    matches = data.get("matches", [])
    result  = []

    for m in matches:
        raw_stage = m.get("stage", "GROUP_STAGE")
        stage     = _STAGE_MAP.get(raw_stage, "group")
        result.append({
            "id":          m["id"],
            "home":        m["homeTeam"]["name"],
            "away":        m["awayTeam"]["name"],
            "kickoff_utc": m["utcDate"],    # already ISO-8601 UTC e.g. "2026-06-11T17:00:00Z"
            "stage":       stage,
        })

    return result


def get_all_wc_matches(competition: str = WC_COMPETITION_CODE, season: int = WC_SEASON) -> list[dict]:
    """
    Return ALL WC matches (any status) so we can back-fill stages for already-synced matches.
    Each dict: {id, home, away, kickoff_utc, stage}
    """
    if not FOOTBALL_DATA_KEY:
        logger.warning("FOOTBALL_DATA_KEY not set — cannot sync stages")
        return []

    data    = _get(f"competitions/{competition}/matches", {"season": season})
    matches = data.get("matches", [])
    result  = []

    for m in matches:
        raw_stage = m.get("stage", "GROUP_STAGE")
        stage     = _STAGE_MAP.get(raw_stage, "group")
        result.append({
            "id":          m["id"],
            "home":        m["homeTeam"]["name"],
            "away":        m["awayTeam"]["name"],
            "kickoff_utc": m["utcDate"],
            "stage":       stage,
        })

    return result


def get_live_score(api_match_id: int) -> Optional[dict]:
    """
    Fetch the current score of a live (in-progress) match directly from football-data.org.
    Returns {"home_score": int, "away_score": int} or None on failure.
    Note: free tier may have a 1–2 min delay on live updates.
    """
    if not FOOTBALL_DATA_KEY:
        return None
    try:
        data      = _get(f"matches/{api_match_id}")
        score_obj = data.get("score") or {}
        ft        = score_obj.get("fullTime") or {}
        home_s    = ft.get("home")
        away_s    = ft.get("away")
        return {
            "home_score": int(home_s) if home_s is not None else 0,
            "away_score": int(away_s) if away_s is not None else 0,
        }
    except Exception as exc:
        logger.warning("Could not fetch live score for match %d: %s", api_match_id, exc)
        return None


def get_match_result(match_id: int) -> Optional[dict]:
    """
    Poll football-data.org for a match's current status and score.

    Returns a dict if the call succeeds:
        {
            "finished":   bool,      # True only when status is FINISHED or AWARDED
            "home_score": int | None,
            "away_score": int | None,
        }
    Returns None on network/API error (caller should retry later).
    """
    if not FOOTBALL_DATA_KEY:
        return None

    try:
        data      = _get(f"matches/{match_id}")
        status      = data.get("status", "")
        finished    = status in ("FINISHED", "AWARDED")
        score_obj   = data.get("score") or {}
        ft          = score_obj.get("fullTime") or {}
        home_s      = ft.get("home")
        away_s      = ft.get("away")
        winner_raw  = score_obj.get("winner")   # "HOME_TEAM" | "AWAY_TEAM" | "DRAW" | None
        winner      = _WINNER_MAP.get(winner_raw) if winner_raw else None
        duration     = score_obj.get("duration", "REGULAR")   # "REGULAR" | "EXTRA_TIME" | "PENALTY_SHOOTOUT"
        went_to_pens = duration == "PENALTY_SHOOTOUT"
        went_to_et   = duration in ("EXTRA_TIME", "PENALTY_SHOOTOUT")
        return {
            "finished":     finished,
            "home_score":   int(home_s) if home_s is not None else None,
            "away_score":   int(away_s) if away_s is not None else None,
            "winner":       winner,        # 'home' | 'draw' | 'away' — authoritative for AET/pens
            "went_to_pens": went_to_pens,  # True only when match ended via penalty shootout
            "went_to_et":   went_to_et,    # True when match went to extra time (inc. pens)
        }
    except Exception as exc:
        logger.warning("Could not fetch result for match %d: %s", match_id, exc)
        return None
