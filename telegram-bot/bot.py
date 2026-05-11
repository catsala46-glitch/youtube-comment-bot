"""
bot.py — Main entry point for the YouTube Comment Picker Telegram bot.

Architecture:
  - config.py   : environment variables and tunables
  - database.py : SQLite persistence
  - youtube.py  : YouTube Data API helpers
  - bot.py      : Telegram handlers, inline keyboards, conversation logic
"""

import asyncio
import csv
import io
import logging
import random
import time
from collections import defaultdict

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import database as db
from youtube import (
    CommentsDisabledError,
    QuotaExceededError,
    VideoNotFoundError,
    YouTubeAPIError,
    extract_video_id,
    fetch_all_comments,
    fetch_video_info,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("YouTubeBot")

# ── In-memory state ───────────────────────────────────────────────────────────
# Maps chat_id -> last fetched video_id
user_video: dict[int, str] = {}

# Rate limiting: chat_id -> last fetch timestamp
cooldown_map: dict[int, float] = defaultdict(float)

# Tracks which chat is waiting for a search query: chat_id -> video_id
awaiting_search: dict[int, str] = {}

# Tracks which chat is waiting for a keyword filter: chat_id -> video_id
awaiting_keyword: dict[int, str] = {}

# Tracks which chat is waiting for a custom winner count: chat_id -> video_id
awaiting_custom_count: dict[int, str] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _main_keyboard(video_id: str) -> InlineKeyboardMarkup:
    """Build the main inline action keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎲 Pick Winner",     callback_data=f"winner:{video_id}"),
            InlineKeyboardButton("🏆 Multi Winner",    callback_data=f"multi:{video_id}"),
        ],
        [
            InlineKeyboardButton("👥 Unique Users",    callback_data=f"unique:{video_id}"),
            InlineKeyboardButton("🔎 Search User",     callback_data=f"search:{video_id}"),
        ],
        [
            InlineKeyboardButton("🔑 Filter Keyword",  callback_data=f"keyword:{video_id}"),
            InlineKeyboardButton("🧹 Remove Duplicates", callback_data=f"dedup:{video_id}"),
        ],
        [
            InlineKeyboardButton("📄 Export CSV",      callback_data=f"export:{video_id}"),
            InlineKeyboardButton("📋 Usernames Only",  callback_data=f"exportnames:{video_id}"),
        ],
        [
            InlineKeyboardButton("📊 Stats",           callback_data=f"stats:{video_id}"),
        ],
    ])


def _multi_winner_keyboard(video_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1 Winner",  callback_data=f"mw:1:{video_id}"),
            InlineKeyboardButton("3 Winners", callback_data=f"mw:3:{video_id}"),
            InlineKeyboardButton("5 Winners", callback_data=f"mw:5:{video_id}"),
        ],
        [InlineKeyboardButton("✏️ Custom number", callback_data=f"mw:custom:{video_id}")],
        [InlineKeyboardButton("⬅️ Back",          callback_data=f"back:{video_id}")],
    ])


def _spinning_frames() -> list[str]:
    return ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]


async def _spin_animation(message, prefix: str) -> None:
    """Replace message text with a spinning emoji for 1.6 s."""
    frames = _spinning_frames()
    for i in range(8):
        try:
            await message.edit_text(f"{frames[i % len(frames)]}  {prefix}")
        except Exception:
            pass
        await asyncio.sleep(0.2)


def _escape(text: str) -> str:
    """Escape special chars for MarkdownV2."""
    for ch in r"_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def _is_on_cooldown(chat_id: int) -> float:
    """Return seconds remaining on cooldown, or 0 if not on cooldown."""
    elapsed = time.time() - cooldown_map[chat_id]
    remaining = config.COOLDOWN_SECONDS - elapsed
    return max(0.0, remaining)


# ── /start ─────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info("ADMIN | /start from user_id=%s username=%s", user.id, user.username)
    await update.message.reply_text(
        "👋 *Welcome to YouTube Comment Picker!*\n\n"
        "Send me any YouTube video URL and I'll fetch all its comments so you can:\n\n"
        "🎲 Pick a random winner\n"
        "🏆 Pick multiple winners\n"
        "🔎 Search for a specific commenter\n"
        "📄 Export comments to CSV\n"
        "🧹 Remove duplicate entries\n\n"
        "Just paste a YouTube link to get started!",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /help ──────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Available Commands*\n\n"
        "/start — Welcome message\n"
        "/help — Show this help\n"
        "/search `<username>` — Search commenters\n"
        "/winner — Pick a random winner\n"
        "/multiwinner — Pick multiple winners\n"
        "/unique — List unique commenters\n"
        "/export — Export all comments to CSV\n"
        "/stats — Show comment statistics\n\n"
        "💡 *Tip:* Just send a YouTube URL to start fetching comments!",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── URL handler — fetch comments ───────────────────────────────────────────────

async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Called when the user sends a YouTube URL."""
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    user = update.effective_user

    # Check if user is entering a search/keyword/custom count reply
    if chat_id in awaiting_search:
        await _handle_search_reply(update, text)
        return
    if chat_id in awaiting_keyword:
        await _handle_keyword_reply(update, text)
        return
    if chat_id in awaiting_custom_count:
        await _handle_custom_count_reply(update, text)
        return

    # Extract video ID
    video_id = extract_video_id(text)
    if not video_id:
        await update.message.reply_text(
            "❌ That doesn't look like a valid YouTube URL.\n"
            "Please send a link like:\n`https://youtu.be/VIDEO_ID`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Cooldown check
    remaining = _is_on_cooldown(chat_id)
    if remaining > 0:
        await update.message.reply_text(
            f"⏳ Please wait *{remaining:.0f}s* before fetching another video.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    logger.info("ADMIN | URL fetch | user_id=%s video_id=%s", user.id, video_id)

    # Check cache first
    cached = db.get_cache_meta(video_id)
    if cached and (time.time() - cached["fetched_at"]) < config.CACHE_TTL_SECONDS:
        user_video[chat_id] = video_id
        age_min = int((time.time() - cached["fetched_at"]) / 60)
        await update.message.reply_text(
            f"⚡ *Loaded from cache* \\({age_min} min ago\\)\n\n"
            f"📹 *{_escape(cached['video_title'])}*\n"
            f"💬 Comments: *{cached['total_count']:,}*",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_main_keyboard(video_id),
        )
        return

    # Send typing indicator then a progress message
    await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
    progress_msg = await update.message.reply_text("⏳ Fetching video info…")

    # Fetch video metadata
    try:
        info = await fetch_video_info(video_id)
    except VideoNotFoundError:
        await progress_msg.edit_text("❌ Video not found or is private.")
        return
    except YouTubeAPIError as exc:
        await progress_msg.edit_text(f"❌ API error: {exc}")
        return

    await progress_msg.edit_text(
        f"📹 *{_escape(info['title'])}*\n"
        f"📊 Expected comments: *{info['comment_count']:,}*\n\n"
        "⏳ Fetching comments… this may take a while for large videos.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    start_ts = time.time()

    # Progress callback — edit the message every 500 comments
    last_update = [0]

    async def on_progress(count: int) -> None:
        if count - last_update[0] >= 500:
            last_update[0] = count
            try:
                await progress_msg.edit_text(
                    f"📥 Fetching comments…\n"
                    f"✅ *{count:,}* fetched so far\\.\\.\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            except Exception:
                pass

    # Fetch all comments
    try:
        comments = await fetch_all_comments(video_id, progress_cb=on_progress, max_comments=config.MAX_COMMENTS)
    except CommentsDisabledError:
        await progress_msg.edit_text("🔕 Comments are disabled on this video.")
        return
    except QuotaExceededError:
        await progress_msg.edit_text("⚠️ YouTube API quota exceeded. Please try again tomorrow.")
        return
    except YouTubeAPIError as exc:
        await progress_msg.edit_text(f"❌ Error fetching comments: {exc}")
        return

    elapsed = time.time() - start_ts
    unique_users = len({c["author"] for c in comments})

    # Persist to DB
    db.save_comments(video_id, comments, info["title"])
    user_video[chat_id] = video_id
    cooldown_map[chat_id] = time.time()

    logger.info(
        "ADMIN | Fetch done | video_id=%s total=%d unique=%d elapsed=%.1fs",
        video_id, len(comments), unique_users, elapsed,
    )

    await progress_msg.edit_text(
        f"✅ *Done\\!*\n\n"
        f"📹 *{_escape(info['title'])}*\n"
        f"💬 Total comments: *{len(comments):,}*\n"
        f"👥 Unique users: *{unique_users:,}*\n"
        f"⏱ Fetched in: *{elapsed:.1f}s*\n\n"
        "Choose an action below 👇",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=_main_keyboard(video_id),
    )


# ── Inline button dispatcher ───────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = update.effective_chat.id

    if data.startswith("winner:"):
        video_id = data.split(":", 1)[1]
        await action_winner(query, video_id, n=1)

    elif data.startswith("multi:"):
        video_id = data.split(":", 1)[1]
        await query.edit_message_reply_markup(_multi_winner_keyboard(video_id))

    elif data.startswith("mw:"):
        _, count_str, video_id = data.split(":", 2)
        if count_str == "custom":
            awaiting_custom_count[chat_id] = video_id
            await query.edit_message_text(
                "✏️ How many winners would you like? Send a number (e.g. `10`):",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await action_winner(query, video_id, n=int(count_str))

    elif data.startswith("unique:"):
        video_id = data.split(":", 1)[1]
        await action_unique(query, video_id)

    elif data.startswith("search:"):
        video_id = data.split(":", 1)[1]
        awaiting_search[chat_id] = video_id
        await query.edit_message_text(
            "🔎 Send the *username* you want to search for:",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("keyword:"):
        video_id = data.split(":", 1)[1]
        awaiting_keyword[chat_id] = video_id
        await query.edit_message_text(
            "🔑 Send a *keyword* to filter comments \\(e\\.g\\. `\\#giveaway`\\):",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    elif data.startswith("dedup:"):
        video_id = data.split(":", 1)[1]
        await action_dedup(query, video_id)

    elif data.startswith("export:"):
        video_id = data.split(":", 1)[1]
        await action_export(query, update, video_id, usernames_only=False)

    elif data.startswith("exportnames:"):
        video_id = data.split(":", 1)[1]
        await action_export(query, update, video_id, usernames_only=True)

    elif data.startswith("stats:"):
        video_id = data.split(":", 1)[1]
        await action_stats(query, video_id)

    elif data.startswith("back:"):
        video_id = data.split(":", 1)[1]
        await query.edit_message_reply_markup(_main_keyboard(video_id))


# ── Actions ───────────────────────────────────────────────────────────────────

async def action_winner(query, video_id: str, n: int) -> None:
    """Pick n random winners from unique users."""
    meta = db.get_cache_meta(video_id)
    comments = db.get_unique_comments(video_id)

    if not comments:
        await query.edit_message_text("❌ No comments found. Please re-fetch the video.")
        return

    if n > len(comments):
        n = len(comments)

    # Spinning animation
    spin_msg = await query.edit_message_text("🎰 Picking winner…")
    await _spin_animation(spin_msg, "Picking winners…")

    winners = random.sample(comments, n)
    title = meta["video_title"] if meta else video_id

    lines = [f"🏆 *Winner{'s' if n > 1 else ''} — {_escape(title)}*\n"]
    for i, w in enumerate(winners, 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"#{i}"
        chan = w.get("author_chan")
        profile_link = f"[{_escape(w['author'])}](https://www.youtube.com/channel/{chan})" if chan else _escape(w["author"])
        lines.append(f"{medal} {profile_link}")
        lines.append(f"   💬 _{_escape(w['text'][:120])}_\n")

    lines.append(f"\n🎲 Selected from *{len(comments):,}* unique users")

    await spin_msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Pick Again", callback_data=f"{'winner' if n == 1 else f'mw:{n}'}:{video_id}"),
            InlineKeyboardButton("⬅️ Back",       callback_data=f"back:{video_id}"),
        ]]),
        disable_web_page_preview=True,
    )


async def action_unique(query, video_id: str) -> None:
    """Show a summary of unique commenters."""
    comments = db.get_unique_comments(video_id)
    all_comments = db.get_comments(video_id)

    if not comments:
        await query.edit_message_text("❌ No comments found.")
        return

    dupes = len(all_comments) - len(comments)
    sample = comments[:10]
    names = "\n".join(f"• {_escape(c['author'])}" for c in sample)
    more = f"\n\\+{len(comments) - 10:,} more…" if len(comments) > 10 else ""

    await query.edit_message_text(
        f"👥 *Unique Commenters*\n\n"
        f"Total unique: *{len(comments):,}*\n"
        f"Duplicate entries removed: *{dupes:,}*\n\n"
        f"*Sample \\(first 10\\):*\n{names}{more}",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back", callback_data=f"back:{video_id}"),
        ]]),
    )


async def action_dedup(query, video_id: str) -> None:
    """Show deduplication summary (data is already stored with unique queries available)."""
    all_c = db.get_comments(video_id)
    unique_c = db.get_unique_comments(video_id)
    removed = len(all_c) - len(unique_c)

    await query.edit_message_text(
        f"🧹 *Duplicate Filter*\n\n"
        f"Total comments: *{len(all_c):,}*\n"
        f"Unique users: *{len(unique_c):,}*\n"
        f"Duplicates removed: *{removed:,}*\n\n"
        "When picking winners, *Unique Users mode* is always used automatically\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back", callback_data=f"back:{video_id}"),
        ]]),
    )


