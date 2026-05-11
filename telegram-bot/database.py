"""
database.py — SQLite persistence layer for comments and cache metadata.

Tables:
  comments   — stores fetched comments per video
  cache_meta — tracks when a video was last fetched
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "comments.db"


def get_conn() -> sqlite3.Connection:
    """Return a thread-safe connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist yet, and run migrations."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS comments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id     TEXT    NOT NULL,
                comment_id   TEXT,
                author       TEXT    NOT NULL,
                author_chan   TEXT,
                profile_pic  TEXT,
                text         TEXT    NOT NULL,
                fetched_at   INTEGER NOT NULL
            );


            CREATE INDEX IF NOT EXISTS idx_comments_video
                ON comments(video_id);

            CREATE TABLE IF NOT EXISTS cache_meta (
                video_id     TEXT    PRIMARY KEY,
                fetched_at   INTEGER NOT NULL,
                total_count  INTEGER NOT NULL DEFAULT 0,
                video_title  TEXT
            );
        """)
        # Migration: add comment_id column to existing databases that predate this column
        existing = {row[1] for row in conn.execute("PRAGMA table_info(comments)")}
        if "comment_id" not in existing:
            conn.execute("ALTER TABLE comments ADD COLUMN comment_id TEXT")


def save_comments(video_id: str, comments: list[dict], video_title: str) -> None:
    """
    Persist a list of comment dicts for *video_id*.
    Replaces any previously stored rows for that video.
    """
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("DELETE FROM comments WHERE video_id = ?", (video_id,))
        conn.executemany(
            """
            INSERT INTO comments (video_id, comment_id, author, author_chan, profile_pic, text, fetched_at)
            VALUES (:video_id, :comment_id, :author, :author_chan, :profile_pic, :text, :fetched_at)
            """,
            [
                {
                    "video_id": video_id,
                    "comment_id": c.get("comment_id"),
                    "author": c["author"],
                    "author_chan": c.get("author_chan"),
                    "profile_pic": c.get("profile_pic"),
                    "text": c["text"],
                    "fetched_at": now,
                }
                for c in comments
            ],
        )
        conn.execute(
            """
            INSERT INTO cache_meta (video_id, fetched_at, total_count, video_title)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                fetched_at  = excluded.fetched_at,
                total_count = excluded.total_count,
                video_title = excluded.video_title
            """,
            (video_id, now, len(comments), video_title),
        )


def get_comments(video_id: str) -> list[dict]:
    """Return all stored comments for *video_id* as plain dicts."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT comment_id, author, author_chan, profile_pic, text FROM comments WHERE video_id = ?",
            (video_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_unique_comments(video_id: str) -> list[dict]:
    """One comment per unique author (first comment wins)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT comment_id, author, author_chan, profile_pic, text
            FROM comments
            WHERE video_id = ?
            GROUP BY author
            """,
            (video_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def search_comments(video_id: str, username: str) -> list[dict]:
    """Case-insensitive search for *username* within *video_id*."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT comment_id, author, author_chan, profile_pic, text
            FROM comments
            WHERE video_id = ? AND LOWER(author) LIKE LOWER(?)
            """,
            (video_id, f"%{username}%"),
        ).fetchall()
    return [dict(r) for r in rows]


def filter_by_keyword(video_id: str, keyword: str) -> list[dict]:
    """Return comments containing *keyword* (case-insensitive)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT comment_id, author, author_chan, profile_pic, text
            FROM comments
            WHERE video_id = ? AND LOWER(text) LIKE LOWER(?)
            """,
            (video_id, f"%{keyword}%"),
        ).fetchall()
    return [dict(r) for r in rows]


def get_cache_meta(video_id: str) -> dict | None:
    """Return cache metadata for *video_id*, or None if not cached."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM cache_meta WHERE video_id = ?", (video_id,)
        ).fetchone()
    return dict(row) if row else None


def cleanup_old_records(max_age_seconds: int) -> int:
    """Delete comments older than *max_age_seconds*. Returns row count deleted."""
    cutoff = int(time.time()) - max_age_seconds
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM comments WHERE fetched_at < ?", (cutoff,)
        )
        conn.execute(
            "DELETE FROM cache_meta WHERE fetched_at < ?", (cutoff,)
        )
    return cur.rowcount
