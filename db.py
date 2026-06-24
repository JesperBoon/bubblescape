"""
db.py — SQLite database setup and helpers.

All file I/O goes through this module. The DB lives at data/bubblescape.db.
"""

import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "bubblescape.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # safer for interrupted writes
    return conn


def setup():
    """Create all tables if they don't exist. Safe to call repeatedly."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            username        TEXT PRIMARY KEY,
            display_name    TEXT,
            followers       INTEGER DEFAULT 0,
            following_count INTEGER DEFAULT 0,
            bio             TEXT,
            verified        INTEGER DEFAULT 0,
            video_count     INTEGER DEFAULT 0,
            hashtags        TEXT DEFAULT '[]',  -- JSON list, top 50 from their videos
            collected_at    TEXT,
            fully_collected INTEGER DEFAULT 0,  -- 1 = full profile + videos fetched
            is_dutch        INTEGER DEFAULT 1,  -- 0 = confirmed non-Dutch, excluded from graph
            priority        INTEGER DEFAULT 0   -- 1 = manually seeded (politici, seeds) → always collected first
        );

        -- Directed follow graph: follower → following
        CREATE TABLE IF NOT EXISTS follows (
            follower     TEXT NOT NULL,
            following    TEXT NOT NULL,
            discovered_at TEXT,
            PRIMARY KEY (follower, following)
        );
        CREATE INDEX IF NOT EXISTS idx_follows_following ON follows(following);

        CREATE TABLE IF NOT EXISTS videos (
            video_id    TEXT PRIMARY KEY,
            author      TEXT,
            description TEXT,
            hashtags    TEXT DEFAULT '[]',  -- JSON list
            mentions    TEXT DEFAULT '[]',  -- JSON list of @mentioned usernames parsed from caption
            audio_id    TEXT,
            audio_title TEXT,
            likes       INTEGER DEFAULT 0,
            comments    INTEGER DEFAULT 0,
            views       INTEGER DEFAULT 0,
            create_time TEXT,              -- TikTok publish timestamp (ISO 8601, from API "published" field)
            language    TEXT,              -- language code detected by TikTok (e.g. "nl", "en")
            collected_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_videos_author ON videos(author);

        CREATE TABLE IF NOT EXISTS sessions (
            session_id         TEXT PRIMARY KEY,
            started_at         TEXT,
            finished_at        TEXT,
            credits_used       INTEGER DEFAULT 0,
            accounts_collected INTEGER DEFAULT 0,
            notes              TEXT
        );

        -- Per-video speech transcripts (KonbiniAPI ASR). One row per probed video:
        -- status 'ok' carries the flattened text; 'no_asr'/'error' are kept so a video
        -- is never re-probed (each probe costs a credit). credit_cost is the measured cost.
        CREATE TABLE IF NOT EXISTS transcripts (
            video_id    TEXT PRIMARY KEY,
            lang        TEXT,               -- language reported for the transcript (e.g. nl-NL)
            text        TEXT,               -- VTT flattened to plain text (NULL when none)
            source      TEXT,               -- e.g. "ASR"
            status      TEXT NOT NULL,      -- 'ok' | 'no_asr' | 'error'
            credit_cost INTEGER,            -- credits this call actually cost (from headers)
            fetched_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_transcripts_status ON transcripts(status);
    """)
    conn.commit()
    # Migrations for existing DBs
    for migration in [
        "ALTER TABLE accounts ADD COLUMN fully_collected INTEGER DEFAULT 0",
        "ALTER TABLE accounts ADD COLUMN is_dutch INTEGER DEFAULT 1",
        "ALTER TABLE videos ADD COLUMN create_time TEXT",
        "ALTER TABLE videos ADD COLUMN language TEXT",
        "ALTER TABLE videos ADD COLUMN mentions TEXT DEFAULT '[]'",
        "ALTER TABLE accounts ADD COLUMN priority INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(migration)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    conn.close()
    print(f"Database ready: {DB_PATH}")


# ── Write helpers ─────────────────────────────────────────────────────────────

def upsert_account(data: dict):
    """Insert or update an account. 'username' key is required."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO accounts
            (username, display_name, followers, following_count, bio, verified, video_count, hashtags, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            display_name    = excluded.display_name,
            followers       = excluded.followers,
            following_count = excluded.following_count,
            bio             = excluded.bio,
            verified        = excluded.verified,
            video_count     = excluded.video_count,
            hashtags        = excluded.hashtags,
            collected_at    = excluded.collected_at
    """, (
        data["username"],
        data.get("display_name", ""),
        data.get("followers", 0),
        data.get("following_count", 0),
        data.get("bio", ""),
        int(data.get("verified", False)),
        data.get("video_count", 0),
        json.dumps(data.get("hashtags", [])),
        datetime.utcnow().isoformat(),
    ))
    # Only set is_dutch=1 on INSERT — never overwrite a confirmed non-Dutch flag
    conn.execute(
        "UPDATE accounts SET is_dutch = 1 WHERE username = ? AND is_dutch != 0", (data["username"],)
    )
    conn.commit()
    conn.close()