async def action_export(query, update: Update, video_id: str, usernames_only: bool) -> None:
    """Export comments to CSV and send as a file."""
    comments = db.get_comments(video_id)
    if not comments:
        await query.edit_message_text("❌ No comments to export.")
        return

    meta = db.get_cache_meta(video_id)
    title = meta["video_title"] if meta else video_id

    buf = io.StringIO()
    writer = csv.writer(buf)

    if usernames_only:
        writer.writerow(["username", "channel_id"])
        seen = set()
        for c in comments:
            if c["author"] not in seen:
                seen.add(c["author"])
                writer.writerow([c["author"], c.get("author_chan", "")])
        filename = f"usernames_{video_id}.csv"
    else:
        writer.writerow(["username", "channel_id", "profile_pic", "comment"])
        for c in comments:
            writer.writerow([c["author"], c.get("author_chan", ""), c.get("profile_pic", ""), c["text"]])
        filename = f"comments_{video_id}.csv"

    buf.seek(0)
    file_bytes = buf.getvalue().encode("utf-8-sig")  # UTF-8 BOM for Excel

    await update.effective_chat.send_document(
        document=io.BytesIO(file_bytes),
        filename=filename,
        caption=(
            f"📄 {'Usernames' if usernames_only else 'Comments'} export\n"
            f"📹 {title}\n"
            f"📊 {len(comments):,} rows"
        ),
    )
    await query.edit_message_reply_markup(_main_keyboard(video_id))


