"""
Research tools for Arena — production-grade web search + time lookups.

Search priority:
  1. Tavily   — real web search, AI-optimised, sourced results (TAVILY_API_KEY)
  2. DuckDuckGo — instant answers fallback (no key needed)

Time lookups use Python's built-in zoneinfo (no API needed).
"""

import logging
import requests
from datetime import datetime
from typing import Optional

from config import TAVILY_API_KEY

logger = logging.getLogger(__name__)


# ── Timezone lookups ───────────────────────────────────────────────────────────

_TZ_MAP: dict[str, str] = {
    "london": "Europe/London", "uk": "Europe/London", "england": "Europe/London",
    "paris": "Europe/Paris", "france": "Europe/Paris",
    "berlin": "Europe/Berlin", "germany": "Europe/Berlin",
    "madrid": "Europe/Madrid", "spain": "Europe/Madrid",
    "rome": "Europe/Rome", "italy": "Europe/Rome",
    "amsterdam": "Europe/Amsterdam", "netherlands": "Europe/Amsterdam",
    "lisbon": "Europe/Lisbon", "portugal": "Europe/Lisbon",
    "moscow": "Europe/Moscow", "russia": "Europe/Moscow",
    "istanbul": "Europe/Istanbul", "turkey": "Europe/Istanbul",
    "new york": "America/New_York", "est": "America/New_York", "eastern": "America/New_York",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles", "pst": "America/Los_Angeles",
    "chicago": "America/Chicago", "cst": "America/Chicago",
    "toronto": "America/Toronto", "canada": "America/Toronto",
    "sao paulo": "America/Sao_Paulo", "brazil": "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires", "argentina": "America/Argentina/Buenos_Aires",
    "mexico city": "America/Mexico_City", "mexico": "America/Mexico_City",
    "dubai": "Asia/Dubai", "uae": "Asia/Dubai",
    "mumbai": "Asia/Kolkata", "india": "Asia/Kolkata", "kolkata": "Asia/Kolkata", "ist": "Asia/Kolkata",
    "kuala lumpur": "Asia/Kuala_Lumpur", "malaysia": "Asia/Kuala_Lumpur", "kl": "Asia/Kuala_Lumpur",
    "singapore": "Asia/Singapore",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "seoul": "Asia/Seoul", "korea": "Asia/Seoul", "south korea": "Asia/Seoul",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "sydney": "Australia/Sydney", "australia": "Australia/Sydney",
    "auckland": "Pacific/Auckland", "new zealand": "Pacific/Auckland",
    "doha": "Asia/Qatar", "qatar": "Asia/Qatar",
    "riyadh": "Asia/Riyadh", "saudi arabia": "Asia/Riyadh",
    "cairo": "Africa/Cairo", "egypt": "Africa/Cairo",
    "lagos": "Africa/Lagos", "nigeria": "Africa/Lagos",
    "nairobi": "Africa/Nairobi", "kenya": "Africa/Nairobi",
    "johannesburg": "Africa/Johannesburg", "south africa": "Africa/Johannesburg",
    "casablanca": "Africa/Casablanca", "morocco": "Africa/Casablanca",
    "accra": "Africa/Accra", "ghana": "Africa/Accra",
    "utc": "UTC", "gmt": "UTC",
    # Common typos / shorthand for timezone queries
    "gbt": "Europe/London",         # typo for GMT — treat as British time (auto BST in summer)
    "bst": "Europe/London",         # British Summer Time
    "uk time": "Europe/London",
    "british time": "Europe/London",
    "london time": "Europe/London",
    "kl time": "Asia/Kuala_Lumpur",
    "malaysia time": "Asia/Kuala_Lumpur",
    "myt": "Asia/Kuala_Lumpur",     # Malaysia Time
    "sgt": "Asia/Singapore",        # Singapore Time
}


