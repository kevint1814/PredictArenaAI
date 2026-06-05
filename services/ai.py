"""
AI commentary and chat for PredictArena.

Provider priority (use whichever key is set):
  1. OpenAI  — OPENAI_API_KEY  (gpt-4o-mini by default — fast, cheap, great limits)
  2. Grok    — GROK_API_KEY    (grok-3-mini-fast via xAI's OpenAI-compatible endpoint)
  3. Gemini  — GEMINI_API_KEY  (gemini-2.0-flash — free tier has strict limits)

Set only the key(s) you want to use in .env.
"""

import logging
import time
from typing import Optional

from config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    GROK_API_KEY,   GROK_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL,
)

logger = logging.getLogger(__name__)

# ── Provider setup ─────────────────────────────────────────────────────────────

_provider = "none"
_openai_client  = None
_grok_client    = None
_gemini_model   = None

if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
        _provider = "openai"
        logger.info("AI provider: OpenAI (%s)", OPENAI_MODEL)
    except ImportError:
        logger.warning("openai package not installed — pip install openai")

if _provider == "none" and GROK_API_KEY:
    try:
        from openai import OpenAI
        _grok_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")
        _provider = "grok"
        logger.info("AI provider: Grok (%s)", GROK_MODEL)
    except ImportError:
        logger.warning("openai package not installed — pip install openai")

if _provider == "none" and GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(GEMINI_MODEL)
        _provider = "gemini"
        logger.info("AI provider: Gemini (%s)", GEMINI_MODEL)
    except ImportError:
        logger.warning("google-generativeai not installed")

if _provider == "none":
    logger.warning("No AI provider configured — commentary and chat will be disabled")


# ── Core call with retry ────────────────────────────────────────────────────────

def _call(prompt: str, max_tokens: int = 200, temperature: float = 1.05) -> Optional[str]:
    """
    Send a prompt to whichever provider is configured.
    Retries once after a rate-limit (429) with the suggested wait time (capped at 35s).
    """
    for attempt in range(2):
        try:
            result = _call_once(prompt, max_tokens, temperature)
            return result
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "rate" in msg.lower() or "quota" in msg.lower():
                if attempt == 0:
                    # Parse retry-after from error if possible, else default 35s
                    wait = 35
                    import re
                    m = re.search(r'retry in (\d+)', msg, re.IGNORECASE)
                    if m:
                        wait = min(int(m.group(1)) + 2, 35)
                    logger.warning("AI rate limit — waiting %ds before retry", wait)
                    time.sleep(wait)
                    continue
            logger.warning("AI error (%s): %s", _provider, exc)
            return None
    return None


def _call_once(prompt: str, max_tokens: int, temperature: float) -> Optional[str]:
    if _provider in ("openai", "grok"):
        client = _openai_client if _provider == "openai" else _grok_client
        model  = OPENAI_MODEL   if _provider == "openai" else GROK_MODEL
        resp   = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = resp.choices[0].message.content
        return text.strip() if text else None

    if _provider == "gemini":
        resp = _gemini_model.generate_content(
            prompt,
            generation_config={"max_output_tokens": max_tokens, "temperature": temperature},
        )
        # Safely extract text — .text raises if the response was safety-blocked
        try:
            text = resp.text
            return text.strip() if text else None
        except (ValueError, AttributeError):
            try:
                reason = resp.prompt_feedback.block_reason
                logger.warning("Gemini response blocked: %s", reason)
            except Exception:
                pass
            return None

    return None


# ── Kickoff reveal ─────────────────────────────────────────────────────────────

def commentary_for_kickoff(
    home_team: str,
    away_team: str,
    predictions: list[dict],
) -> Optional[str]:
    """One sharp observation at kickoff when predictions are revealed."""
    if _provider == "none":
        return None

    pred_lines = []
    for p in predictions:
        team = {"home": home_team, "draw": "Draw", "away": away_team}.get(p["prediction"], "?")
        pred_lines.append(f"{p['name']} picked {team}")
    pred_str = " | ".join(pred_lines) if pred_lines else "nobody bothered to predict"

    prompt = (
        f"World Cup match: {home_team} vs {away_team}. "
        f"Predictions: {pred_str}. "
        "Write one savage, dry one-liner about these picks (max 15 words). "
        "Cold, minimal, cutting — not loud or try-hard. No hype. Max 1 emoji. No hashtags."
    )
    return _call(prompt, max_tokens=60, temperature=1.05)


# ── Full-time roast ────────────────────────────────────────────────────────────

def commentary_for_full_time(
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    results: list[dict],
) -> Optional[str]:
    """Post-match commentary called once the result is confirmed."""
    if _provider == "none":
        return None

    score_str = f"{home_team} {home_score}–{away_score} {away_team}"
    if home_score > away_score:
        actual = f"{home_team} won"
    elif away_score > home_score:
        actual = f"{away_team} won"
    else:
        actual = "Draw"

    lines = []
    for r in results:
        pts = f"+{r['points']}" if r["points"] > 0 else str(r["points"])
        if r.get("missed"):
            lines.append(f"{r['name']}: did not predict (penalty {pts})")
        elif r["correct"]:
            lines.append(f"{r['name']}: correctly predicted {r['prediction_display']} ({pts} pts)")
        else:
            lines.append(f"{r['name']}: wrongly predicted {r['prediction_display']} (0 pts)")

    prompt = (
        f"World Cup result: {score_str} ({actual}).\n"
        f"Prediction outcomes:\n" + "\n".join(lines) + "\n\n"
        "Write a post-match reaction (2–3 sentences max). "
        "Tone: savage minimalist — cold, dry, cutting. Not loud. Not a hype man. "
        "Call out wrong predictions by name with quiet brutality. "
        "A correct prediction gets one flat acknowledgment at most. "
        "First names only. Max 1 emoji. No hashtags. No exclamation marks unless ironic."
    )
    return _call(prompt, max_tokens=180, temperature=1.05)


