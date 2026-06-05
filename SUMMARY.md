# PredictArena AI — Project Summary

Private FIFA World Cup 2026 prediction bot for two players, built on Telegram.
Everything is automated — the only manual action required from users is tapping their prediction.

---

## Features

### Fully Automated Match Lifecycle

1. **Fixture sync** — bot pulls all WC 2026 fixtures from football-data.org every 6 hours, including knockout fixtures as teams qualify round by round. No manual schedule entry needed.
2. **Prediction prompts** — ~24 hours before each match, the bot DMs both players directly with an inline keyboard. One tap to predict.
3. **30-minute nudge** — if a player still hasn't picked with 30 minutes to go, the bot DMs them again personally with the keyboard.
4. **Group reminders** — countdown posts at 30, 15, 10, and 5 minutes to kickoff showing who has and hasn't predicted (✅/❌).
5. **Kickoff lock & reveal** — at kickoff, predictions lock (no more changes), and the bot posts both players' picks to the group with an AI-generated needle.
6. **Auto result detection** — polls football-data.org every 3 minutes. When a match is confirmed finished, scores are graded and posted to the group automatically.
7. **Full-time announcement** — result, each player's prediction outcome, points delta, AI roast, and updated leaderboard — all in one message.
8. **Concurrent matches** — if multiple matches kick off simultaneously (common in final group rounds), all are handled independently and correctly.

### Prediction Flow

- Players receive a DM with flag-labelled buttons: `🇧🇷 Brazil  🤝 Draw  🇦🇷 Argentina`
- Draw is shown for group stage only. Knockout rounds show only the two teams.
- Players can change their prediction any number of times until the second kickoff time passes.
- After kickoff: tapping any old keyboard button returns "🔒 Too late — this match has kicked off."

### Scoring

| Outcome | Group | R32 | R16 | QF | SF | Final |
|---------|-------|-----|-----|-----|-----|-------|
| Correct | +1 | +2 | +3 | +5 | +8 | +15 |
| Wrong | 0 | 0 | 0 | 0 | 0 | 0 |
| Missed | −1 | −2 | −3 | −3 | −3 | −3 |

- **Wrong** = you picked but got it wrong. No penalty — just no points.
- **Missed** = you never submitted a prediction. Penalty applies regardless of reason.
- Knockout results use the `score.winner` field from the API — accounts for AET and penalty shootouts correctly.

### Leaderboard & Stats

- `/leaderboard` — ranked standings with points, accuracy %, and current streak
- `/stats` — your own breakdown: correct / wrong / missed / accuracy / streak / best streak

### AI Commentary (Gemini Flash)

- **Kickoff needle** — one savage line at the prediction reveal, aimed at whoever made the riskier pick
- **Full-time roast** — 3–5 sentences mocking wrong predictions by name, referencing the exact scoreline. Correct predictions get a backhanded compliment. Both wrong = equal suffering. Uses first names only, max 2 emojis, no hashtags.

---

## Commands

### User commands (DM or group)

| Command | Description |
|---------|-------------|
| `/predict` | Show current picks and prediction keyboard (DM only) |
| `/upcoming` | Next 10 matches with kickoff times, stages, point stakes |
| `/leaderboard` | Current standings |
| `/stats` | Your personal stats |
| `/help` | Command list |

### Admin commands (DM only — Kevin)

| Command | Description |
|---------|-------------|
| `/matches` | List all matches with internal IDs and status |
| `/addmatch <api_id> <Home> <Away> <YYYY-MM-DDTHH:MM:SS> <stage>` | Add a match manually. Use `none` for api_id if no API match. Use underscores for team name spaces. |
| `/syncmatches` | Manually trigger fixture sync from football-data.org |
| `/setresult <match_id> <home_score> <away_score>` | Manual result entry (fallback if auto-check fails) |
| `/regrade <match_id>` | Reverse and re-run grading for a match |
| `/users` | List registered users |
| `/test` | Create a dummy match kicking off in 3 minutes — full flow test |
| `/testsuccess` | Wipe all test data and restore scores |

### Valid stages for `/addmatch`

`group` · `round_of_32` · `round_of_16` · `quarter_final` · `semi_final` · `final`

---

## Setup Guide

### Prerequisites

- Python 3.11+
- A Telegram bot token (from @BotFather)
- Both players' Telegram user IDs
- A Telegram group ID where the bot is admin
- A Gemini API key (free at aistudio.google.com)
- (Optional) A football-data.org API key (free at football-data.org/client/register)

