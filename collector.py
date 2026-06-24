"""
collector.py — KonbiniAPI client for TikTok data.

Handles authentication, pagination, credit tracking, and basic error handling.
All methods yield or return normalized dicts — no raw API shapes leak out.

Key findings from debug run:
  - All responses wrapped in a "data" key → unwrap in _get()
  - Videos use "tag" (not "hashtags"), "content" is a plain string (not dict)
  - Video ID is in "entityId", not "id" (which is a URL)
  - Following lists are often private on TikTok → use search + hashtag co-occurrence instead
  - Tag endpoint (/tiktok/tags/{tag}/videos) returns 404 → use search/videos instead
"""

import logging
import random
import re
import time
from typing import Optional, Generator
import requests
from config import KONBINI_API_KEY, KONBINI_BASE_URL

logger = logging.getLogger(__name__)


class KonbiniClient:
    def __init__(self):
        if not KONBINI_API_KEY:
            raise ValueError(
                "KONBINI_API_KEY is not set. "
                "Copy .env.example to .env and add your key."
            )
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {KONBINI_API_KEY}",
            "Accept": "application/json",
        })
        self.credits_used = 0
        self.credits_remaining = None

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        """
        Single GET request.
        Unwraps the "data" envelope that KonbiniAPI wraps all responses in.
        Returns the inner data dict, or None on failure.
        """
        time.sleep(random.uniform(0.5, 2.0))  # polite pacing — prevents 502 cascade
        url = f"{KONBINI_BASE_URL}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=20)

            used = int(resp.headers.get("X-Credits-Used", 0))
            remaining = resp.headers.get("X-Credits-Remaining")
            self.credits_used += used
            if remaining is not None:
                self.credits_remaining = int(remaining)

            if resp.status_code == 200:
                body = resp.json()
                # All KonbiniAPI responses wrap their payload in {"data": {...}}
                return body.get("data") if "data" in body else body
            elif resp.status_code == 404:
                logger.debug(f"404 Not found: {path}")
                return None
            elif resp.status_code == 401:
                raise ValueError("API key rejected (401). Check your .env file.")
            elif resp.status_code == 402:
                raise RuntimeError("Out of credits (402). Top up your KonbiniAPI account.")
            elif resp.status_code in (502, 503):
                logger.warning(f"Unexpected {resp.status_code} from {path}: {resp.text[:200]}")
                time.sleep(5)  # brief backoff before giving up — server-side instability
                return None
            else:
                logger.warning(f"Unexpected {resp.status_code} from {path}: {resp.text[:200]}")
                return None

        except requests.Timeout:
            logger.warning(f"Timeout: {path}")
            return None
        except requests.RequestException as e:
            logger.error(f"Request error on {path}: {e}")
            return None

    def _paginate(self, path: str, max_pages: int, extra_params: dict = None) -> Generator[list, None, None]:
        """
        Generic paginator. Yields one page (list of items) at a time.
        Uses nextCursor for pagination (KonbiniAPI's actual field name).
        """
        cursor = None
        for _ in range(max_pages):
            params = dict(extra_params or {})
            if cursor:
                params["cursor"] = cursor
            data = self._get(path, params)
            if not data:
                break
            items = data.get("orderedItems", [])
            if not items:
                break
            yield items
            cursor = data.get("nextCursor")
            if not cursor:
                break

    # ── Public methods ────────────────────────────────────────────────────────

    def get_user_profile(self, username: str) -> Optional[dict]:
        """Fetch a single user profile. Returns normalized dict or None."""
        data = self._get(f"/tiktok/users/{username}")
        if not data:
            return None
        return {
            "username": data.get("preferredUsername", username),
            "display_name": data.get("name", ""),
            "followers": int(data.get("followerCount") or 0),
            "following_count": int(data.get("followingCount") or 0),
            "bio": data.get("summary", ""),
            "verified": bool(data.get("isVerified", False)),
            "video_count": int(data.get("mediaCount") or 0),
        }

    def get_user_following(self, username: str, max_pages: int) -> Generator[list, None, None]:
        """
        Yields pages of accounts this user follows.
        Note: TikTok allows users to hide their following list.
        Many accounts will return 0 items — this is expected.
        """
        for page in self._paginate(f"/tiktok/users/{username}/following", max_pages):
            normalized = []
            for item in page:
                uname = item.get("preferredUsername") or _extract_username(item.get("url", "") or item.get("id", ""))
                if uname:
                    normalized.append({
                        "username": uname,
                        "display_name": item.get("name", ""),
                        "followers": int(item.get("followerCount") or 0),
                    })
            if normalized:
                yield normalized

    def get_user_videos(self, username: str, max_pages: int) -> Generator[list, None, None]:
        """
        Yields pages of videos posted by this user.
        Each item is a normalized dict with hashtags and engagement counts.
        """
        for page in self._paginate(f"/tiktok/users/{username}/videos", max_pages):
            yield [_parse_video(item, username) for item in page]

    def search_videos(self, query: str, max_pages: int) -> Generator[list, None, None]:
        """
        Search for videos by keyword/hashtag.
        Used for bootstrap — more reliable than the tag endpoint.
        Each item contains the video + author info.
        """
        for page in self._paginate("/tiktok/search/videos", max_pages, {"query": query}):
            normalized = []
            for item in page:
                author_raw = item.get("attributedTo") or {}
                uname = author_raw.get("preferredUsername") or _extract_username(
                    author_raw.get("url", "") or author_raw.get("id", "")
                )
                normalized.append({
                    "video": _parse_video(item, uname),
                    "author": {
                        "username": uname,
                        "display_name": author_raw.get("name", ""),
                        "followers": int(author_raw.get("followerCount") or 0),
                    }
                })
            yield normalized

    def search_users(self, query: str, max_pages: int = 1) -> list:
        """Search for user accounts by keyword. Returns flat list."""
        results = []
        for page in self._paginate("/tiktok/search/users", max_pages, {"query": query}):
            for item in page:
                uname = item.get("preferredUsername") or _extract_username(item.get("url", "") or item.get("id", ""))
                if uname:
                    results.append({
                        "username": uname,
                        "display_name": item.get("name", ""),
                        "followers": int(item.get("followerCount") or 0),
                    })
        return results

    def health_check(self) -> bool:
        """Quick test call to verify the API key works. Costs 1 credit."""
        logger.info("Running API health check...")
        result = self.get_user_profile("tiktok")
        if result:
            logger.info(f"Health check passed. Credits remaining: {self.credits_remaining}")
            return True
        else:
            logger.error("Health check failed — check your API key.")
            return False

    def get_video_transcript(self, video_id: str, language: str = "original") -> dict:
        """
        Fetch a video's speech transcript (WebVTT) and flatten it to plain text.

        Unlike _get(), this surfaces the status so callers can cache misses: a video
        with no auto-transcript returns 404, which still costs a credit, so we record it
        as 'no_asr' and never re-probe. `language="original"` returns native ASR.

        Returns: {status: 'ok'|'no_asr'|'error', lang, text, source, credit_cost}
        """
        time.sleep(random.uniform(0.5, 2.0))  # polite pacing — same as the scraper
        url = f"{KONBINI_BASE_URL}/tiktok/videos/{video_id}/transcripts/{language}"
        none = {"lang": None, "text": None, "source": None}
        try:
            resp = self.session.get(url, timeout=20)
        except requests.Timeout:
            logger.warning(f"Transcript timeout: {video_id}")
            return {"status": "error", "credit_cost": 0, **none}
        except requests.RequestException as e:
            logger.error(f"Transcript request error on {video_id}: {e}")
            return {"status": "error", "credit_cost": 0, **none}

        cost = int(resp.headers.get("X-Credits-Used", 0) or 0)
        self.credits_used += cost
        remaining = resp.headers.get("X-Credits-Remaining")
        if remaining is not None:
            self.credits_remaining = int(remaining)

        if resp.status_code == 200:
            data = (resp.json() or {}).get("data", {}) or {}
            text = vtt_to_text(data.get("content", "") or "")
            if text:
                return {"status": "ok", "lang": data.get("language"),
                        "text": text, "source": data.get("source"), "credit_cost": cost}
            return {"status": "no_asr", "credit_cost": cost, **none}
        elif resp.status_code == 404:
            return {"status": "no_asr", "credit_cost": cost, **none}
        elif resp.status_code == 401:
            raise ValueError("API key rejected (401). Check your .env file.")
        elif resp.status_code == 402:
            raise RuntimeError("Out of credits (402). Top up your KonbiniAPI account.")
        else:
            logger.warning(f"Unexpected {resp.status_code} on transcript {video_id}: {resp.text[:120]}")
            return {"status": "error", "credit_cost": cost, **none}


