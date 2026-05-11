# YouTube Comment Picker — Telegram Bot

A production-ready Telegram bot that fetches all comments from any YouTube video so you can pick random winners, search commenters, filter by keyword, and export to CSV.

---

## Files

```
telegram-bot/
├── bot.py          — Main bot logic, commands, inline buttons
├── youtube.py      — YouTube Data API helpers + pagination
├── database.py     — SQLite storage for comments
├── config.py       — Environment variable config
├── requirements.txt
└── README.md
```

---

## Step 1 — Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g. "YT Comment Picker")
4. Choose a username ending in `bot` (e.g. `yt_comment_picker_bot`)
5. BotFather replies with your **bot token** — looks like `123456789:ABCdef...`

---

## Step 2 — Get a YouTube API Key

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (any name)
3. Go to **APIs & Services → Library**, search **YouTube Data API v3**, click **Enable**
4. Go to **APIs & Services → Credentials**, click **+ Create Credentials → API key**
5. Copy your key

---

## Step 3 — Set Environment Variables

In Replit, go to the **Secrets** tab and add:

| Key | Value |
|-----|-------|
| `TELEGRAM_BOT_TOKEN` | Your token from BotFather |
| `YOUTUBE_API_KEY` | Your key from Google Cloud |

---

## Step 4 — Install & Run

### Locally (Python 3.11+)

```bash
cd telegram-bot
pip install -r requirements.txt
TELEGRAM_BOT_TOKEN=xxx YOUTUBE_API_KEY=yyy python bot.py
```

### On Replit

Add both secrets via the Secrets tab, then click **Run**. The bot runs automatically.

### Free hosting alternatives

**Render.com**
1. Create a new "Background Worker" service
2. Connect your GitHub repo
3. Set build command: `pip install -r telegram-bot/requirements.txt`
4. Set start command: `python telegram-bot/bot.py`
5. Add environment variables in the Render dashboard

**Railway.app**
1. New project → Deploy from GitHub
2. Set the same build/start commands
3. Add env vars under Variables tab

---

## Bot Commands

| Command | What it does |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | List all commands |
| `/winner` | Pick one random winner |
| `/multiwinner` | Pick 1, 3, 5 or custom number of winners |
| `/unique` | Show unique commenter count |
| `/search <name>` | Search commenters by name |
| `/export` | Export all comments as CSV |
| `/stats` | Show video comment statistics |

You can also just **send a YouTube URL** to start fetching.

---

## Inline Buttons (after fetching a video)

| Button | Action |
|--------|--------|
| 🎲 Pick Winner | Random winner from unique users |
| 🏆 Multi Winner | Pick 1 / 3 / 5 / custom winners |
| 👥 Unique Users | Summary of unique commenters |
| 🔎 Search User | Search by username |
| 🔑 Filter Keyword | Filter comments by keyword (e.g. #giveaway) |
| 🧹 Remove Duplicates | Show dedup stats |
| 📄 Export CSV | Full comment export |
| 📋 Usernames Only | Unique usernames CSV |
| 📊 Stats | Comment statistics |

---

## Configuration (config.py)

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_COMMENTS` | `None` | Cap on comments fetched (None = all) |
| `COOLDOWN_SECONDS` | `30` | Seconds between user fetches |
| `CACHE_TTL_SECONDS` | `3600` | How long cached data stays fresh |
| `DB_MAX_AGE_SECONDS` | `86400` | Auto-delete rows older than this |
| `MAX_RETRIES` | `3` | API retry attempts |

---

## YouTube API Quota

Each page of comments costs **1 quota unit**. A video with 10,000 comments uses ~100 units.
The free daily quota is **10,000 units** — enough for ~100 medium-sized videos per day.