def get_city_time(text: str) -> Optional[str]:
    """Return current local time for the first recognised location in text."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return None

    tl = text.lower()
    for city, tz_name in sorted(_TZ_MAP.items(), key=lambda x: -len(x[0])):
        if city in tl:
            try:
                tz  = ZoneInfo(tz_name)
                now = datetime.now(tz)
                offset = now.strftime("%z")
                utc_str = f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else "UTC"
                return (
                    f"It's **{now.strftime('%H:%M')}** in {city.title()} "
                    f"({now.strftime('%A, %d %b %Y')}, {utc_str})"
                )
            except Exception:
                return None
    return None


# ── Tavily web search ──────────────────────────────────────────────────────────

def _tavily_search(query: str) -> Optional[str]:
    """
    Real web search via Tavily — returns a clean answer with sources.
    Uses search_depth='advanced' for football/player research queries.
    """
    if not TAVILY_API_KEY:
        return None
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)

        # Advanced depth for research queries, basic for quick facts
        is_deep = any(w in query.lower() for w in [
            "stats", "career", "history", "research", "biography",
            "transfer", "squad", "lineup", "injury", "form"
        ])

        response = client.search(
            query=query,
            search_depth="advanced" if is_deep else "basic",
            max_results=5,
            include_answer=True,
            include_raw_content=False,
        )

        # Prefer the AI-generated answer if available
        answer = (response.get("answer") or "").strip()
        if len(answer) > 60:
            # Append top source names for credibility
            sources = [
                r.get("url", "").split("/")[2].replace("www.", "")
                for r in response.get("results", [])[:2]
                if r.get("url")
            ]
            src_str = ", ".join(s for s in sources if s)
            return answer[:700] + (f"\n[{src_str}]" if src_str else "")

        # Fallback: stitch top result snippets
        snippets = [
            r.get("content", "").strip()
            for r in response.get("results", [])[:3]
            if r.get("content")
        ]
        combined = " ".join(snippets)[:700]
        return combined if len(combined) > 60 else None

    except Exception as exc:
        logger.warning("Tavily search failed: %s", exc)
        return None


# ── DuckDuckGo fallback ────────────────────────────────────────────────────────

def _ddg_search(query: str) -> Optional[str]:
    """DuckDuckGo instant answers — fallback when Tavily is not configured."""
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1",
                    "skip_disambig": "1", "no_redirect": "1"},
            timeout=6,
        )
        data = resp.json()
        abstract = (data.get("AbstractText") or "").strip()
        if len(abstract) > 60:
            src = data.get("AbstractSource", "")
            return abstract[:500] + (f" [{src}]" if src else "")
        answer = (data.get("Answer") or "").strip()
        if answer:
            return answer
        for topic in data.get("RelatedTopics", []):
            if isinstance(topic, dict):
                snippet = (topic.get("Text") or "").strip()
                if len(snippet) > 50:
                    return snippet[:400]
        return None
    except Exception as exc:
        logger.debug("DDG search failed: %s", exc)
        return None


# ── Public interface ───────────────────────────────────────────────────────────

def web_search(query: str) -> Optional[str]:
    """Tavily → DuckDuckGo fallback. Returns clean text or None."""
    if TAVILY_API_KEY:
        result = _tavily_search(query)
        if result:
            return result
    return _ddg_search(query)


# ── Intent detection ───────────────────────────────────────────────────────────

_TIME_TRIGGERS = {
    "time in", "time at", "what time", "current time", "utc", "gmt",
    "timezone", "time zone", "clock in", "what's the time", "hour in",
    "time now in", "local time",
}

_RESEARCH_TRIGGERS = {
    "who is", "who's", "what is", "what's", "tell me about", "research",
    "stats", "statistics", "how old", "nationality", "career", "born",
    "plays for", "which club", "history of", "facts about", "how tall",
    "position", "transfer", "net worth", "salary", "injury", "form",
    "recent form", "squad", "lineup", "manager", "coach", "record",
    "goals", "assists", "caps", "trophies", "world cup", "champions league",
    "premier league", "la liga", "bundesliga", "serie a", "ligue 1",
    # Live score / result queries
    "score", "the score", "result", "who's winning", "who is winning",
    "how many goals", "what's the result", "live score", "current score",
    "how's the game", "how's the match", "what's happening in",
}


def detect_research_intent(text: str) -> str:
    """
    Returns 'time', 'search', or 'none'.
    """
    tl = text.lower()
    if any(w in tl for w in _TIME_TRIGGERS):
        return "time"
    if any(w in tl for w in _RESEARCH_TRIGGERS):
        return "search"
    return "none"