# ── Helpers ───────────────────────────────────────────────────────────────────

def vtt_to_text(vtt: str) -> str:
    """Flatten WebVTT to plain text: drop the header, cue numbers and timestamp lines,
    and collapse consecutive repeated subtitle lines."""
    lines = []
    for raw in (vtt or "").splitlines():
        line = raw.strip()
        if not line or line.upper().startswith("WEBVTT") or "-->" in line or line.isdigit():
            continue
        if not lines or lines[-1] != line:
            lines.append(line)
    return " ".join(lines).strip()

def _extract_username(url: str) -> str:
    """Pull username from a TikTok profile URL like https://www.tiktok.com/@username"""
    if not url:
        return ""
    parts = url.rstrip("/").split("/")
    candidate = parts[-1].lstrip("@")
    # Filter out obvious non-usernames
    if candidate and "." not in candidate or len(candidate) > 2:
        return candidate
    return ""


_MENTION_RE = re.compile(r"@([\w.]+)")


def _parse_video(item: dict, author_username: str) -> dict:
    """
    Normalize a raw KonbiniAPI video object to our internal shape.

    Confirmed field names from debug run:
      - Tags: item["tag"] is a list of {"type": "Tag", "name": "fvd", ...}
      - Content: item["content"] is a plain string (not a dict)
      - Video ID: item["entityId"] is the numeric TikTok ID
      - Author: item["attributedTo"] contains the author profile
      - published: ISO 8601 timestamp of when TikTok published the video
      - language: BCP-47 language code detected by TikTok (e.g. "nl", "en")
    """
    # Tags: list of {"type": "Tag", "name": "fvd", "entityId": "...", ...}
    raw_tags = item.get("tag") or []
    hashtags = [t["name"] for t in raw_tags if isinstance(t, dict) and t.get("name")]

    # Content is a plain string
    description = item.get("content", "") or ""

    # @mentions parsed from caption — zero extra API calls
    mentions = list(dict.fromkeys(  # deduplicate, preserve order
        m.lower() for m in _MENTION_RE.findall(description)
    ))

    # Use entityId (numeric) as the video ID, fall back to URL-based id
    video_id = item.get("entityId") or item.get("id", "")

    # Audio (if present)
    audio = item.get("audio") or {}

    return {
        "id": video_id,
        "author": author_username,
        "description": description,
        "hashtags": hashtags,
        "mentions": mentions,
        "audio_id": audio.get("entityId") or audio.get("id", ""),
        "audio_title": audio.get("name", ""),
        "likes": int(item.get("likeCount") or 0),
        "comments": int(item.get("commentCount") or 0),
        "views": int(item.get("viewCount") or 0),
        "create_time": item.get("published"),       # ISO 8601 from TikTok e.g. "2024-03-15T14:11:29.000Z"
        "language": item.get("language"),            # TikTok-detected language code e.g. "nl"
    }