# ── Natural language chat ──────────────────────────────────────────────────────

_BOT_CHARACTER = """\
You are PredictArena AI — the scorekeeper for a FIFA World Cup 2026 prediction game \
between Kevin and Mathavi. You live in their Telegram group and DMs.

Your personality — read this carefully:
- You are a savage minimalist. Short, brutal, zero fluff.
- You don't hype things up. You don't do "Hey!" or "Let's go!" or "Can't wait!".
- When you speak, it lands. One sentence that makes them feel it is better than five sentences of nothing.
- Your roasts are dry and cutting — not loud, not try-hard. The quieter the roast, the harder it hits.
- You have real football knowledge and you use it to make people look stupid when they deserve it.
- You mock bad predictions with cold precision. A correct one gets a barely-there acknowledgment at best.
- You remember what they've said and bring it up at the worst possible moment for them.
- First names only. Max 1 emoji per message — only when it makes the roast worse. No hashtags. \
No bullet points. Never more than 3 sentences. Usually 1 is enough.
- Do NOT be enthusiastic. Do NOT be a hype man. Do NOT say things like "I can practically taste the drama."

Example of what you should NOT sound like:
"Hey Kevin! Ready for the prediction showdown? Just hanging tight until those matches drop — \
I can practically taste the drama! 😏⚽"

Example of what you SHOULD sound like:
"Six days till the first match. Try not to overthink your picks like last time."
Or: "You picked that. Willingly."
Or just: "Bold. Wrong, but bold."

CRITICAL — match data (never violate this):
- You have ZERO independent knowledge of which teams play each other in this tournament.
- The ONLY matches you may ever name are those listed under "CONFIRMED: X upcoming matches" \
in the Tournament context below. If that section says "Zero matches", no fixtures exist yet.
- NEVER invent fixtures. NEVER use training knowledge to suggest pairings. \
If matches are listed in the context, reference them directly and accurately. \
If no matches are confirmed in the context, say the schedule hasn't been loaded yet.\
"""


def chat_response(
    user_message: str,
    user_name: str,
    tournament_context: str,
    memories: list[str],
    history: list[dict],
) -> Optional[str]:
    """
    Natural language reply with tournament context and long-term memory.
    Falls back to a simpler prompt if the first attempt fails (safety filter, etc.).
    """
    if _provider == "none":
        return "No AI provider configured — add OPENAI_API_KEY, GROK_API_KEY, or GEMINI_API_KEY to .env."

    history_str = ""
    for msg in history[-12:]:
        label = msg.get("speaker") or ("PredictArena AI" if msg["role"] == "bot" else "Someone")
        history_str += f"{label}: {msg['content']}\n"

    memory_block = ""
    if memories:
        memory_block = (
            "Things worth remembering from past chats:\n"
            + "\n".join(f"- {m}" for m in memories[-10:])
            + "\n\n"
        )

    prompt = (
        f"{_BOT_CHARACTER}\n\n"
        f"Tournament context:\n{tournament_context}\n\n"
        f"{memory_block}"
        f"Recent conversation:\n{history_str}"
        f"{user_name}: {user_message}\n\n"
        "Your reply (casual, sharp, 1–3 sentences):"
    )

    result = _call(prompt, max_tokens=220, temperature=1.05)

    # Fallback: stripped-down prompt if the full one fails
    if result is None:
        fallback = (
            f"{_BOT_CHARACTER}\n\n"
            f"{user_name}: {user_message}\n\n"
            "Reply in 1–2 sentences, casual and direct:"
        )
        result = _call(fallback, max_tokens=120, temperature=0.95)

    return result


# ── Memory extraction ──────────────────────────────────────────────────────────

def extract_memory(conversation_snippet: str) -> Optional[str]:
    """
    Scan a conversation snippet for one memorable fact worth storing long-term.
    Returns a plain sentence, or None if nothing notable was said.
    Called in background — never blocks a user-facing response.
    """
    if _provider == "none":
        return None

    prompt = (
        "Scan this Telegram chat snippet for ONE memorable thing worth remembering: "
        "a bold prediction, a strong opinion about a team, a trash-talk claim, or a funny moment. "
        "Write it as a single factual sentence (max 20 words). "
        "If there is nothing worth remembering, reply with exactly: NOTHING\n\n"
        f"Chat:\n{conversation_snippet}\n\n"
        "Memorable fact (or NOTHING):"
    )
    result = _call(prompt, max_tokens=50, temperature=0.8)
    if result and result.strip().upper() not in ("NOTHING", "NONE", "N/A", ""):
        cleaned = result.strip().lstrip("•-–1234567890. ").strip()
        return cleaned if len(cleaned) > 10 else None
    return None