### 1. Get your Telegram IDs

Send a message to [@userinfobot](https://t.me/userinfobot) to get your own Telegram ID.
For the group ID, add [@RawDataBot](https://t.me/RawDataBot) to the group temporarily.

### 2. Clone and install

```bash
cd ~/Desktop
# The project folder is already at predicarena/
cd predicarena
pip install -r requirements.txt
```

### 3. Create .env

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_GROUP_ID=-100xxxxxxxxxx
ADMIN_TELEGRAM_ID=111111111        # Kevin's Telegram ID
ADMIN_NAME=Kevin
USER_2_TELEGRAM_ID=222222222       # Mathavi's Telegram ID
USER_2_NAME=Mathavi
GEMINI_API_KEY=your_gemini_key_here
FOOTBALL_DATA_KEY=your_football_data_key  # optional but recommended
```

### 4. Run locally

```bash
python main.py
```

Polling mode starts automatically when `WEBHOOK_URL` is not set.
Both players are auto-registered on startup — no `/start` needed, but they must have DM'd the bot at least once for it to DM them.

### 5. First-time setup

1. Both Kevin and Mathavi DM the bot `/start` — this opens the DM channel so the bot can message first.
2. Kevin runs `/syncmatches` in DM to load all WC 2026 group fixtures.
3. That's it. The bot handles everything from here.

---

## Deploying to Render

### 1. Push to GitHub

```bash
git init && git add . && git commit -m "init"
git remote add origin https://github.com/you/predicarena.git
git push
```

### 2. Create a Web Service on Render

- Runtime: Python 3
- Build command: `pip install -r requirements.txt`
- Start command: `python main.py`
- Instance type: Free

### 3. Add a Persistent Disk

The SQLite database must survive restarts.

- Go to your service → Disks → Add Disk
- Mount path: `/data`
- Size: 1 GB (more than enough)

### 4. Set environment variables (Render → Environment)

Everything from your `.env` file, plus:

```
WEBHOOK_URL=https://your-service.onrender.com
WEBHOOK_SECRET=pick_a_random_string
DATABASE_PATH=/data/predicarena.db
PORT=10000
```

### 5. Deploy

Render auto-deploys on push. The bot switches to webhook mode automatically when `WEBHOOK_URL` is set.

---

## Architecture

```
predicarena/
├── main.py                  Entry point — polling (local) or webhook (Render)
├── config.py                All env vars, scoring constants, stage definitions
├── requirements.txt
├── render.yaml              Render deployment config
├── .env.example
│
├── database/
│   └── db.py                SQLite layer — schema, migrations, all CRUD functions
│
├── services/
│   ├── football.py          football-data.org v4 client (fixtures + results)
│   ├── scoring.py           Grade match predictions, determine results
│   └── ai.py                Gemini Flash — kickoff needle + full-time roast
│
├── bot/
│   ├── handlers.py          All Telegram command + callback handlers
│   ├── keyboards.py         Inline keyboards with flag emojis
│   └── flags.py             Country flag emoji map for all WC 2026 nations
│
└── scheduler/
    └── jobs.py              5 background jobs (auto-sync, DM prompts, reminders,
                             kickoff lock/reveal, result polling)
```

### Background jobs

| Job | Interval | What it does |
|-----|----------|--------------|
| `job_auto_sync_fixtures` | Every 6h | Pulls new WC fixtures from football-data.org |
| `job_send_prediction_prompts` | Every 30min | DMs players for matches kicking off in next 24h |
| `job_reminders` | Every 60s | Group reminders at 30/15/10/5 min + personal nudge at 30 min |
| `job_match_starts` | Every 60s | Locks predictions at kickoff, posts reveal + AI needle |
| `job_check_results` | Every 3min | Polls API for finished matches, grades and posts result |

### Known limitations

- **Streak counter** is not reversed during `/regrade` — recalculating streaks from the full match history is complex and not worth it for 2 players. Points, correct/wrong/missed counts are all reversed correctly.
- **Bot DMs require prior contact** — Telegram does not allow bots to message users who have never started a conversation. Both players must send `/start` once.
- **Free tier rate limit** — football-data.org allows 10 calls/minute. With up to 6 simultaneous live matches in the group stage, the bot uses at most 2 calls/minute. Well within limits.