async def action_stats(query, video_id: str) -> None:
    """Display statistics for the fetched video."""
    meta = db.get_cache_meta(video_id)
    all_c = db.get_comments(video_id)
    unique_c = db.get_unique_comments(video_id)

    if not meta:
        await query.edit_message_text("❌ No data found for this video.")
        return

    age_min = int((time.time() - meta["fetched_at"]) / 60)

    await query.edit_message_text(
        f"📊 *Statistics*\n\n"
        f"📹 *{_escape(meta['video_title'])}*\n"
        f"🆔 Video ID: `{video_id}`\n\n"
        f"💬 Total comments: *{len(all_c):,}*\n"
        f"👥 Unique users: *{len(unique_c):,}*\n"
        f"🔁 Duplicates: *{len(all_c) - len(unique_c):,}*\n\n"
        f"🕐 Fetched *{age_min}* min ago",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back", callback_data=f"back:{video_id}"),
        ]]),
    )


# ── Search / keyword reply handlers ───────────────────────────────────────────

async def _handle_search_reply(update: Update, text: str) -> None:
    chat_id = update.effective_chat.id
    video_id = awaiting_search.pop(chat_id)
    results = db.search_comments(video_id, text)

    if not results:
        await update.message.reply_text(
            f"🔎 No commenters matching *{_escape(text)}* found.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_main_keyboard(video_id),
        )
        return

    lines = [f"🔎 *Search results for* `{_escape(text)}`\n"]
    for r in results[:20]:
        lines.append(f"• *{_escape(r['author'])}*: _{_escape(r['text'][:100])}_")
    if len(results) > 20:
        lines.append(f"\n\\+{len(results) - 20} more matches…")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=_main_keyboard(video_id),
    )