def add_follow_edge(follower: str, following: str):
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO follows (follower, following, discovered_at)
        VALUES (?, ?, ?)
    """, (follower, following, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def upsert_video(data: dict):
    if not data.get("id"):
        return
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO videos
            (video_id, author, description, hashtags, mentions, audio_id, audio_title,
             likes, comments, views, create_time, language, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["id"],
        data.get("author", ""),
        data.get("description", ""),
        json.dumps(data.get("hashtags", [])),
        json.dumps(data.get("mentions", [])),
        data.get("audio_id", ""),
        data.get("audio_title", ""),
        data.get("likes", 0),
        data.get("comments", 0),
        data.get("views", 0),
        data.get("create_time"),
        data.get("language"),
        datetime.utcnow().isoformat(),
    ))
    conn.commit()
    conn.close()


def upsert_transcript(video_id: str, result: dict):
    """
    Store one transcript probe. `result` is the dict returned by
    KonbiniClient.get_video_transcript(): {status, lang, text, source, credit_cost}.
    INSERT OR REPLACE keeps the table idempotent — a re-probe overwrites in place.
    """
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO transcripts
            (video_id, lang, text, source, status, credit_cost, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        video_id,
        result.get("lang"),
        result.get("text"),
        result.get("source"),
        result["status"],
        result.get("credit_cost"),
        datetime.utcnow().isoformat(),
    ))
    conn.commit()
    conn.close()


def log_session(session_id, started_at, finished_at, credits_used, accounts_collected, notes=""):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO sessions
            (session_id, started_at, finished_at, credits_used, accounts_collected, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (session_id, started_at, finished_at, credits_used, accounts_collected, notes))
    conn.commit()
    conn.close()


# ── Read helpers ──────────────────────────────────────────────────────────────

def account_exists(username: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM accounts WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return row is not None


def rebuild_hashtag_profiles(min_videos: int = 1, top_n: int = 50) -> int:
    """
    Rebuild accounts.hashtags from raw video data for all Dutch collected accounts.

    Uses all hashtags across all stored videos per account (not just what was
    captured at collection time), returning the top_n by frequency.
    This enriches sparse profiles and improves graph connectivity.

    Returns the number of accounts updated.
    """
    conn = get_conn()

    # All Dutch fully-collected accounts
    accounts = conn.execute("""
        SELECT username FROM accounts
        WHERE fully_collected = 1 AND is_dutch = 1
    """).fetchall()

    updated = 0
    for (username,) in accounts:
        rows = conn.execute(
            "SELECT hashtags FROM videos WHERE author = ?", (username,)
        ).fetchall()

        if len(rows) < min_videos:
            continue

        counts: Counter = Counter()
        for r in rows:
            counts.update(json.loads(r[0]))

        if not counts:
            continue

        top_tags = [t for t, _ in counts.most_common(top_n)]
        conn.execute(
            "UPDATE accounts SET hashtags = ? WHERE username = ?",
            (json.dumps(top_tags), username)
        )
        updated += 1

    conn.commit()
    conn.close()
    return updated


def get_frontier_usernames(limit: int = 200) -> list[tuple[str, int]]:
    """
    Accounts discovered via search but not yet fully collected.
    Returns list of (username, followers) tuples, ordered by followers descending.
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT username, followers FROM accounts
        WHERE fully_collected = 0
        ORDER BY followers DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [(r["username"], r["followers"] or 0) for r in rows]


