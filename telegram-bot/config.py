import os

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ── YouTube ───────────────────────────────────────────────────────────────────
YOUTUBE_API_KEY: str = os.environ.get("YOUTUBE_API_KEY", "")
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Maximum comments to fetch per video (None = fetch all available)
MAX_COMMENTS: int | None = None

# ── Rate limiting ─────────────────────────────────────────────────────────────
# Seconds a user must wait between fetch requests
COOLDOWN_SECONDS: int = 30

# ── Cache ──────────────────────────────────────────────────────────────────────
# How long (seconds) to keep cached comment data before re-fetching
CACHE_TTL_SECONDS: int = 3600  # 1 hour

# ── Auto-cleanup ───────────────────────────────────────────────────────────────
# Delete DB rows older than this many seconds on startup
DB_MAX_AGE_SECONDS: int = 86400  # 24 hours

# ── Retry ─────────────────────────────────────────────────────────────────────
MAX_RETRIES: int = 3
RETRY_DELAY_SECONDS: float = 2.0

# ── Admin ─────────────────────────────────────────────────────────────────────
# Telegram user IDs of admins (leave empty to disable admin-only commands)
ADMIN_IDS: list[int] = []