async def _handle_keyword_reply(update: Update, text: str) -> None:
    chat_id = update.effective_chat.id
    video_id = awaiting_keyword.pop(chat_id)
    results = db.filter_by_keyword(video_id, text)

    if not results:
        await update.message.reply_text(
            f"🔑 No comments containing *{_escape(text)}* found.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_main_keyboard(video_id),
        )
        return

    unique = len({r["author"] for r in results})
    await update.message.reply_text(
        f"🔑 *Keyword filter:* `{_escape(text)}`\n\n"
        f"Found *{len(results):,}* comments from *{unique:,}* users\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🎲 Pick Winner from {len(results):,} filtered",
                                  callback_data=f"winner:{video_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:{video_id}")],
        ]),
    )


async def _handle_custom_count_reply(update: Update, text: str) -> None:
    chat_id = update.effective_chat.id
    video_id = awaiting_custom_count.pop(chat_id)

    try:
        n = int(text.strip())
        if n < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Please send a valid positive number.",
            reply_markup=_main_keyboard(video_id),
        )
        return

    # Fake a callback-like object to reuse action_winner
    class _FakeQuery:
        async def edit_message_text(self, *a, **kw):
            return await update.message.reply_text(*a, **kw)

    await action_winner(_FakeQuery(), video_id, n=n)