def get_frontier_scored(limit: int = 500) -> list[tuple[str, int]]:
    """
    Frontier accounts sorted by: log10(followers) × hashtag_novelty.

    novelty = fraction of the account's top video hashtags that do NOT appear
    in the top-300 corpus hashtags (built from all fully-collected accounts).
    Accounts with no video data get novelty=0.5 (unknown → neutral).

    This pushes high-reach accounts with fresh hashtag territory to the top,
    and naturally deprioritises low-follower or redundant accounts without
    hard-cutting them.
    """
    import math
    from collections import Counter

    conn = get_conn()

    # Build corpus from fully-collected accounts
    corpus_rows = conn.execute(
        "SELECT hashtags FROM accounts WHERE fully_collected = 1 AND hashtags != '[]'"
    ).fetchall()
    corpus_counts: Counter = Counter()
    for r in corpus_rows:
        corpus_counts.update(json.loads(r["hashtags"]))
    corpus_top = {t for t, _ in corpus_counts.most_common(300)}

    # All frontier accounts
    frontier = conn.execute(
        "SELECT username, followers, priority FROM accounts WHERE fully_collected = 0"
    ).fetchall()

    # Collect hashtag data from already-stored videos (bootstrap search side-effect)
    video_tags: dict[str, list[str]] = {}
    for row in frontier:
        uname = row["username"]
        vtag_rows = conn.execute(
            "SELECT hashtags FROM videos WHERE author = ?", (uname,)
        ).fetchall()
        tag_counts: Counter = Counter()
        for vr in vtag_rows:
            tag_counts.update(json.loads(vr["hashtags"]))
        if tag_counts:
            video_tags[uname] = [t for t, _ in tag_counts.most_common(20)]

    conn.close()

    priority_queue: list[tuple[str, int]] = []
    scored: list[tuple[str, int, float]] = []

    for row in frontier:
        uname = row["username"]
        followers = row["followers"] or 0

        # Priority accounts (manually seeded: politici, seeds) always go first
        if row["priority"]:
            priority_queue.append((uname, followers))
            continue

        tags = video_tags.get(uname, [])

        if tags and corpus_top:
            novelty = sum(1 for t in tags if t not in corpus_top) / len(tags)
        elif followers > 200_000:
            novelty = 0.1  # large account with no Dutch hashtag signal → deprioritize
        else:
            novelty = 0.5  # small unknown account → neutral, worth checking

        # Quadratic log: amplifies follower differences more than plain log
        # followers=0 means unknown (not scraped yet) — assume moderate reach (10k)
        # rather than treating as tiny (100), which would bury manually-seeded accounts
        effective_followers = followers if followers > 0 else 10_000
        reach = math.log10(effective_followers) ** 2
        # With a large corpus (2600+ accounts), reach matters more than novelty —
        # shift weight toward bigger accounts as DB matures
        score = reach * (0.7 + 0.3 * novelty)
        scored.append((uname, followers, score))

    scored.sort(key=lambda x: x[2], reverse=True)
    normal_queue = [(uname, followers) for uname, followers, _ in scored]

    # Priority accounts first, then novelty-scored remainder
    combined = priority_queue + normal_queue
    return combined[:limit]


def get_frontier_with_signal_count() -> int:
    """Count frontier accounts that have real hashtag signal (via videos table).
    Used to decide whether auto-bootstrap is needed."""
    conn = get_conn()
    count = conn.execute("""
        SELECT COUNT(DISTINCT a.username)
        FROM accounts a
        JOIN videos v ON v.author = a.username
        WHERE a.fully_collected = 0
        AND v.hashtags != '[]' AND v.hashtags IS NOT NULL
    """).fetchone()[0]
    conn.close()
    return count


def mark_non_dutch(username: str):
    """Flag account as confirmed non-Dutch. Excluded from graph and future scoring."""
    conn = get_conn()
    conn.execute(
        "UPDATE accounts SET is_dutch = 0, fully_collected = 1 WHERE username = ?",
        (username,)
    )
    conn.commit()
    conn.close()


def mark_account_collected(username: str):
    """Mark an account as fully collected so it won't appear in the frontier again."""
    conn = get_conn()
    conn.execute("UPDATE accounts SET fully_collected = 1 WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def get_all_accounts() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM accounts").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_edges() -> list[tuple]:
    conn = get_conn()
    rows = conn.execute("SELECT follower, following FROM follows").fetchall()
    conn.close()
    return [(r["follower"], r["following"]) for r in rows]


def get_stats() -> dict:
    """Quick summary for logging."""
    conn = get_conn()
    accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    edges = conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0]
    videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    conn.close()
    return {"accounts": accounts, "edges": edges, "videos": videos}
