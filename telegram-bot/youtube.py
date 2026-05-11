"""
youtube.py — YouTube Data API v3 helpers.

Handles:
  - Video ID extraction from various URL formats
  - Video metadata fetching (title, comment count)
  - Full paginated comment fetching with retries
  - Progress callback support so the bot can send live updates
"""

import asyncio
import re
import time
from typing import AsyncIterator, Callable, Awaitable
from urllib.parse import urlparse, parse_qs

import aiohttp

from config import YOUTUBE_API_BASE, YOUTUBE_API_KEY, MAX_RETRIES, RETRY_DELAY_SECONDS


# ── URL parsing ───────────────────────────────────────────────────────────────

def extract_video_id(url: str) -> str | None:
    """
    Extract the YouTube video ID from the most common URL formats:
      - https://www.youtube.com/watch?v=VIDEO_ID
      - https://youtu.be/VIDEO_ID
      - https://youtube.com/shorts/VIDEO_ID
      - https://www.youtube.com/embed/VIDEO_ID
    Returns None if no valid ID is found.
    """
    url = url.strip()
    parsed = urlparse(url)

    # youtu.be short links
    if parsed.netloc in ("youtu.be",):
        vid = parsed.path.lstrip("/").split("?")[0]
        return vid if _valid_id(vid) else None

    # Standard / shorts / embed
    if "youtube.com" in parsed.netloc:
        if parsed.path.startswith(("/shorts/", "/embed/")):
            vid = parsed.path.split("/")[2]
            return vid if _valid_id(vid) else None
        qs = parse_qs(parsed.query)
        vid = qs.get("v", [None])[0]
        return vid if vid and _valid_id(vid) else None

    # Raw 11-char ID fallback
    if _valid_id(url):
        return url

    return None


def _valid_id(vid: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", vid))


# ── API request helper ────────────────────────────────────────────────────────

async def _api_get(session: aiohttp.ClientSession, endpoint: str, params: dict) -> dict:
    """
    Make a single GET request to the YouTube API with automatic retries.
    Raises an appropriate exception on unrecoverable errors.
    """
    params["key"] = YOUTUBE_API_KEY
    url = f"{YOUTUBE_API_BASE}/{endpoint}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()

                if resp.status == 200:
                    return data

                # Decode API errors
                error = data.get("error", {})
                reason = (error.get("errors") or [{}])[0].get("reason", "")
                message = error.get("message", "Unknown API error")

                if resp.status == 403:
                    if "commentsDisabled" in reason or "disabled" in message.lower():
                        raise CommentsDisabledError("Comments are disabled on this video.")
                    if "quotaExceeded" in reason or "quota" in message.lower():
                        raise QuotaExceededError("YouTube API quota exceeded. Try again tomorrow.")
                    raise YouTubeAPIError(f"Access denied: {message}")

                if resp.status == 404:
                    raise VideoNotFoundError("Video not found or it is private.")

                raise YouTubeAPIError(f"HTTP {resp.status}: {message}")

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == MAX_RETRIES:
                raise YouTubeAPIError(f"Network error after {MAX_RETRIES} retries: {exc}") from exc
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)


# ── Video metadata ────────────────────────────────────────────────────────────

async def fetch_video_info(video_id: str) -> dict:
    """
    Return a dict with:
      title, channel_title, comment_count (int), thumbnail_url
    Raises VideoNotFoundError if the video doesn't exist / is private.
    """
    async with aiohttp.ClientSession() as session:
        data = await _api_get(session, "videos", {
            "part": "snippet,statistics",
            "id": video_id,
        })

    items = data.get("items", [])
    if not items:
        raise VideoNotFoundError("Video not found or is private.")

    snippet = items[0]["snippet"]
    stats = items[0].get("statistics", {})

    return {
        "title": snippet.get("title", "Unknown"),
        "channel_title": snippet.get("channelTitle", "Unknown"),
        "comment_count": int(stats.get("commentCount", 0)),
        "thumbnail_url": (
            snippet.get("thumbnails", {}).get("high", {}).get("url")
            or snippet.get("thumbnails", {}).get("default", {}).get("url")
        ),
    }


# ── Comment fetching ──────────────────────────────────────────────────────────

ProgressCallback = Callable[[int], Awaitable[None]]


async def fetch_all_comments(
    video_id: str,
    progress_cb: ProgressCallback | None = None,
    max_comments: int | None = None,
) -> list[dict]:
    """
    Fetch all top-level comments for *video_id* using pagination.

    Each returned dict has:
      author, author_chan, profile_pic, text

    *progress_cb* is called with the running total after each page so
    the bot can post live progress updates.
    """
    comments: list[dict] = []
    page_token: str | None = None

    async with aiohttp.ClientSession() as session:
        while True:
            params: dict = {
                "part": "snippet",
                "videoId": video_id,
                "maxResults": 100,  # max allowed per page
                "textFormat": "plainText",
                "order": "time",
            }
            if page_token:
                params["pageToken"] = page_token

            data = await _api_get(session, "commentThreads", params)

            for item in data.get("items", []):
                top = item["snippet"]["topLevelComment"]["snippet"]
                comments.append({
                    "comment_id": item["snippet"]["topLevelComment"]["id"],
                    "author": top.get("authorDisplayName", "Unknown"),
                    "author_chan": top.get("authorChannelId", {}).get("value"),
                    "profile_pic": top.get("authorProfileImageUrl"),
                    "text": top.get("textDisplay", ""),
                })

            if progress_cb:
                await progress_cb(len(comments))

            page_token = data.get("nextPageToken")
            if not page_token:
                break

            if max_comments and len(comments) >= max_comments:
                break

            # Small delay to be polite to the API
            await asyncio.sleep(0.1)

    return comments


# ── Custom exceptions ─────────────────────────────────────────────────────────

class YouTubeAPIError(Exception):
    """Generic YouTube API error."""

class VideoNotFoundError(YouTubeAPIError):
    """Video doesn't exist or is private."""

class CommentsDisabledError(YouTubeAPIError):
    """Comments have been disabled on the video."""

class QuotaExceededError(YouTubeAPIError):
    """Daily API quota has been exhausted."""