# ── Slash command shortcuts ────────────────────────────────────────────────────

async def _require_video(update: Update) -> str | None:
    chat_id = update.effective_chat.id
    video_id = user_video.get(chat_id)
    if not video_id:
        await update.message.reply_text(
            "❗ Please send a YouTube URL first so I can fetch comments."
        )
    return video_id


async def cmd_winner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    video_id = await _require_video(update)
    if not video_id:
        return

    class _FakeQuery:
        async def edit_message_text(self, *a, **kw):
            return await update.message.reply_text(*a, **kw)

    await action_winner(_FakeQuery(), video_id, n=1)


async def cmd_multiwinner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    video_id = await _require_video(update)
    if not video_id:
        return
    await update.message.reply_text(
        "🏆 How many winners?",
        reply_markup=_multi_winner_keyboard(video_id),
    )


async def cmd_unique(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    video_id = await _require_video(update)
    if not video_id:
        return

    class _FakeQuery:
        async def edit_message_text(self, *a, **kw):
            return await update.message.reply_text(*a, **kw)
        async def edit_message_reply_markup(self, *a, **kw):
            pass

    await action_unique(_FakeQuery(), video_id)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    video_id = await _require_video(update)
    if not video_id:
        return
    username = " ".join(ctx.args) if ctx.args else ""
    if username:
        results = db.search_comments(video_id, username)
        if not results:
            await update.message.reply_text(f"🔎 No results for '{username}'.")
            return
        lines = [f"🔎 *Results for* `{_escape(username)}`\n"]
        for r in results[:20]:
            lines.append(f"• *{_escape(r['author'])}*: _{_escape(r['text'][:100])}_")
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_main_keyboard(video_id),
        )
    else:
        awaiting_search[update.effective_chat.id] = video_id
        await update.message.reply_text("🔎 Send the username to search for:")


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    video_id = await _require_video(update)
    if not video_id:
        return
    comments = db.get_comments(video_id)
    if not comments:
        await update.message.reply_text("❌ No comments to export.")
        return

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["username", "channel_id", "profile_pic", "comment"])
    for c in comments:
        writer.writerow([c["author"], c.get("author_chan", ""), c.get("profile_pic", ""), c["text"]])
    buf.seek(0)
    file_bytes = buf.getvalue().encode("utf-8-sig")
    await update.effective_chat.send_document(
        document=io.BytesIO(file_bytes),
        filename=f"comments_{video_id}.csv",
        caption=f"📄 {len(comments):,} comments exported",
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    video_id = await _require_video(update)
    if not video_id:
        return

    class _FakeQuery:
        async def edit_message_text(self, *a, **kw):
            return await update.message.reply_text(*a, **kw)

    await action_stats(_FakeQuery(), video_id)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Add it to your environment secrets.")
    if not config.YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is not set. Add it to your environment secrets.")

    # Initialise database
    db.init_db()

    # Clean up old records on startup
    removed = db.cleanup_old_records(config.DB_MAX_AGE_SECONDS)
    if removed:
        logger.info("ADMIN | Cleaned %d old comment rows from DB", removed)

    # Build app
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_help))
    app.add_handler(CommandHandler("winner",       cmd_winner))
    app.add_handler(CommandHandler("multiwinner",  cmd_multiwinner))
    app.add_handler(CommandHandler("unique",       cmd_unique))
    app.add_handler(CommandHandler("search",       cmd_search))
    app.add_handler(CommandHandler("export",       cmd_export))
    app.add_handler(CommandHandler("stats",        cmd_stats))

    # Inline button handler
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Message handler — catches URLs and text replies
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    logger.info("ADMIN | Bot started and polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
