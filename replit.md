# YouTube Comment Picker — Telegram Bot

A production-ready Telegram bot that fetches all comments from any YouTube video so you can pick random winners, search commenters, filter by keyword, and export to CSV.

## Run & Operate

- `cd telegram-bot && python bot.py` — run the bot (requires secrets below)
- Required secrets: `TELEGRAM_BOT_TOKEN`, `YOUTUBE_API_KEY`

## Stack

- Python 3.11+
- python-telegram-bot v21 (async)
- aiohttp — async YouTube API calls
- SQLite — comment storage (no external DB needed)
- YouTube Data API v3

## Where things live

```
telegram-bot/
├── bot.py          — All Telegram handlers, inline keyboards, commands
├── youtube.py      — YouTube Data API v3: URL parsing, metadata, comment pagination
├── database.py     — SQLite: save/fetch/search/export comments
├── config.py       — Env var config and tunables
├── requirements.txt
└── README.md       — Full setup guide with hosting instructions
```

## Architecture decisions

- All YouTube API calls are async via aiohttp — never blocks the bot
- Comments stored in SQLite locally — no external database needed
- Cache layer (1 hour TTL) avoids re-fetching the same video repeatedly
- Winners always drawn from unique-user pool — no duplicate entries
- Progress callback sends live Telegram updates every 500 fetched comments

## Product

Users send any YouTube URL to the bot. It fetches all comments (with live progress updates), then offers inline buttons to pick random winners, filter by keyword, search by username, and export to CSV.

## User preferences

- _Populate as you build_

## Gotchas

- YouTube API quota is 10,000 units/day. Each comment page = 1 unit. ~100 medium videos/day.
- Run `pip install -r telegram-bot/requirements.txt` before starting locally.
- Set both `TELEGRAM_BOT_TOKEN` and `YOUTUBE_API_KEY` in the Secrets tab before running.

## Pointers

- See `telegram-bot/README.md` for full setup guide including BotFather, Google Cloud, and free hosting
